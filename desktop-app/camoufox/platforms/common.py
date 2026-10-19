#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BossClaw 隐身引擎 —— 平台公共基座（浏览器 / 人类化 / Cookie 按平台持久化）
====================================================================
从 camoufox_server.py 抽取的共享能力，供 BOSS 与 猎聘/智联/51Job 平台模块复用。
仅使用 Camoufox 原生隐身内核（AGENTS.md：本地 Chrome/Edge 不可复用）。

  - open_browser   : 按检测内核打开浏览器（Camoufox 原生内核优先）
  - human_delay/sleep/type_greeting_human : 人类化抖动（只做主动降频，绝不绕过）
  - load/save_cookies(platform) : 登录态按平台独立持久化 ~/.bossclaw/camoufox-cookies-{platform}.json
  - goto_stable    : 反爬空壳页自动重试导航
"""
import json
import os
import random
import re
import shutil
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# ============================================================
# 路径与配置
# ============================================================
DATA_DIR = Path.home() / '.bossclaw'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 平台：boss / liepin / zhaopin / job51（BOSS 为默认兼容路径）
PLATFORMS = ('boss', 'liepin', 'zhaopin', 'job51')


def cookie_file(platform: str = 'boss') -> Path:
    """各平台独立 Cookie 文件：~/.bossclaw/camoufox-cookies-{platform}.json（boss 保持旧路径兼容）。"""
    p = str(platform or 'boss').strip().lower()
    if p == 'boss':
        return DATA_DIR / 'camoufox-cookies.json'
    return DATA_DIR / f'camoufox-cookies-{p}.json'


# 内核检测结果缓存（进程生命周期内只检测一次）
_KERNEL_CACHE: dict | None = None


def log(icon: str, msg: str):
    """输出带时间戳的日志到 stderr。

    Windows 中文环境下 Electron 用管道接 stderr 时，Python 会退回 GBK (cp936)，
    而调用方传入的 icon 常是 emoji 或符号，这类码位在 GBK 中不存在，
    直接 print 会抛 UnicodeEncodeError并中断调用方。
    故用 errors='replace' 兜底：日志图标最多显示为 ?，绝不影响主流程。

    注意：本函数的 docstring 刻意只用 ASCII + 中文，未放任何 emoji 或符号，
    因为某些构建工具在 GBK 环境下读取源码行时会把它们写进错误信息。
    """
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {icon} {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        # 编码不支持时退化为安全输出，绝不因日志而抛错
        enc = getattr(sys.stderr, 'encoding', None) or 'ascii'
        print(line.encode(enc, errors='replace').decode(enc, errors='replace'),
              file=sys.stderr, flush=True)


# ============================================================
# 人类化抖动（对齐 AGENTS.md「只做主动降频、加入人类化抖动，绝不绕过」）
# ============================================================
def human_delay(seconds: float, jitter_ratio: float = 0.35, min_seconds: float = 0.0) -> float:
    """在 base 秒基础上叠加 ±ratio 的随机游走，返回实际需等待的秒数。"""
    base = max(0.0, seconds)
    width = base * jitter_ratio
    return max(min_seconds, base - width + random.random() * width * 2)


def human_sleep(seconds: float, jitter_ratio: float = 0.35, min_seconds: float = 0.0, rng=None):
    """人类化随机关心（对 status 透传时也可传 rng）。"""
    return time.sleep(human_delay(seconds, jitter_ratio, min_seconds))


def type_greeting_human(page, text: str, os_name: str | None = None):
    """真实键盘逐字输入（带随机打字节奏；中文略慢）。os_name 仅保留签名兼容。"""
    for ch in text:
        delay = random.randint(20, 120)
        if ord(ch) > 127:
            delay = random.randint(40, 140)
        page.keyboard.type(ch, delay=delay)
    return True


# ============================================================
# Cookie 管理（按平台独立持久化）
# ============================================================
def load_cookies(platform: str = 'boss') -> list:
    f = cookie_file(platform)
    if not f.exists():
        return []
    try:
        with open(f, encoding='utf-8') as fh:
            auth = json.load(fh)
    except Exception as e:
        log('⚠️', f'读取 Cookie 失败（{platform}）：{e}')
        return []
    pw_cookies = []
    for c in auth.get('cookies', []):
        # H-9：字段缺失不再抛 KeyError 穿透整链路（search_jobs/deliver/do_login → HTTP 500）；
        # 缺关键字段的条目直接跳过（降级为空/部分 Cookie 继续，而非整条链路硬失败）。
        if not isinstance(c, dict):
            continue
        name, value, domain = c.get("name"), c.get("value"), c.get("domain")
        if not name or not value or not domain:
            log('⚠️', '跳过字段缺失的 Cookie（%s）：name=%r domain=%r' % (platform, name, domain))
            continue
        cookie = {
            "name": name, "value": value,
            "domain": domain, "path": c.get("path", "/"),
        }
        expires = c.get("expires", -1)
        if expires and expires > 0:
            cookie["expires"] = expires
        if c.get("httpOnly"):
            cookie["httpOnly"] = True
        if c.get("secure"):
            cookie["secure"] = True
        pw_cookies.append(cookie)
    return pw_cookies


def save_cookies(context, platform: str = 'boss') -> int:
    """导出会话 Cookie 回持久化文件（带备份）。"""
    try:
        all_cookies = context.cookies()
    except Exception as e:
        log('⚠️', f'导出 Cookie 失败（{platform}）：{e}')
        return 0
    export = []
    for c in all_cookies:
        export.append({
            "name": c["name"], "value": c["value"], "domain": c["domain"],
            "path": c.get("path", "/"), "expires": c.get("expires", -1),
            "size": len(c.get("value", "")), "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False), "session": c.get("expires", -1) == -1,
        })
    f = cookie_file(platform)
    if f.exists():
        try:
            backup = f.parent / (f.stem + '.backup.json')
            shutil.copy(f, backup)
        except Exception:
            pass
    with open(f, 'w', encoding='utf-8') as fh:
        json.dump({"cookies": export, "origins": []}, fh, ensure_ascii=False, indent=2)
    log('💾', f'Cookie 已持久化（{platform}）：{len(export)} 条 → {f}')
    return len(export)


def clear_cookies(platform: str = 'boss') -> bool:
    f = cookie_file(platform)
    if f.exists():
        try:
            f.unlink()
            log('🗑️', f'已清除 {platform} Cookie')
            return True
        except Exception:
            return False
    return False


# ============================================================
# 内核检测与浏览器启动（仅 Camoufox 原生隐身内核）
# ============================================================
def detect_kernel(force: bool = False) -> dict:
    """检测可用的隐身内核。仅 Camoufox 原生内核可用；系统浏览器不参与回退。"""
    global _KERNEL_CACHE
    if _KERNEL_CACHE and not force:
        return _KERNEL_CACHE
    try:
        import warnings
        import camoufox
        from camoufox.utils import installed_verstr
        # warnings 压制：pin 失配（装了 beta.39 而 pip 包钉死 beta.36）会打
        # RuntimeWarning 到 stderr，桥进程日志里看着像报错，实际能正常启动。
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if installed_verstr():
                _KERNEL_CACHE = {
                    "kind": "camoufox", "path": None, "camoufox": True,
                    "message": "Camoufox 隐身引擎内核（C++ 级指纹伪装）",
                }
                return _KERNEL_CACHE
    except Exception:
        pass
    _KERNEL_CACHE = {
        "kind": "none", "path": None, "camoufox": False,
        "message": "隐身引擎未就绪：Camoufox 内核文件缺失。请运行离线安装（不要用 camoufox fetch，"
                   "它会先清空目录再联网下载、网络失败会留下空壳）："
                   "python install_offline.py",
    }
    return _KERNEL_CACHE


@contextmanager
def open_browser(os_name: str | None = None, headless: bool = False):
    """按检测到的内核打开浏览器，yield page；退出时自动关闭。仅 Camoufox 原生内核可用。

    内核缺失时显式抛错（fail-safe）：不静默回退到 Playwright 自带 Chromium ——
    BOSS 反爬对该内核返回空壳页，静默回退只会把「内核缺失」伪装成「被反爬拦截」误导排查。
    （P6-05：原 15 个系统浏览器候选路径 + stealth_init_script 为死代码，已删除。）
    """
    kernel = detect_kernel()
    if kernel["kind"] != "camoufox":
        raise RuntimeError(kernel.get("message") or "隐身引擎未就绪：请先安装 Camoufox 内核")
    # 🔴 绕过 Camoufox() 包装（2026-10-15 实测定案）：
    # Camoufox() → NewBrowser() → launch_options() 在 fingerprint=None 时也会
    # 调 generate_fingerprint()，把指纹塞进 CAMOU_CONFIG_* env 传给内核。
    # 本机（双显卡/远程会话）上「指纹 env + FF156 软件渲染」组合会让 GPU/内容
    # 进程雪崩 → juggler「gBrowser never populated」→ new_page 永远挂死。
    # 而「无 CAMOU_CONFIG env + GPU 禁用 prefs + 禁内容沙箱 + 单内容进程」
    # 全链路正常（实测 diag_headful.py：launched 11.1s → page ok → 智联 186 万字符）。
    # 故直接 p.firefox.launch 内核本体，不注入指纹 env；camoufox.exe 自带
    # C++ 级反检测 patch，隐身能力保留大头（TLS/HTTP2 指纹仍在）。
    from playwright.sync_api import sync_playwright
    from camoufox.pkgman import camoufox_path
    from pathlib import Path as _P
    import warnings as _w

    # GPU 兼容配方（缺一不可，2026-10-15 有头实测）：
    #   GPU 沙箱禁用  → GPU 进程不再 3 连败
    #   内容沙箱禁用  → 有头模式内容进程不崩（无头可省，有头必须）
    #   软件 WebRender + 禁硬件加速 → D3D11 无设备时走软件合成
    #   单内容进程    → dom.ipc.processCount=1 避免 Fission 多进程雪崩
    os.environ.setdefault('MOZ_DISABLE_GPU_SANDBOX', '1')
    os.environ.setdefault('MOZ_DISABLE_RDD_SANDBOX', '1')
    os.environ.setdefault('MOZ_DISABLE_CONTENT_SANDBOX', '1')
    gpu_prefs = {
        "gfx.webrender.all": False,
        "gfx.webrender.software": True,
        "layers.acceleration.disabled": True,
        "dom.ipc.processCount": 1,
    }

    with _w.catch_warnings():
        _w.simplefilter("ignore")  # 压制 pin 失配 RuntimeWarning
        # camoufox_path() 返回内核目录；exe 在目录下的 camoufox.exe
        exe = str(_P(camoufox_path(download_if_missing=False)) / "camoufox.exe")

    cm = sync_playwright()
    p = cm.__enter__()
    try:
        browser = p.firefox.launch(
            headless=bool(headless),
            executable_path=exe,
            firefox_user_prefs=gpu_prefs,
        )
        page = browser.new_context().new_page()
        yield page
    finally:
        try:
            cm.__exit__(None, None, None)  # playwright 退出会顺带关闭浏览器
        except Exception:
            pass
    return


def goto_stable(page, url, *, max_tries=4, min_content=500, wait=3):
    """导航并使页面稳定。反爬会对自动化浏览器间歇性返回空壳页，自动重试。"""
    for attempt in range(max_tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log('⚠️', f'导航第 {attempt + 1} 次失败：{str(e)[:60]}，重试…')
            time.sleep(2)
            continue
        time.sleep(wait)
        cur = (page.url or '').strip().lower()
        if cur.startswith(('about:', 'data:')) or not cur.startswith('http'):
            log('⚠️', f'第 {attempt + 1} 次命中空白页（{cur[:40]}），重试…')
            time.sleep(2)
            continue
        try:
            blen = len(page.content() or '')
        except Exception as e:
            # M-16：取页面内容失败**不能**当作「内容充足」直接返回成功（原实现 blen=float('inf')），
            # 多为页面仍在导航或被反爬中断 → 按未稳定处理并重试。
            log('⚠️', f'第 {attempt + 1} 次读取页面内容失败：{str(e)[:60]}，重试…')
            time.sleep(2)
            continue
        if blen >= min_content:
            return True
        log('⚠️', f'第 {attempt + 1} 次命中反爬空壳（len={blen}），重试…')
        time.sleep(2)
    return False


# ============================================================
# 风控文本 / 外部网申检测（跨平台复用；各平台按需调用）
# ============================================================
# 「访问验证 / 请按住滑块」是 Aliyun WAF 滑块壳页的正文文案（51Job 实测），
# 单靠空壳页长度判定不够——必须能按文案收口，否则会静默空采而不是停止交人工。
RISK_TEXT_RE = re.compile(
    r'安全验证|访问验证|请按住滑块|访问过于频繁|请完成验证|验证码|异常请求|账号异常|操作过于频繁|请稍后再试|'
    r'登录已过期|请重新登录|当前环境异常|系统检测到异常'
)

# 结构化 WAF 标记（Aliyun WAF 滑块壳页正文可能为空，文本词表会漏检；
# 与 get_jobs(Job51.java)::checkAccessVerification 的判定标记同源）
WAF_DOM_PROBE_JS = (
    "() => { try { return !!(document.querySelector('.waf-nc-title')"
    " || document.querySelector('script[name^=\"aliyunwaf_\"]')); } catch (e) { return false; } }"
)

# 投递类平台（智联/51Job）每日上限提示词
DAILY_LIMIT_RE = re.compile(r'今日投递|已达上限|投递上限|投递次数|每日.{0,4}投递|今日.{0,6}限制')


def risk_text_hit(page) -> str:
    """风控检测：页面正文文案 + Aliyun WAF 结构标记，命中返回命中词（无命中返回空串）。"""
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText.slice(0, 4000) : '')") or ''
        m = RISK_TEXT_RE.search(text)
        if m:
            return m.group(0)
    except Exception:
        pass
    try:
        if page.evaluate(WAF_DOM_PROBE_JS):
            return 'aliyun-waf'
    except Exception:
        pass
    return ''
