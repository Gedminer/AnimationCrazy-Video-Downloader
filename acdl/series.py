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


def display_table(
    info: AnimeInfo,
    highlight: Iterable[int] = (),
    group_filter: list[str] | None = None,
) -> None:
    """打印剧集列表

    group_filter 非空时只显示指定分组（双语作品下载前选集用）。
    双语作品按配音标签分块展示（如【原音(日语)】/【中文配音】）。
    """
    marks = set(highlight)
    if group_filter:
        shown = {k: info.groups.get(k, []) for k in group_filter if k in info.groups}
    else:
        shown = info.groups
    if not shown:
        warn("该作品没有可用的剧集列表")
        return

    name = info.bangumi_name or info.raw_title
    print(f"\n{Fore.CYAN}作品:{Style.RESET_ALL} {name}")
    meta = []
    if info.anime_sn:
        meta.append(f"animeSn={info.anime_sn}")
    distinct = max((e.episode for v in shown.values() for e in v), default=0)
    meta.append(f"共 {distinct} 集")
    if info.audio_labels:
        langs = " / ".join(info.group_label(k) for k in shown)
        meta.append(f"配音: {langs}")
    elif len(shown) > 1:
        meta.append(f"分组: {', '.join(sorted(shown))}")
    print(f"{Fore.CYAN}信息:{Style.RESET_ALL} {'  '.join(meta)}")

    print()
    # 单分组（或单语言作品）直接打网格；双语按配音标签分块
    if not info.audio_labels and len(shown) == 1:
        _print_grid(list(shown.values())[0], info, marks)
        return
    for key in shown:
        print(f"{Fore.MAGENTA}【{info.group_label(key)}】{Style.RESET_ALL}")
        _print_grid(shown[key], info, marks)


def _print_grid(eps: list[Episode], info: AnimeInfo, marks: set[int]) -> None:
    if not eps:
        warn("  （无剧集）")
        return
    maxlen = max(len(str(e.episode)) for e in eps)
    for idx, ep in enumerate(eps):
        mark = "*" if ep.episode in marks else " "
        current = ">" if ep.video_sn == info.current_video_sn else " "
        print(f"{str(ep.episode):>{maxlen + 2}}{mark}{current}", end="")
        if (idx + 1) % 8 == 0:
            print()
    if len(eps) % 8:
        print()
    print(f"  ({Fore.YELLOW}*{Style.RESET_ALL} 已选中  {Fore.YELLOW}>{Style.RESET_ALL} 当前播放)")


def select_episodes(
    info: AnimeInfo,
    spec: str | None = None,
    interactive: bool = True,
    group_filter: list[str] | None = None,
) -> list[Episode]:
    """返回用户选定的剧集列表

    group_filter 限定可选分组（双语作品先选配音再选集）。
    """
    eps = info.episodes_of(group_filter) if group_filter else info.all_episodes
    if not eps:
        return []

    # 非交互模式：直接按表达式选取
    if not interactive:
        # 注意：集号上限用「最大集号」而非列表长度，因为双语作品某分组
        # 的集号可能从 38 开始（如 38~50），用 len() 会错误截断选择范围。
        total = max((e.episode for e in eps), default=0)
        numbers = parse_selection(spec or "all", total)
        return _match_numbers(eps, numbers)

    display_table(info, group_filter=group_filter)

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


def episode_title(
    info: AnimeInfo,
    ep: Episode,
    zerofill: int = 2,
    audio_label: str = "",
) -> str:
    """生成单集文件名（不含清晰度与扩展名）

    audio_label 非空时（双语作品）追加 [标签] 以免不同配音版本文件名冲突。
    """
    name = info.bangumi_name or info.raw_title or "untitled"
    num = str(ep.episode).zfill(max(1, zerofill))
    base = f"{name} - 第{num}集"
    if audio_label:
        base += f" [{audio_label}]"
    return base
