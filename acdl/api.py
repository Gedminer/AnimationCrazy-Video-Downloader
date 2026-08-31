"""动画疯官方 API 客户端

端点来自页面脚本 anime_player.js 中的实际调用（2026-08 验证有效）：
    getDeviceId : GET  https://ani.gamer.com.tw/ajax/getdeviceid.php
    getVideo    : GET  https://api.gamer.com.tw/anime/v1/video.php?videoSn=|?animeSn=
    videoSrc    : GET  https://api.gamer.com.tw/anime/v1/video_src.php?...

注意：
    videoSrc 直接裸调会返回 code 1007「裝置驗證異常」，取流仍需在真实浏览器
    环境中进行（见 extractor.py）。本模块只负责元数据与剧集列表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from . import DEFAULT_UA, PLAY_URL
from .config import Config
from .utils import warn

API_BASE = "https://api.gamer.com.tw"
VIDEO_API = API_BASE + "/anime/v1/video.php"
DEVICE_API = "https://ani.gamer.com.tw/ajax/getdeviceid.php"

# 标题形如 "GRAND BLUE 碧藍之海 3 [8]"
_TITLE_EP_RE = re.compile(r"^(?P<name>.*?)\s*\[(?P<ep>\d+)\]\s*$")
_DEVICEID_RE = re.compile(r"[0-9a-f]{32,}")


@dataclass
class Episode:
    episode: int
    video_sn: int
    state: int = 0
    cover: str = ""
    title: str = ""

    @property
    def play_url(self) -> str:
        return f"{PLAY_URL}?sn={self.video_sn}"


@dataclass
class AnimeInfo:
    anime_sn: int = 0
    raw_title: str = ""
    total_episode: int = 0
    groups: dict[str, list[Episode]] = field(default_factory=dict)
    current_video_sn: int = 0

    # ---------------------------------------------------------- 名称处理

    @property
    def bangumi_name(self) -> str:
        """作品名（去掉末尾的 [集数]）"""
        m = _TITLE_EP_RE.match(self.raw_title or "")
        return (m.group("name") if m else (self.raw_title or "")).strip()

    @property
    def all_episodes(self) -> list[Episode]:
        """所有分组的剧集，按分组键与集号排序"""
        eps: list[Episode] = []
        for key in sorted(self.groups.keys(), key=lambda k: (len(k), k)):
            eps.extend(sorted(self.groups[key], key=lambda e: e.episode))
        return eps

    @property
    def multi_group(self) -> bool:
        return len(self.groups) > 1

    def label(self, ep: Episode) -> str:
        """带分组前缀的显示名，多分组时用于消歧"""
        if not self.multi_group:
            return str(ep.episode)
        for key, items in self.groups.items():
            if any(e.video_sn == ep.video_sn for e in items):
                return f"{key}-{ep.episode}"
        return str(ep.episode)


class AniClient:
    """轻量元数据客户端（剧集列表、单集信息、设备 ID）"""

    def __init__(self, cfg: Config, cookies: Optional[list[dict]] = None) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.user_agent or DEFAULT_UA,
            "Origin": "https://ani.gamer.com.tw",
            "Referer": PLAY_URL,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh-CN;q=0.9,zh;q=0.8",
        })
        if cfg.proxy:
            self.session.proxies.update({"http": cfg.proxy, "https": cfg.proxy})
        elif not cfg.use_system_proxy:
            self.session.trust_env = False
        if cookies:
            self.set_cookies(cookies)

    def set_cookies(self, cookies: list[dict]) -> None:
        jar = self.session.cookies
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

    def import_cookie_header(self, raw: str) -> None:
        """导入浏览器中复制的 Cookie 请求头字符串"""
        for part in (raw or "").split(";"):
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            name, value = name.strip(), value.strip()
            if not name:
                continue
            try:
                self.session.cookies.set(name, value, domain="gamer.com.tw", path="/")
            except Exception:  # noqa: BLE001
                continue

    # ---------------------------------------------------------- 基础请求

    def _get_json(self, url: str, referer: str | None = None) -> dict[str, Any]:
        headers = {"Referer": referer} if referer else None
        resp = self.session.get(url, headers=headers, timeout=self.cfg.http_timeout)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as exc:
            raise RuntimeError(f"接口返回非 JSON 内容: {url}") from exc

    def get_device_id(self) -> str:
        """仅供探测用；真实取流请使用浏览器内的 deviceid"""
        try:
            data = self._get_json(DEVICE_API, referer=PLAY_URL)
        except Exception as exc:  # noqa: BLE001
            warn(f"获取 device id 失败: {exc}")
            return ""
        return str(data.get("deviceid") or "")

    # ---------------------------------------------------------- 业务接口

    def get_video(self, video_sn: int | None = None, anime_sn: int | None = None) -> dict:
        """调用 /anime/v1/video.php，videoSn 与 animeSn 至少提供一个"""
        if not video_sn and not anime_sn:
            raise ValueError("video_sn 与 anime_sn 至少需要提供其中一个")
        params = []
        if video_sn:
            params.append(f"videoSn={int(video_sn)}")
        if anime_sn:
            params.append(f"animeSn={int(anime_sn)}")
        url = f"{VIDEO_API}?{'&'.join(params)}"
        data = self._get_json(url, referer=f"{PLAY_URL}?sn={video_sn or anime_sn}")
        if "error" in data:
            err = data["error"] or {}
            raise RuntimeError(
                f"接口返回错误 [{err.get('code')}]: {err.get('message')}"
            )
        return data.get("data") or {}

    def fetch_anime(self, video_sn: int | None = None, anime_sn: int | None = None) -> AnimeInfo:
        """获取作品信息及完整剧集列表"""
        data = self.get_video(video_sn=video_sn, anime_sn=anime_sn)
        anime = data.get("anime") or {}
        video = data.get("video") or {}

        info = AnimeInfo(
            anime_sn=int(anime.get("animeSn") or 0),
            raw_title=str(anime.get("title") or video.get("title") or ""),
            total_episode=int(anime.get("totalEpisode") or 0),
            current_video_sn=int(video.get("videoSn") or 0),
        )

        raw_groups = anime.get("episodes") or {}
        if isinstance(raw_groups, dict):
            for key, items in raw_groups.items():
                if not isinstance(items, list):
                    continue
                bucket: list[Episode] = []
                for it in items:
                    try:
                        bucket.append(
                            Episode(
                                episode=int(it.get("episode") or 0),
                                video_sn=int(it.get("videoSn") or 0),
                                state=int(it.get("state") or 0),
                                cover=str(it.get("cover") or ""),
                            )
                        )
                    except (TypeError, ValueError):
                        continue
                if bucket:
                    info.groups[str(key)] = bucket
        elif isinstance(raw_groups, list):
            bucket = []
            for it in raw_groups:
                try:
                    bucket.append(
                        Episode(
                            episode=int(it.get("episode") or 0),
                            video_sn=int(it.get("videoSn") or 0),
                        )
                    )
                except (TypeError, ValueError):
                    continue
            if bucket:
                info.groups["0"] = bucket

        # animeSn 查询时标题带的是第一集的 [1]，需要用当前集信息修正不影响作品名解析
        return info

    def resolve_anime(self, target: str) -> AnimeInfo:
        """输入页面 URL / videoSn / animeSn 均可，自动识别"""
        from .extractor import sn_from_url

        raw = (target or "").strip()

        # 显式指定了 animeSn 参数
        m = re.search(r"[?&]animeSn=(\d+)", raw, re.IGNORECASE)
        if m:
            return self.fetch_anime(anime_sn=int(m.group(1)))

        if raw.isdigit():
            candidate = raw
        else:
            candidate = sn_from_url(raw)
            if not candidate:
                raise ValueError(f"无法从输入中解析 sn: {target}")

        # 同一个数字既可能是 videoSn 也可能是 animeSn，逐个尝试
        value = int(candidate)
        first_error: Exception | None = None
        for kwargs in ({"video_sn": value}, {"anime_sn": value}):
            try:
                return self.fetch_anime(**kwargs)
            except Exception as exc:  # noqa: BLE001
                first_error = exc
                continue
        raise first_error or ValueError(f"无法获取作品信息: {target}")
