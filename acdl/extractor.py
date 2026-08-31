"""取流提取：拦截浏览器网络响应，自动获取 m3u8 / 密钥 / IV / sn

设计要点：
    不依赖任何具体 ajax 端点（如 m3u8.php / token.php），因为这些端点随时可能
    变更或下线。改为直接监听浏览器实际发出的请求与响应，只要页面能播放，
    就能捕获到 m3u8、EXT-X-KEY 密钥地址与密钥本体。
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from playwright.sync_api import Page

from . import PLAY_URL, DEFAULT_UA
from .config import Config
from .utils import error, info, ok, warn

M3U8_HINTS = (".m3u8", "mpegurl", "m3u8.php", "/m3u8/", "playlist")
KEY_HINTS = ("key", ".key", "enc", "hlskey", "drm")

# 未登录 / 非 VIP 会先播放贴片广告，需与正片流区分
_AD_URL_HINTS = (
    "/ad/", "/ads/", "/advert", "welcome_to_anigamer",
    "preroll", "ad.m3u8", "/sponsor",
)

# 载入页面后可能弹出的「年龄确认」覆盖层按钮文本
# 注意：不要收录「年齡」「18歲」这类过于宽泛的词，会误命中页面上的静态标签，
# 导致每秒无意义点击（真实按钮文案是「同意」/「確認」）。
_ADULT_CONFIRM_TEXTS = (
    "我是成年人", "我滿18", "已滿18", "滿18歲以上", "滿18歲", "滿18",
    "確認年齡", "年齡確認",
    "進入觀看", "開始觀看", "进入观看", "开始观看",
    "同意", "確認", "確定",
)
# 广告播放完毕后出现的「跳过」按钮文本
# 动画疯实际按钮文案为「點此跳過廣告」（繁体），需完整收录
_SKIP_AD_TEXTS = (
    "點此跳過廣告", "点此跳过广告",
    "跳過廣告", "跳过广告", "略過廣告",
    "跳過", "跳过", "SKIP", "略過", "略过",
    "免費觀看", "免费观看",
)
# 跳过按钮常见 class（命中其一即点击，不依赖文本内容）
_SKIP_AD_CLASSES = (
    "skip", "skipad", "skip-ad", "ad-skip", "vjs-skip", "jump-ad",
    "skip-btn", "ad-skip-btn", "btn-skip", "next-ad", "skipoverlay",
    "skipbutton",
)

# #EXT-X-KEY:METHOD=AES-128,URI="https://...",IV=0x...
_EXT_X_KEY_RE = re.compile(
    r"#EXT-X-KEY:(?P<attrs>[^\n]*)", re.IGNORECASE
)
_ATTR_RE = re.compile(
    r"""(?P<k>[A-Z0-9\-]+)\s*=\s*(?:"(?P<v1>[^"]*)"|(?P<v2>[^,]*))""",
    re.IGNORECASE,
)
_IV_RE = re.compile(r"IV\s*=\s*0x([0-9a-fA-F]{32})", re.IGNORECASE)
_STREAM_INF_RE = re.compile(
    r"#EXT-X-STREAM-INF:(?P<attrs>[^\n]*)\n(?P<uri>[^\s#][^\n]*)", re.IGNORECASE
)
_RESOLUTION_RE = re.compile(r"RESOLUTION\s*=\s*(\d+)\s*[xX]\s*(\d+)")
_BANDWIDTH_RE = re.compile(r"BANDWIDTH\s*=\s*(\d+)")


@dataclass
class Variant:
    """master playlist 中的一档清晰度"""
    url: str
    resolution: str = ""
    bandwidth: int = 0
    height: int = 0


@dataclass
class StreamInfo:
    sn: str = ""
    title: str = ""
    m3u8_url: str = ""
    master_url: str = ""
    key_url: str = ""
    key_hex: str = ""
    iv_hex: str = ""
    method: str = "AES_128"
    variants: list[Variant] = field(default_factory=list)
    playlist_text: str = ""
    resolution: str = ""
    device_id: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.m3u8_url)

    @property
    def encrypted(self) -> bool:
        return bool(self.key_url or self.key_hex)


