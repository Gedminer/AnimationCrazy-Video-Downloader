"""通用工具：日志输出、文件名清洗、时间格式化"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from colorama import Fore, Style, init

init()

# Windows 文件名非法字符
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')
_WHITESPACE = re.compile(r"\s+")


def sanitize(name: str, replacement: str = "_") -> str:
    """清洗为合法的目录/文件名"""
    cleaned = _ILLEGAL.sub(replacement, name)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" .")
    return cleaned or "untitled"


def info(msg: str) -> None:
    print(f"{Fore.CYAN}[info]{Style.RESET_ALL} {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{Fore.GREEN}[ ok ]{Style.RESET_ALL} {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{Fore.YELLOW}[warn]{Style.RESET_ALL} {msg}", flush=True)


def error(msg: str) -> None:
    print(f"{Fore.RED}[fail]{Style.RESET_ALL} {msg}", flush=True)


def step(msg: str) -> None:
    print(f"\n{Fore.MAGENTA}==> {msg}{Style.RESET_ALL}", flush=True)


def ask(prompt: str, default: str = "") -> str:
    """带默认值的交互式输入"""
    suffix = f" [{default}]" if default else ""
    raw = input(f"{Fore.YELLOW}{prompt}{suffix}: {Style.RESET_ALL}").strip()
    return raw or default


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def fmt_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def unique_path(path: Path) -> Path:
    """若目标已存在，追加 (2)/(3) 后缀避免覆盖"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    idx = 2
    while True:
        candidate = parent / f"{stem} ({idx}){suffix}"
        if not candidate.exists():
            return candidate
        idx += 1


def countdown(seconds: int, label: str = "冷却") -> None:
    """集间冷却，可被 Ctrl+C 中断"""
    if seconds <= 0:
        return
    try:
        for remaining in range(seconds, 0, -1):
            sys.stdout.write(
                f"\r{Fore.CYAN}{label} {remaining:>3d}s ...{Style.RESET_ALL}   "
            )
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()
    except KeyboardInterrupt:
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()
        print()
