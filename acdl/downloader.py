"""调用 N_m3u8DL-RE 执行下载

与旧版 ac-dl.py 的关键区别：
    旧版用 subprocess.run(命令字符串)，标题只要含空格或引号就会崩溃。
    这里改用参数列表调用，并对非法文件名做清洗。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import DEFAULT_UA, PLAY_URL
from .config import Config
from .extractor import StreamInfo
from .utils import error, fmt_duration, fmt_size, info, ok, sanitize, warn


@dataclass
class TaskResult:
    episode: int = 0
    video_sn: int = 0
    title: str = ""
    success: bool = False
    message: str = ""
    output: Optional[Path] = None
    duration: float = 0.0
    size: int = 0
    attempts: int = 0


class Downloader:
    def __init__(self, cfg: Config, exe_path: str | Path = "re.exe") -> None:
        self.cfg = cfg
        self.exe_path = Path(exe_path)
        self._ffmpeg = shutil.which("ffmpeg") or ""
        if not self._ffmpeg:
            local = Path("ffmpeg.exe")
            if local.exists():
                self._ffmpeg = str(local.resolve())

    # ------------------------------------------------------------ 环境检查

    def validate(self) -> tuple[bool, str]:
        if not self.exe_path.exists():
            return False, (
                f"找不到下载器: {self.exe_path}\n"
                f"请确认 N_m3u8DL-RE 可执行文件位于该路径，"
                f"或用 --re-path 指定。"
            )
        return True, str(self.exe_path.resolve())

    @property
    def has_ffmpeg(self) -> bool:
        return bool(self._ffmpeg)

    # ------------------------------------------------------------ 命令组装

    def build_command(
        self,
        state: StreamInfo,
        save_dir: Path,
        save_name: str,
        tmp_dir: Path | None = None,
    ) -> list[str]:
        referer = f"{PLAY_URL}?sn={state.sn}"
        cmd: list[str] = [
            str(self.exe_path.resolve()),
            state.m3u8_url,
            "--save-dir", str(save_dir),
            "--save-name", save_name,
            "--thread-count", str(max(1, self.cfg.thread_count)),
            "--download-retry-count", str(max(0, self.cfg.download_retry)),
            "--http-request-timeout", str(max(10, self.cfg.http_timeout)),
            "-H", f"Referer: {referer}",
            "-H", "Origin: https://ani.gamer.com.tw",
            "-H", f"User-Agent: {self.cfg.user_agent or DEFAULT_UA}",
            "--no-log",
        ]

        # 加密流：显式指定方法与密钥，避免下载器自行请求已过期的密钥
        # 注意：N_m3u8DL-RE 要求 AES_128（下划线），而 m3u8 里是 AES-128（连字符）
        if state.method and state.method.upper() != "NONE":
            method = state.method.upper().replace("-", "_")
            cmd += ["--custom-hls-method", method]
        if state.key_hex:
            cmd += ["--custom-hls-key", state.key_hex]
            if state.iv_hex:
                # 只传纯 hex，避免 0x 前缀被当作非法字符
                cmd += ["--custom-hls-iv", state.iv_hex.lower()]

        if self.cfg.max_speed:
            cmd += ["--max-speed", self.cfg.max_speed]

        if tmp_dir:
            cmd += ["--tmp-dir", str(tmp_dir)]

        if self._ffmpeg:
            ext = (self.cfg.video_ext or "mp4").lower().lstrip(".")
            cmd += ["-M", f"format={ext}"]
            if not self.cfg.add_resolution:
                cmd += ["--no-date-info"]
        else:
            # 无 ffmpeg 时退化为二进制合并，产物为 .ts
            cmd += ["--binary-merge"]
            warn("未检测到 ffmpeg，将使用二进制合并，输出 .ts（建议安装 ffmpeg 以获得 mp4/mkv）")

        if self.cfg.proxy:
            cmd += ["--custom-proxy", self.cfg.proxy]
        elif not self.cfg.use_system_proxy:
            cmd += ["--use-system-proxy", "false"]

        return cmd

    # ------------------------------------------------------------ 执行

    def run(
        self,
        state: StreamInfo,
        save_dir: Path,
        save_name: str,
        episode: int = 0,
        title: str = "",
        tmp_dir: Path | None = None,
        max_attempts: int | None = None,
    ) -> TaskResult:
        attempts = max_attempts or max(1, self.cfg.task_retry + 1)
        result = TaskResult(episode=episode, video_sn=int(state.sn or 0), title=title or save_name)
        save_dir.mkdir(parents=True, exist_ok=True)
        started = time.time()

        # 已存在则跳过：避免重复下载 / 覆盖（重跑整批时不会再生副本）
        if self.cfg.skip_existing:
            existing = self._find_output(save_dir, save_name)
            if existing:
                result.success = True
                result.output = existing
                result.size = existing.stat().st_size
                result.message = "跳过（已存在）"
                ok(f"已存在，跳过: {existing.name} ({fmt_size(result.size)})")
                result.duration = time.time() - started
                return result

        cmd = self.build_command(state, save_dir, save_name, tmp_dir)

        # 后台可见性：
        #   show_cli（Windows 默认开）= 用 CREATE_NEW_CONSOLE 让 N_m3u8DL-RE 弹独立
        #   控制台窗口，实时显示进度条/速度/ETA；此时输出不重定向（窗口即显示）。
        #   可用环境变量 ACDL_SHOW_CLI=0 强制关闭弹窗（改为写日志文件），便于无界面环境监控。
        show_cli = bool(self.cfg.show_cli)
        if os.environ.get("ACDL_SHOW_CLI", "").strip().lower() in ("0", "false", "no", "off"):
            show_cli = False
        show_cli = show_cli and (sys.platform == "win32")

        re_log = save_dir / f"{save_name}.re.log"
        run_kwargs: dict = {"cwd": str(self.exe_path.parent or ".")}
        if show_cli:
            run_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            info(f"已弹出 N_m3u8DL-RE 控制台窗口（实时进度）")
        else:
            log_fh = open(re_log, "w", encoding="utf-8", errors="replace")
            run_kwargs["stdout"] = log_fh
            run_kwargs["stderr"] = subprocess.STDOUT
            info(f"N_m3u8DL-RE 输出写入日志: {re_log}")

        for attempt in range(1, attempts + 1):
            result.attempts = attempt
            info(f"第 {attempt}/{attempts} 次尝试：{save_name}")
            try:
                proc = subprocess.run(cmd, **run_kwargs)
                code = proc.returncode
            except FileNotFoundError:
                result.message = f"下载器不存在: {self.exe_path}"
                error(result.message)
                break
            except OSError as exc:
                result.message = f"无法启动下载器: {exc}"
                error(result.message)
                break

            produced = self._find_output(save_dir, save_name)
            if code == 0 and produced:
                result.success = True
                result.output = produced
                result.size = produced.stat().st_size
                result.message = "完成"
                ok(f"下载完成: {produced.name} ({fmt_size(result.size)})")
                break

            result.message = f"下载器退出码 {code}"
            if attempt < attempts:
                warn(f"{result.message}，准备重试...")
                time.sleep(2)

        result.duration = time.time() - started
        if not result.success:
            error(f"下载失败: {save_name} ({result.message})")
        if not show_cli:
            try:
                log_fh.close()
            except Exception:  # noqa: BLE001
                pass
        return result

    @staticmethod
    def _find_output(save_dir: Path, save_name: str) -> Optional[Path]:
        """定位产物：下载器可能附加清晰度后缀或改变扩展名"""
        if not save_dir.exists():
            return None
        candidates = [
            p for p in save_dir.iterdir()
            if p.is_file() and p.stem.startswith(sanitize(save_name))
            and p.suffix.lower() in (".mp4", ".mkv", ".ts", ".mov", ".m4s")
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]


def summarize(results: list[TaskResult], elapsed: float) -> None:
    """打印批量下载汇总"""
    if not results:
        return
    ok_count = sum(1 for r in results if r.success)
    fail_count = len(results) - ok_count
    total_size = sum(r.size for r in results)

    print()
    print("=" * 62)
    print(f"  批量下载汇总")
    print("=" * 62)
    print(f"  成功 {ok_count} 集 / 失败 {fail_count} 集 / 共 {len(results)} 集")
    print(f"  总大小 {fmt_size(total_size)}   总耗时 {fmt_duration(elapsed)}")
    print("-" * 62)

    for r in results:
        flag = "OK  " if r.success else "FAIL"
        tail = f"{fmt_size(r.size)}" if r.success else r.message
        print(f"  [{flag}] {r.title}  ({tail})")

    if fail_count:
        print("-" * 62)
        print("  失败清单：")
        for r in results:
            if not r.success:
                print(f"    - {r.title}: {r.message}")
    print("=" * 62)