def _parse_attrs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(raw or ""):
        key = m.group("k").upper()
        val = m.group("v1") if m.group("v1") is not None else (m.group("v2") or "")
        out[key] = val.strip()
    return out


class StreamExtractor:
    """在浏览器页面上运行，捕获取流所需的全部信息"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": cfg.user_agent or DEFAULT_UA,
            "Origin": "https://ani.gamer.com.tw",
            "Referer": PLAY_URL,
            "Accept": "*/*",
            "Accept-Language": "zh-TW,zh-CN;q=0.9,zh;q=0.8",
        })
        if cfg.proxy:
            self._session.proxies.update({"http": cfg.proxy, "https": cfg.proxy})
        elif not cfg.use_system_proxy:
            self._session.trust_env = False

        self._state: Optional[StreamInfo] = None
        self._key_candidates: dict[str, bytes] = {}
        self._seen_playlists: list[tuple[str, str]] = []
        self._ad_playlists: list[str] = []
        self._locked: bool = False  # 选定档位后上锁，屏蔽后续 360p 等拦截覆盖

    # ------------------------------------------------------------ 请求辅助

    def sync_cookies(self, cookies: list[dict]) -> None:
        """把浏览器 Cookie 同步到 requests 会话，用于主动补全密钥"""
        jar = self._session.cookies
        jar.clear()
        for c in cookies or []:
            try:
                jar.set(
                    c.get("name", ""),
                    c.get("value", ""),
                    domain=c.get("domain") or "",
                    path=c.get("path") or "/",
                )
            except Exception:  # noqa: BLE001
                continue

    def _get(self, url: str, referer: str | None = None, timeout: int | None = None) -> bytes:
        headers = {}
        if referer:
            headers["Referer"] = referer
        resp = self._session.get(
            url, headers=headers, timeout=timeout or self.cfg.http_timeout
        )
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------ 拦截

    def attach(self, page: Page, state: StreamInfo) -> None:
        """注册响应监听，结果写入 state"""
        self._state = state
        page.on("response", lambda resp: self._on_response(resp))

    def _on_response(self, response) -> None:
        state = self._state
        if state is None:
            return

        url = response.url or ""
        if not url or url.startswith("data:") or url.startswith("blob:"):
            return

        try:
            headers = response.headers or {}
        except Exception:  # noqa: BLE001
            headers = {}
        ctype = (headers.get("content-type") or "").lower()

        try:
            status = response.status
        except Exception:  # noqa: BLE001
            status = 0
        if status and status >= 400:
            return

        lower = url.lower()

        if any(h in lower for h in (".m3u8", "mpegurl", "m3u8.php")) or "mpegurl" in ctype:
            body = self._safe_body(response)
            if body:
                self._handle_playlist(url, body)

        elif self._looks_like_key(url, ctype):
            body = self._safe_body(response)
            if body:
                self._handle_key(url, body)

    @staticmethod
    def _safe_body(response) -> bytes:
        try:
            return response.body()
        except Exception:  # noqa: BLE001
            return b""

    def _looks_like_key(self, url: str, ctype: str) -> bool:
        if self.is_ad_stream(url):
            return False
        lower = url.lower()
        if self._state and self._state.key_url:
            if lower == self._state.key_url.lower() or lower.split("?")[0] == self._state.key_url.lower().split("?")[0]:
                return True
        if any(h in lower for h in KEY_HINTS):
            return True
        return ctype in ("application/octet-stream", "binary/octet-stream")

    def _handle_key(self, url: str, body: bytes) -> None:
        # AES-128 密钥恒为 16 字节；AES-256 为 32 字节
        if len(body) not in (16, 32):
            return
        self._key_candidates[url] = body
        state = self._state
        if state is None:
            return
        if not state.key_hex:
            state.key_hex = body.hex()
            if not state.key_url:
                state.key_url = url
            ok(f"已捕获密钥 ({len(body)} 字节): {state.key_hex}")
        elif url.lower() == (state.key_url or "").lower() and state.key_hex != body.hex():
            state.key_hex = body.hex()
            info(f"密钥已更新: {state.key_hex}")

    @staticmethod
    def is_ad_stream(url: str) -> bool:
        """判断是否贴片广告流"""
        lower = (url or "").lower()
        return any(h in lower for h in _AD_URL_HINTS)

    def _handle_playlist(self, url: str, body: bytes) -> None:
        state = self._state
        if state is None:
            return
        # 已锁定选定档位后，忽略后续任何媒体流拦截（动画疯播放器可能自动切到
        # 360p 并把 m3u8/key 覆盖成 360p，而 360p 密钥端点取到的密钥解不开分片）。
        if self._locked:
            return
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return
        if "#EXTM3U" not in text:
            return

        self._seen_playlists.append((url, text))

        # 广告流单独记录，不作为正片
        if self.is_ad_stream(url):
            if url not in self._ad_playlists:
                self._ad_playlists.append(url)
                info(f"跳过广告流: {url[:80]}")
            return

        if "#EXT-X-STREAM-INF" in text:
            if not state.master_url:
                state.master_url = url
                ok(f"已捕获 master playlist")
            self._parse_variants(url, text)
            # media playlist 优先级高于 master
            if not state.m3u8_url:
                state.m3u8_url = url
        else:
            state.m3u8_url = url
            state.playlist_text = text
            ok(f"已捕获 media playlist")

        self._parse_key_from_playlist(url, text)

    def _parse_variants(self, base_url: str, text: str) -> None:
        state = self._state
        if state is None:
            return
        variants: list[Variant] = []
        for m in _STREAM_INF_RE.finditer(text):
            attrs = m.group("attrs") or ""
            uri = (m.group("uri") or "").strip()
            if not uri:
                continue
            absolute = urljoin(base_url, uri)
            res_m = _RESOLUTION_RE.search(attrs)
            bw_m = _BANDWIDTH_RE.search(attrs)
            height = int(res_m.group(2)) if res_m else 0
            variants.append(
                Variant(
                    url=absolute,
                    resolution=f"{res_m.group(1)}x{res_m.group(2)}" if res_m else "",
                    bandwidth=int(bw_m.group(1)) if bw_m else 0,
                    height=height,
                )
            )
        # 去重后按清晰度降序
        seen: set[str] = set()
        uniq: list[Variant] = []
        for v in variants:
            if v.url in seen:
                continue
            seen.add(v.url)
            uniq.append(v)
        uniq.sort(key=lambda x: (x.height, x.bandwidth), reverse=True)
        state.variants = uniq
        if uniq:
            info(f"可用清晰度: {', '.join(v.resolution or str(v.bandwidth) for v in uniq[:6])}")

    def _parse_key_from_playlist(self, base_url: str, text: str) -> None:
        state = self._state
        if state is None:
            return
        for m in _EXT_X_KEY_RE.finditer(text):
            attrs = _parse_attrs(m.group("attrs"))
            method = (attrs.get("METHOD") or "").upper()
            if method and method != "NONE":
                state.method = method
            uri = attrs.get("URI", "")
            if uri:
                # 注意：必须跟随最新 playlist 更新，否则 m3u8 与 key 可能来自不同档位
                # （例如播放器自动切到 360p，但 key 仍停留在首次捕获的 720p），
                # 导致下载器用错密钥解密 -> CryptographicException: Padding is invalid。
                state.key_url = urljoin(base_url, uri)
                ok(f"已捕获密钥地址: {state.key_url}")
            iv_raw = attrs.get("IV", "")
            if iv_raw:
                iv_m = _IV_RE.search(f"IV={iv_raw}")
                if iv_m:
                    state.iv_hex = iv_m.group(1)
                    info(f"已找到初始偏移向量: 0x{state.iv_hex}")
        if not state.iv_hex:
            iv_m = _IV_RE.search(text)
            if iv_m:
                state.iv_hex = iv_m.group(1)
                info(f"已找到初始偏移向量: 0x{state.iv_hex}")

    # ------------------------------------------------------------ 主动补全

    def probe_active(self, page: Page, state: StreamInfo) -> None:
        """拦截超时后的兜底：在页面上下文中直接调用站内 ajax 取流

        仍然不硬编码端点，而是从页面已加载的脚本 / 全局变量中寻找可用线索，
        失败则静默返回，交由调用方报错。
        """
        if state.ready:
            return
        try:
            result = page.evaluate(
                """async () => {
                    const out = [];
                    const ends = ['/ajax/m3u8.php', '/ajax/token.php', '/ajax/getdeviceid.php'];
                    const sn = (window.animefun && window.animefun.videoSn)
                        || new URLSearchParams(location.search).get('sn') || '';
                    let device = '';
                    try {
                        const r = await fetch('/ajax/getdeviceid.php', {credentials:'include'});
                        const j = await r.json();
                        device = j.deviceid || '';
                    } catch (e) {}
                    for (const ep of ends) {
                        if (ep === '/ajax/getdeviceid.php') continue;
                        try {
                            const u = ep + '?sn=' + encodeURIComponent(sn)
                                    + '&device=' + encodeURIComponent(device);
                            const r = await fetch(u, {credentials:'include'});
                            out.push({url: u, status: r.status, text: (await r.text()).slice(0, 500)});
                        } catch (e) {
                            out.push({url: ep, error: String(e)});
                        }
                    }
                    return {sn, device, out};
                }"""
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"主动探测失败: {exc}")
            return

        if not isinstance(result, dict):
            return
        if result.get("device"):
            info(f"主动探测取得 device id: {str(result['device'])[:16]}...")
        for item in result.get("out") or []:
            text = (item.get("text") or "").strip()
            if not text or text.lower().startswith("file not found"):
                continue
            # 形如 {"src":"//.../index.m3u8?..."}
            m = re.search(r'"src"\s*:\s*"([^"]+)"', text)
            if m:
                src = m.group(1)
                if src.startswith("//"):
                    src = "https:" + src
                state.m3u8_url = src
                ok(f"主动探测取得 m3u8: {src[:80]}...")
                break

    def fetch_key_bytes(
        self, state: StreamInfo, referer: str, page: Page | None = None
    ) -> None:
        """主动拉取密钥本体（拦截未命中时补全）

        优先在浏览器页面上下文中 fetch：走浏览器网络栈、自带完整 Cookie/Referer，
        可绕开 requests 走系统代理时出现的 502 等问题；失败再退回 requests。
        """
        if not state.key_url or state.key_hex:
            return

        # 拦截阶段已经抓到过，直接复用
        for url, blob in self._key_candidates.items():
            if url.split("?")[0] == state.key_url.split("?")[0]:
                state.key_hex = blob.hex()
                ok(f"复用已拦截的密钥: {state.key_hex}")
                return

        blob = b""
        if page is not None:
            blob = self._fetch_key_via_browser(page, state.key_url)
        if not blob:
            try:
                blob = self._get(state.key_url, referer=referer)
            except Exception as exc:  # noqa: BLE001
                warn(f"密钥下载失败（可能已过期或需要鉴权）: {exc}")
                return

        if len(blob) in (16, 32):
            state.key_hex = blob.hex()
            ok(f"已下载密钥: {state.key_hex}")
        else:
            warn(f"密钥长度异常（{len(blob)} 字节），可能未通过鉴权")

    @staticmethod
    def _fetch_key_via_browser(page: Page, key_url: str) -> bytes:
        """在页面上下文中取密钥；失败返回空字节"""
        try:
            b64 = page.evaluate(
                """async (url) => {
                    try {
                        const r = await fetch(url, {credentials: 'include', cache: 'no-store'});
                        if (!r.ok) return '';
                        const buf = new Uint8Array(await r.arrayBuffer());
                        let bin = '';
                        for (let i = 0; i < buf.length; i++) {
                            bin += String.fromCharCode(buf[i]);
                        }
                        return btoa(bin);
                    } catch (e) {
                        return '';
                    }
                }""",
                key_url,
            )
        except Exception:  # noqa: BLE001
            return b""
        if not b64:
            return b""
        try:
            return base64.b64decode(b64)
        except Exception:  # noqa: BLE001
            return b""

    def ensure_media_playlist(self, state: StreamInfo, referer: str) -> None:
        """若拿到的是 master playlist，则抓取选定档位的 media playlist

        交给下载器的必须是 media playlist —— 它才包含 #EXT-X-KEY。
        master 仅用于列出可选清晰度。
        """
        if not state.m3u8_url:
            return
        if not state.variants:
            return

        # 已经拿到 media playlist 时，仍强制切换到选定档位（默认最佳清晰度），
        # 确保最终交给下载器的 m3u8 与 key/IV 来自同一档位，
        # 避免跨档位密钥不匹配导致解密失败（Padding is invalid）。
        target = self._pick_variant(state.variants)
        if target is None:
            return
        try:
            blob = self._get(target.url, referer=referer)
        except Exception as exc:  # noqa: BLE001
            warn(f"media playlist 获取失败: {exc}")
            return
        try:
            text = blob.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return
        if "#EXTM3U" not in text:
            return

        state.master_url = state.m3u8_url
        state.m3u8_url = target.url
        state.playlist_text = text
        state.resolution = target.resolution or str(target.bandwidth)
        self._parse_key_from_playlist(target.url, text)
        info(f"已选定清晰度 {state.resolution}，切换至对应 media playlist")
        # 上锁：后续任何 360p 等拦截都不再覆盖已选定的档位与密钥
        self._locked = True

    def _pick_variant(self, variants: list[Variant]) -> Variant | None:
        want = (self.cfg.resolution or "").strip()
        if want:
            for v in variants:
                if str(v.height) == want or v.resolution.startswith(want):
                    return v
            warn(f"未找到 {want} 清晰度，改用最佳可用")
        return variants[0] if variants else None

    # ------------------------------------------------------------ 主流程

    def capture(
        self,
        page: Page,
        sn: str,
        wait_seconds: int | None = None,
        cookies: list[dict] | None = None,
    ) -> StreamInfo:
        """打开播放页，三阶段等待正片取流信息出现

        阶段 1  监听启动：挂载响应监听并打开播放页
        阶段 2  判流：出现首个 m3u8 流
                    - 非广告流（VIP / 已看过广告）-> 直接完成
                    - 广告流                      -> 进入阶段 3
        阶段 3  等广告：持续监听，丢弃一切广告 m3u8/key，
                    一旦出现非广告的正片流即锁定完成
        """
        state = StreamInfo(sn=sn)
        self.sync_cookies(cookies or [])
        # 每集重置跨集累积状态，避免上一集的广告流干扰本轮判定
        self._ad_playlists = []
        self._seen_playlists = []
        self._locked = False  # 选定档位后上锁，屏蔽后续 360p 等拦截覆盖

        url = f"{PLAY_URL}?sn={sn}"
        referer = url
        self._session.headers["Referer"] = referer

        self.attach(page, state)
        info(f"打开播放页: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            warn(f"页面导航异常，继续等待取流: {exc}")

        limit = max(5, wait_seconds if wait_seconds is not None else self.cfg.ad_wait)
        deadline = time.time() + limit
        probed = False
        nudged = False
        ad_noticed = False
        title_checked = False

        # 阶段 2/3 主循环
        while time.time() < deadline:
            if state.m3u8_url:
                break  # 已捕获正片 media playlist

            page.wait_for_timeout(1000)

            # 每轮尝试处理年龄确认 / 跳过广告覆盖层
            self._handle_overlays(page)

            # 首次出现广告流：进入阶段 3
            if not ad_noticed and self._ad_playlists:
                ad_noticed = True
                remain = int(deadline - time.time())
                info(f"检测到贴片广告，正在等待广告结束（最长约 {remain}s）...")

            elapsed = deadline - time.time()
            # 过半仍未取到正片，尝试点击播放区触发播放
            if not nudged and elapsed < limit * 0.6:
                nudged = True
                self._nudge_play(page)
            # 剩余 1/3 时启动主动探测（兜底）
            if not probed and elapsed < limit / 3:
                probed = True
                info("拦截未命中，尝试主动探测取流...")
                self.probe_active(page, state)

        if not title_checked:
            try:
                state.title = page.title() or ""
            except Exception:  # noqa: BLE001
                state.title = ""
            title_checked = True

        if not state.m3u8_url:
            if self._ad_playlists:
                warn(
                    f"只捕获到 {len(self._ad_playlists)} 个广告流，未取得正片。\n"
                    f"      可能原因：未登录 / 该集需要会员权限 / 广告等待时间不足。\n"
                    f"      可尝试：先执行 ac-dl.py login，或调大配置中的 ad_wait。"
                )
            else:
                warn("等待超时且未捕获任何 m3u8 流（广告或正片均未观察到）。")
            return state

        # 补全 media playlist 与密钥本体
        self.ensure_media_playlist(state, referer)
        self.fetch_key_bytes(state, referer, page)
        return state

    @staticmethod
    def _nudge_play(page: Page) -> None:
        """尝试触发播放：点击播放器区域"""
        info("尝试触发播放...")
        try:
            page.evaluate(
                """() => {
                    const v = document.querySelector('video');
                    if (v) { v.muted = true; v.play().catch(() => {}); }
                }"""
            )
        except Exception:  # noqa: BLE001
            return
        for selector in (
            ".vjs-big-play-button",
            ".play-btn",
            ".btn-play",
            "#ani_video video",
            ".anime-video video",
        ):
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    return
            except Exception:  # noqa: BLE001
                continue

    # ------------------------------------------------------------ 覆盖层处理

    @staticmethod
    def _click_by_text(
        page: Page, texts: tuple[str, ...], classes: tuple[str, ...] = ()
    ) -> str:
        """在页面中查找文本/值/class 匹配的可点击元素并点击，返回命中的关键字

        分两层策略：
        1) Playwright 文本定位器：不限制元素类型，可命中 div/a/span/iframe 内等自定义按钮；
        2) JS 回退：通过选择器遍历，命中 class/role/onclick 等常规按钮，并跳过 disabled 元素。
        """
        # 1) Playwright 文本定位器（更适合自定义 overlay 按钮）
        for k in texts:
            try:
                loc = page.get_by_text(k)
                if loc.count() == 0:
                    continue
                first = loc.first
                if first.is_visible():
                    first.click(timeout=2500)
                    return k
            except Exception:  # noqa: BLE001
                continue

        # 2) JS 回退（保持原有按钮/class 命中能力）
        try:
            return page.evaluate(
                """(args) => {
                    const texts = args[0], classes = args[1];
                    const sels = ['button', 'input[type=button]', 'input[type=submit]',
                        'a[role=button]', '[role=button]', '.btn', '.button',
                        'div[onclick]', 'span[onclick]', '.dialogify button',
                        '.dialog button', '.BH-box button', '.modal button', 'a'];
                    for (const sel of sels) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            // 跳过不可见 / 禁用元素
                            if (el.offsetParent === null &&
                                el.getClientRects().length === 0) continue;
                            if (el.disabled) continue;
                            const t = (el.textContent || '').trim();
                            const v = (el.value || '').trim();
                            const title = (el.title || '').trim();
                            const cls = (el.className || '').toString().toLowerCase();
                            for (const k of texts) {
                                if (t.includes(k) || v.includes(k) ||
                                    title.includes(k)) {
                                    el.click();
                                    return k;
                                }
                            }
                            for (const c of classes) {
                                if (cls.includes(c)) {
                                    el.click();
                                    return c;
                                }
                            }
                        }
                    }
                    return '';
                }""",
                [list(texts), list(classes)],
            )
        except Exception:  # noqa: BLE001
            return ""

    def _handle_overlays(self, page: Page) -> list[str]:
        """自动处理载入/播放过程中的覆盖层：年龄确认、广告跳过"""
        hit: list[str] = []
        a = self._click_by_text(page, _ADULT_CONFIRM_TEXTS)
        if a:
            hit.append(f"年龄确认[{a}]")
        s = self._click_by_text(page, _SKIP_AD_TEXTS, _SKIP_AD_CLASSES)
        if s:
            hit.append(f"跳过广告[{s}]")
        if hit:
            info("已处理页面覆盖层: " + ", ".join(hit))
        return hit


def sn_from_url(url_or_sn: str) -> str:
    """从播放页 URL 或纯数字 sn 中提取 sn"""
    raw = (url_or_sn or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return raw
    parsed = urlparse(raw)
    qs = parse_qs(parsed.query)
    if "sn" in qs and qs["sn"] and qs["sn"][0].isdigit():
        return qs["sn"][0]
    m = re.search(r"[?&]sn=(\d+)", raw)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{4,8})\b", raw)
    return m.group(1) if m else ""
