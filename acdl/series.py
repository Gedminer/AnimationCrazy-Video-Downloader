"""剧集列表与选集

剧集数据直接来自官方接口 /anime/v1/video.php 的 episodes 字段，
不依赖页面 DOM，因此不受前端改版影响。
"""

from __future__ import annotations

import re
from typing import Iterable

from colorama import Fore, Style

from .api import AnimeInfo, Episode
from .utils import ask, warn

_ITEM_RE = re.compile(r"^\s*(\d+)?\s*(?:([+-])\s*(\d*))?\s*$")


def parse_selection(spec: str, total: int) -> list[int]:
    """解析选集表达式，返回升序去重的集号列表

    支持：
        空 / all            全部
        last                最新一集
        1-12                区间
        1,3,5-8             枚举与区间混合
        8-  /  8+           从第 8 集到最后一集
        -6                  第 1 到第 6 集
    """
    spec = (spec or "").strip().lower()
    if not spec or spec in ("all", "a", "*"):
        return list(range(1, total + 1))
    if spec in ("last", "latest", "new"):
        return [total] if total > 0 else []

    picked: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _ITEM_RE.match(chunk)
        if not m:
            warn(f"无法解析的选集片段，已跳过: {chunk}")
            continue
        start_s, op, end_s = m.group(1), m.group(2), m.group(3)

        if start_s is None and op == "-" and end_s:
            picked.update(range(1, int(end_s) + 1))
            continue
        if start_s is None:
            warn(f"无法解析的选集片段，已跳过: {chunk}")
            continue

        start = int(start_s)
        if op is None:
            picked.add(start)
        elif op == "-":
            end = int(end_s) if end_s else total
            picked.update(range(start, end + 1))
        elif op == "+":
            picked.update(range(start, total + 1))

    valid = sorted(n for n in picked if 1 <= n <= total)
    dropped = sorted(n for n in picked if not 1 <= n <= total)
    if dropped:
        warn(f"以下集号超出范围已忽略: {dropped}")
    return valid


def display_table(info: AnimeInfo, highlight: Iterable[int] = ()) -> None:
    """打印剧集列表"""
    marks = set(highlight)
    eps = info.all_episodes
    if not eps:
        warn("该作品没有可用的剧集列表")
        return

    name = info.bangumi_name or info.raw_title
    print(f"\n{Fore.CYAN}作品:{Style.RESET_ALL} {name}")
    meta = []
    if info.anime_sn:
        meta.append(f"animeSn={info.anime_sn}")
    if info.total_episode:
        meta.append(f"共 {info.total_episode} 集")
    else:
        meta.append(f"共 {len(eps)} 集")
    if info.multi_group:
        meta.append(f"分组: {', '.join(sorted(info.groups))}")
    print(f"{Fore.CYAN}信息:{Style.RESET_ALL} {'  '.join(meta)}")

    # 每行 8 集；标记放在数字之后，避免与相邻数字混淆
    print()
    maxlen = max(len(info.label(e)) for e in eps)
    for idx, ep in enumerate(eps):
        label = info.label(ep)
        mark = "*" if ep.episode in marks else " "
        current = ">" if ep.video_sn == info.current_video_sn else " "
        print(f"{label:>{maxlen + 2}}{mark}{current}", end="")
        if (idx + 1) % 8 == 0:
            print()
    if len(eps) % 8:
        print()
    print(f"  ({Fore.YELLOW}*{Style.RESET_ALL} 已选中  {Fore.YELLOW}>{Style.RESET_ALL} 当前播放)")


def select_episodes(
    info: AnimeInfo,
    spec: str | None = None,
    interactive: bool = True,
) -> list[Episode]:
    """返回用户选定的剧集列表"""
    eps = info.all_episodes
    if not eps:
        return []

    # 非交互模式：直接按表达式选取
    if not interactive:
        numbers = parse_selection(spec or "all", len(eps))
        return _match_numbers(eps, numbers)

    display_table(info)

    default_spec = spec or "all"
    while True:
        raw = ask(
            "请选择要下载的集数",
            default_spec,
        )
        numbers = parse_selection(raw, len(eps))
        if numbers:
            chosen = _match_numbers(eps, numbers)
            if chosen:
                return chosen
        warn("未选中任何有效集数，请重新输入")


def _match_numbers(eps: list[Episode], numbers: list[int]) -> list[Episode]:
    index = {e.episode: e for e in eps}
    # 集号可能重复（多分组），按出现顺序取
    out: list[Episode] = []
    seen: set[int] = set()
    for n in numbers:
        ep = index.get(n)
        if ep is not None and ep.video_sn not in seen:
            seen.add(ep.video_sn)
            out.append(ep)
    # 保持原始顺序
    order = {e.video_sn: i for i, e in enumerate(eps)}
    out.sort(key=lambda e: order.get(e.video_sn, 0))
    return out


def episode_title(info: AnimeInfo, ep: Episode, zerofill: int = 2) -> str:
    """生成单集文件名（不含清晰度与扩展名）"""
    name = info.bangumi_name or info.raw_title or "untitled"
    num = str(ep.episode).zfill(max(1, zerofill))
    return f"{name} - 第{num}集"
