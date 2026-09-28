"""浏览器会话：持久化 Profile 与 CDP 接管两种登录态方式"""

from __future__ import annotations

import glob
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from . import BASE_URL, DEFAULT_UA
from .config import Config
from .utils import error, info, ok, step, warn

# 巴哈姆特登录页
LOGIN_URL = "https://user.gamer.com.tw/login.php"

# 规避基础自动化指纹
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-TW', 'zh-CN', 'zh'] });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch (e) {}
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch (e) {}
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch (e) {}
"""

_CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--mute-audio",
]

# 登录态 Cookie 关键字
_LOGIN_COOKIE_HINTS = ("bahaid", "bahaenur", "baharune", "ckbahaid", "bhuid", "baha_token")

# 需排除的干扰 Cookie：广告、统计、追踪（例如 ckBahaAd 是广告 Cookie 而非登录凭证）
_LOGIN_COOKIE_EXCLUDE = ("ad", "ads", "advert", "ga", "gtm", "track", "stat", "utm", "csrf")


def _full_chromium_exe() -> str | None:
    """定位 Playwright 安装的『完整 Chromium』可执行文件。

    Playwright 默认 headless=True 会调用残缺的 chromium_headless_shell，
    其功能不全、易被动画疯反爬识别导致广告不推进。改用完整 chromium 的
    chrome.exe + 无头模式可规避该问题。返回 None 表示未找到。
    """
    # 便携优先：PLAYWRIGHT_BROWSERS_PATH 或 exe 同级的 ms-playwright
    candidates = []
    pb = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if pb and pb != "0":
        candidates.append(pb)
    try:
        # 冻结后 browser.py 位于 _internal/acdl/，用脚本/exe 所在目录的上两级兜底
        candidates.append(str(Path(__file__).resolve().parent.parent / "ms-playwright"))
    except Exception:  # noqa: BLE001
        pass
    candidates.append(os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"))
    for base in candidates:
        if base and os.path.isdir(base):
            matches = sorted(
                glob.glob(os.path.join(base, "chromium-*", "chrome-win*", "chrome.exe")),
                reverse=True,
            )
            if matches:
                return matches[0]
    return None


class BrowserSession:
    """统一管理 Playwright 浏览器生命周期

    mode:
      profile - 使用独立的持久化用户目录，首次需 --login 登录一次
      cdp     - 连接用户手动以 --remote-debugging-port 启动的 Chrome，复用其登录态
    """

    def __init__(
        self,
        cfg: Config,
        mode: str = "profile",
        profile_dir: str | Path = ".auth/chrome-profile",
        headless: bool | None = None,
        cdp_port: int | None = None,
    ) -> None:
        self.cfg = cfg
        self.mode = mode
        self.profile_dir = Path(profile_dir)
        self.headless = cfg.headless if headless is None else headless
        self.cdp_port = cdp_port or cfg.cdp_port

        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> BrowserContext:
        if self._context is not None:
            return self._context

        self._pw = sync_playwright().start()
        if self.mode == "cdp":
            self._context = self._start_cdp()
        else:
            self._context = self._start_profile()

        self._context.set_default_timeout(self.cfg.nav_timeout * 1000)
        self._context.set_default_navigation_timeout(self.cfg.nav_timeout * 1000)
        return self._context

    def _start_profile(self) -> BrowserContext:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        info(f"启动持久化浏览器 Profile: {self.profile_dir}")

        launch_kwargs = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            user_agent=self.cfg.user_agent or DEFAULT_UA,
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            viewport={"width": 1440, "height": 900},
            args=_CHROME_ARGS,
            ignore_https_errors=True,
        )
        # 始终显式锁定「完整 Chromium」，而不是让 Playwright 按自身版本去解析内核：
        # 1) 规避残缺的 headless_shell（功能不全，易被动画疯反爬、广告不推进）
        # 2) 规避「已装 playwright 版本」与「本地已下载 chromium 版本」不匹配。
        #    典型报错：Executable doesn't exist at ...\chromium-1243\chrome-win64\chrome.exe
        #    —— 例如本机 playwright 1.63 要求 1243，但 ms-playwright 下只有 1234。
        #    注意：有头模式同样要走这里，否则会退化成按默认解析而踩到上述版本不匹配。
        full = _full_chromium_exe()
        if full:
            launch_kwargs["executable_path"] = full
            info(f"使用完整 Chromium: {full}")
        else:
            warn(
                "未找到完整 Chromium（请在 ms-playwright 下放 chromium-*）。"
                "将使用 Playwright 默认内核；若报 'Executable doesn't exist' "
                "请执行 playwright install chromium"
            )

        return self._pw.chromium.launch_persistent_context(**launch_kwargs)

    def _start_cdp(self) -> BrowserContext:
        endpoint = f"http://127.0.0.1:{self.cdp_port}"
        info(f"尝试通过 CDP 连接已运行的 Chrome: {endpoint}")
        try:
            self._browser = self._pw.chromium.connect_over_cdp(endpoint, timeout=8000)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"无法通过 CDP 连接到 {endpoint}。\n"
                f"请先用以下方式启动 Chrome 并登录动画疯：\n"
                f'  chrome.exe --remote-debugging-port={self.cdp_port} '
                f'--user-data-dir="%LOCALAPPDATA%\\ac-dl-cdp"\n'
                f"原始错误: {exc}"
            ) from exc

        if not self._browser.contexts:
            raise RuntimeError("CDP 已连接，但未找到可用的浏览器上下文（无打开的窗口）。")
        ok("已接管现有 Chrome 会话")
        return self._browser.contexts[0]

    def stop(self) -> None:
        try:
            if self._context is not None and self.mode != "cdp":
                self._context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._context = None
        self._browser = None
        self._pw = None

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    # ---------------------------------------------------------------- 页面

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        return self._context

    def new_page(self) -> Page:
        page = self.context.new_page()
        page.add_init_script(_STEALTH_JS)
        return page

    def open(self, url: str, wait_until: str = "domcontentloaded") -> Page:
        page = self.new_page()
        page.goto(url, wait_until=wait_until)
        return page

    # ---------------------------------------------------------------- 登录态

    def check_login(self, page: Page | None = None) -> tuple[bool, str]:
        """综合判断登录态，返回 (是否已登录, 判定依据)"""
        owns_page = page is None
        page = page or self.new_page()
        try:
            try:
                page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1800)
            except Exception as exc:  # noqa: BLE001
                return False, f"无法打开动画疯首页: {exc}"

            # 信号 1：Cookie
            cookie_hit = self._login_cookie_names()
            if cookie_hit:
                return True, f"检测到登录 Cookie: {', '.join(cookie_hit[:3])}"

            # 信号 2：DOM 中的用户信息（未登录时 nunjucks 模板未渲染）
            dom_state = page.evaluate(
                """() => {
                    const GUEST_NAME = '未知的勇者';
                    const userEl = document.querySelector('.user-info');
                    const href = userEl ? (userEl.getAttribute('href') || '') : '';
                    const nameEl = document.querySelector('.user-name');
                    const name = nameEl ? (nameEl.textContent || '').trim() : '';
                    // 未登录：指向登录页 或 模板占位符未替换
                    if (!href || href.includes('login.php') || href.includes('${BAHAID}')) {
                        return {state: 'guest', name: name};
                    }
                    // 已登录：href 是真实用户主页（home.gamer.com.tw/<ID>）
                    if (href.includes('/home.gamer.com.tw/') && !href.includes('${')) {
                        return {state: 'logged', name: name || href};
                    }
                    // 已登录但不可用 href 判断时，用昵称兜底
                    if (name && name !== GUEST_NAME) return {state: 'logged', name: name};
                    return {state: 'unknown', name: name};
                }"""
            )
            if isinstance(dom_state, dict) and dom_state.get("state") == "logged":
                return True, f"页面显示用户: {dom_state.get('name', '')[:40]}"

            # 信号 3：页面是否仍提示登入
            body_text = page.evaluate("() => document.body.innerText || ''")
            if "登入" in body_text and "登出" not in body_text:
                return False, "页面仍显示「登入」，未检测到登录态"

            return False, "未检测到明确的登录标识"
        finally:
            if owns_page:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def _login_cookie_names(self) -> list[str]:
        try:
            cookies = self.context.cookies()
        except Exception:  # noqa: BLE001
            return []
        hits = []
        for c in cookies:
            name = (c.get("name") or "").lower()
            domain = (c.get("domain") or "").lower()
            if "gamer.com.tw" not in domain:
                continue
            if not any(hint in name for hint in _LOGIN_COOKIE_HINTS):
                continue
            # ckBahaAd 这类广告 Cookie 也含 "baha"，需排除
            if any(bad in name for bad in _LOGIN_COOKIE_EXCLUDE):
                continue
            value = c.get("value") or ""
            if value and value.lower() not in ("0", "null", "undefined", "guest"):
                hits.append(c["name"])
        return hits

    def interactive_login(self, timeout: int = 600) -> bool:
        """打开登录页，等待用户手动完成登录后自动继续。

        重要：轮询期间**不能**对登录页做任何导航。
        check_login(page) 会 goto 动画疯首页，若每隔几秒调用一次，用户正在
        填写的登录页会被反复刷掉，表现为「页面不停刷新、必须极快才能登录」。
        因此这里改为只读 Cookie 判断，登录页在用户操作期间保持不动。
        """
        page = self.new_page()
        step("请在打开的浏览器窗口中登录巴哈姆特动画疯")
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:  # noqa: BLE001
            warn(f"打开登录页失败: {exc}")

        # 持久化 Profile 里已有登录态则直接放行，不再打扰
        hits = self._login_cookie_names()
        if hits:
            ok(f"已检测到登录态（Cookie: {', '.join(hits[:3])}），无需重复登录")
            return True

        print("登录页不会自动刷新，可从容填写；登录完成后程序会自动继续（Ctrl+C 可取消）...")
        deadline = time.time() + timeout
        last_dot = 0
        while time.time() < deadline:
            # 只读 Cookie，绝不导航登录页
            if self._login_cookie_names():
                # 命中后再做一次完整校验（另开临时页并自动关闭，不影响登录页）
                logged, reason = self.check_login()
                if logged:
                    ok(f"登录成功（{reason}）")
                    return True
            now = int(time.time() - (deadline - timeout))
            if now != last_dot:
                last_dot = now
                print(f"\r  已等待 {now}s ...", end="", flush=True)
            time.sleep(2)

        print()
        error(f"等待登录超时（{timeout}s），未检测到登录态。")
        return False

    def ensure_login(self, allow_interactive: bool = True) -> bool:
        """确保处于登录态；返回 False 表示未登录（调用方决定是否继续）"""
        logged, reason = self.check_login()
        if logged:
            ok(f"已登录：{reason}")
            return True

        warn(f"未检测到登录态：{reason}")
        if not allow_interactive:
            return False
        if self.mode == "cdp":
            error("CDP 模式下请在被接管的 Chrome 中完成登录后重试。")
            return False

        print("\n是否现在打开浏览器登录？（y=打开浏览器登录 / n=取消本次下载并返回主菜单）")
        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):  # 用户取消了输入
            print()
            return False
        if choice not in ("y", "yes", ""):
            return False
        return self.interactive_login()

    def reset_profile(self) -> None:
        """清除持久化 Profile（用于重新登录 / 退出登录态）"""
        if self._context is not None and self.mode != "cdp":
            try:
                self._context.close()
            except Exception:  # noqa: BLE001
                pass
            self._context = None
        if self.profile_dir.exists():
            shutil.rmtree(self.profile_dir, ignore_errors=True)
            info(f"已清除 Profile: {self.profile_dir}")
