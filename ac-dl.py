#!/usr/bin/env python
# Project: AnimationCrazy Video Downloader (巴哈姆特動畫瘋視頻下載器)
# Nickname: ac-dl.py
# Original Creator: CrymanChen
# Refactored: v2.0 增加浏览器自动取流与批量下载
# Copyright (C) CrymanChen. All Rights Reserved.

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Windows 控制台 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from colorama import Fore, Style, init  # noqa: E402

init()

from acdl import __version__  # noqa: E402
from acdl.api import AniClient  # noqa: E402
from acdl.browser import BrowserSession  # noqa: E402
from acdl.config import Config, load, save  # noqa: E402
from acdl.downloader import Downloader, TaskResult, summarize  # noqa: E402
from acdl.extractor import StreamExtractor, sn_from_url  # noqa: E402
from acdl.series import display_table, episode_title, select_episodes  # noqa: E402
from acdl.utils import (  # noqa: E402
    ask, countdown, error, info, ok, sanitize, step, warn,
)

if getattr(sys, "frozen", False):
    # PyInstaller 单目录打包：ac-dl.exe 位于 dist/ac-dl/ac-dl.exe，
    # 资源（tools/、ms-playwright/、ffmpeg、config）与本目录同级。
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
NEW_RE = ROOT / "tools" / "N_m3u8DL-RE" / "N_m3u8DL-RE.exe"
OLD_RE = ROOT / "re.exe"
PROFILE_DIR = ROOT / ".auth" / "chrome-profile"
TMP_DIR = ROOT / ".cache" / "tmp"

# 便携版：若同目录存在 ms-playwright 则优先用它（否则走默认 LOCALAPPDATA，开发期不受影响）
_portable_pw = ROOT / "ms-playwright"
if _portable_pw.is_dir():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_portable_pw)


# ---------------------------------------------------------------- 工具


def detect_re(cfg: Config, override: str | None = None) -> Path:
    """定位 N_m3u8DL-RE，优先新版"""
    if override:
        return Path(override)
    if NEW_RE.exists():
        return NEW_RE
    if OLD_RE.exists():
        warn(f"使用旧版下载器 {OLD_RE.name}（建议升级到 tools/ 下的新版本）")
        return OLD_RE
    return ROOT / "N_m3u8DL-RE.exe"


def build_client(cfg: Config, cookie: str = "") -> AniClient:
    client = AniClient(cfg)
    if cookie:
        client.import_cookie_header(cookie)
    return client


def show_stream(state) -> None:
    print(f"    sn        : {state.sn}")
    print(f"    m3u8      : {state.m3u8_url[:100]}")
    if state.resolution:
        print(f"    清晰度    : {state.resolution}")
    if state.key_url:
        print(f"    密钥地址  : {state.key_url[:100]}")
    if state.key_hex:
        print(f"    密钥(hex) : {state.key_hex}")
    if state.iv_hex:
        print(f"    偏移向量  : 0x{state.iv_hex}")
    if not state.key_hex and state.key_url:
        warn("未取得密钥本体，下载器将自行请求密钥地址（可能因鉴权失败）")


# ---------------------------------------------------------------- 子命令


def cmd_login(cfg: Config, args) -> int:
    """登录：持久化 Profile 或 CDP 接管"""
    mode = args.mode
    if mode == "cdp":
        info(
            "CDP 模式需要你先手动启动 Chrome 并登录动画疯：\n"
            f'  chrome.exe --remote-debugging-port={args.cdp_port} '
            f'--user-data-dir="%LOCALAPPDATA%\\ac-dl-cdp"'
        )
        input("  登录完成后按回车继续...")
        session = BrowserSession(cfg, mode="cdp", cdp_port=args.cdp_port)
        try:
            session.start()
            logged, reason = session.check_login()
            if logged:
                ok(f"已接管登录态：{reason}")
                return 0
            error(f"未检测到登录态：{reason}")
            return 1
        finally:
            session.stop()

    session = BrowserSession(cfg, mode="profile", profile_dir=PROFILE_DIR)
    try:
        session.start()
        if session.interactive_login(timeout=args.timeout):
            return 0
        return 1
    finally:
        session.stop()


def cmd_logout(cfg: Config, args) -> int:
    session = BrowserSession(cfg, mode="profile", profile_dir=PROFILE_DIR)
    session.reset_profile()
    ok("已清除本地登录态")
    return 0


def cmd_info(cfg: Config, args) -> int:
    """查看作品与剧集列表（纯接口，不需要浏览器）"""
    client = build_client(cfg, args.cookie)
    try:
        anime = client.resolve_anime(args.target)
    except Exception as exc:  # noqa: BLE001
        error(f"获取作品信息失败: {exc}")
        return 1
    display_table(anime)
    if args.dump_json:
        import json
        payload = {
            "animeSn": anime.anime_sn,
            "title": anime.bangumi_name,
            "totalEpisode": anime.total_episode,
            "episodes": [
                {"episode": e.episode, "videoSn": e.video_sn}
                for e in anime.all_episodes
            ],
        }
        Path(args.dump_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ok(f"已导出 JSON: {args.dump_json}")
    return 0


def _capture_one(
    session: BrowserSession,
    extractor: StreamExtractor,
    video_sn: int,
    cfg: Config,
    cookies: list[dict],
) -> object:
    page = session.new_page()
    try:
        state = extractor.capture(
            page, str(video_sn), wait_seconds=args_wait(cfg), cookies=cookies
        )
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
    return state


def args_wait(cfg: Config) -> int:
    return cfg.ad_wait


def cmd_download(cfg: Config, args, batch: bool = False) -> int:
    """下载单集或批量下载"""
    re_path = detect_re(cfg, args.re_path)
    downloader = Downloader(cfg, re_path)
    valid, msg = downloader.validate()
    if not valid:
        error(msg)
        return 1
    info(f"下载器: {msg}")

    client = build_client(cfg, args.cookie)
    step("读取作品信息")
    try:
        anime = client.resolve_anime(args.target)
    except Exception as exc:  # noqa: BLE001
        error(f"获取作品信息失败: {exc}")
        return 1

    ok(f"{anime.bangumi_name}  共 {len(anime.all_episodes)} 集")

    if batch:
        chosen = select_episodes(anime, args.select, interactive=not args.yes)
    else:
        target_sn = sn_from_url(args.target)
        chosen = [e for e in anime.all_episodes if str(e.video_sn) == target_sn]
        if not chosen:
            chosen = anime.all_episodes[-1:]

    if not chosen:
        error("没有选中任何剧集")
        return 1

    print(f"\n{Style.BRIGHT}待下载 {len(chosen)} 集:{Style.RESET_ALL}")
    for e in chosen:
        print(f"  - {episode_title(anime, e, cfg.zerofill)}  (sn={e.video_sn})")
    if not args.yes:
        if ask("确认开始下载？(y/n)", "y").lower() not in ("y", "yes", ""):
            info("已取消")
            return 0

    save_root = Path(args.save_dir or cfg.save_dir)
    if not save_root.is_absolute():
        save_root = ROOT / save_root
    out_dir = save_root / sanitize(anime.bangumi_name or "untitled")

    step("启动浏览器会话")
    session = BrowserSession(
        cfg,
        mode=args.mode,
        profile_dir=PROFILE_DIR,
        cdp_port=args.cdp_port,
        headless=args.headless or cfg.headless,
    )
    session.start()
    extractor = StreamExtractor(cfg)

    results: list[TaskResult] = []
    started = time.time()
    try:
        if args.mode != "cdp":
            session.ensure_login(allow_interactive=not args.yes)

        cookies = []
        try:
            cookies = session.context.cookies()
        except Exception:  # noqa: BLE001
            pass
        client.set_cookies(cookies)

        for idx, ep in enumerate(chosen, 1):
            name = episode_title(anime, ep, cfg.zerofill)
            step(f"[{idx}/{len(chosen)}] {name}  (sn={ep.video_sn})")

            state = _capture_one(session, extractor, ep.video_sn, cfg, cookies)
            if not state or not state.ready:
                results.append(TaskResult(
                    episode=ep.episode, video_sn=ep.video_sn, title=name,
                    success=False, message="未能取得 m3u8（登录态不足或该集无权限）",
                ))
                error(results[-1].message)
                continue

            show_stream(state)
            result = downloader.run(
                state,
                save_dir=out_dir,
                save_name=sanitize(name),
                episode=ep.episode,
                title=name,
                tmp_dir=TMP_DIR,
            )
            results.append(result)

            if idx < len(chosen) and cfg.cooldown > 0:
                countdown(cfg.cooldown, "集间冷却")
    except KeyboardInterrupt:
        warn("\n已中断（Ctrl+C）")
    finally:
        session.stop()

    summarize(results, time.time() - started)
    return 0 if all(r.success for r in results) else 2


def cmd_manual(cfg: Config, args) -> int:
    """旧版手动输入模式（回退通道）"""
    warn("手动模式：需要你自行从浏览器开发者工具中获取三项信息")
    url = ask("m3u8 链接地址")
    key_url = ask("密钥链接")
    sn = ask("sn 号")
    if not (url and sn):
        error("m3u8 链接与 sn 号不能为空")
        return 1

    from acdl.extractor import StreamInfo
    state = StreamInfo(sn=sn, m3u8_url=url, key_url=key_url)

    extractor = StreamExtractor(cfg)
    if args.cookie:
        extractor.sync_cookies([])
        for part in args.cookie.split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                extractor._session.cookies.set(
                    k.strip(), v.strip(), domain="gamer.com.tw", path="/"
                )
    if key_url and not state.key_hex:
        extractor.fetch_key_bytes(state, f"https://ani.gamer.com.tw/animeVideo.php?sn={sn}")

    name = ask("保存文件名（不含扩展名）", f"video_{sn}")
    re_path = detect_re(cfg, args.re_path)
    downloader = Downloader(cfg, re_path)
    valid, msg = downloader.validate()
    if not valid:
        error(msg)
        return 1

    out_dir = Path(args.save_dir or cfg.save_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    result = downloader.run(state, out_dir, sanitize(name), tmp_dir=TMP_DIR)
    summarize([result], result.duration)
    return 0 if result.success else 2


def cmd_config(cfg: Config, args) -> int:
    """查看或修改配置"""
    if args.reset:
        path = save(Config())
        ok(f"已重置配置: {path}")
        return 0

    print(f"\n当前配置（{Path(args.config).resolve()}）：\n")
    width = max(len(k) for k in Config.__dataclass_fields__)
    for key in Config.__dataclass_fields__:
        print(f"  {key:<{width}} = {getattr(cfg, key)}")
    print(f"\n用文本编辑器修改 {args.config} 后重新运行即可生效。\n")
    return 0


def cmd_menu(cfg: Config, args) -> int:
    """交互式主菜单"""
    print(f"\n{Style.BRIGHT}动画疯下载器 ac-dl v{__version__}{Style.RESET_ALL}")
    print("  1. 批量下载整部作品")
    print("  2. 下载单集")
    print("  3. 查看作品剧集列表")
    print("  4. 登录 / 重新登录")
    print("  5. 查看配置")
    print("  6. 手动输入模式（旧版）")
    print("  0. 退出")

    choice = ask("请选择", "1")
    if choice == "1":
        target = ask("请输入作品页/播放页 URL 或 sn 号")
        if not target:
            return 0
        args.target = target
        args.select = ask("要下载哪些集？（如 1-12 / 1,3,5-8 / all / 8-）", "all")
        return cmd_download(cfg, args, batch=True)
    if choice == "2":
        target = ask("请输入播放页 URL 或 sn 号")
        if not target:
            return 0
        args.target = target
        return cmd_download(cfg, args, batch=False)
    if choice == "3":
        target = ask("请输入作品页/播放页 URL 或 sn 号")
        if not target:
            return 0
        args.target = target
        return cmd_info(cfg, args)
    if choice == "4":
        return cmd_login(cfg, args)
    if choice == "5":
        return cmd_config(cfg, args)
    if choice == "6":
        return cmd_manual(cfg, args)
    return 0


# ---------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ac-dl.py",
        description="动画疯视频下载器（自动取流 + 批量下载）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  ac-dl.py login                        首次登录\n"
            "  ac-dl.py info 50646                   查看作品的全部剧集\n"
            "  ac-dl.py dl 50646                     下载单集\n"
            "  ac-dl.py batch 50646 --select 1-12    批量下载 1-12 集\n"
            "  ac-dl.py batch 50646 --select all -y  非交互下载全集\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"ac-dl {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.ini", help="配置文件路径")
    common.add_argument("--mode", choices=("profile", "cdp"), default="profile",
                        help="登录态方式：profile=持久化目录，cdp=接管已开 Chrome")
    common.add_argument("--cdp-port", type=int, default=None, help="CDP 远程调试端口")
    common.add_argument("--headless", action="store_true", help="无头模式（可能影响取流）")
    common.add_argument("--cookie", default="", help="手动指定 Cookie 请求头字符串")
    common.add_argument("--save-dir", default=None, help="输出根目录")
    common.add_argument("--resolution", default=None, help="指定清晰度，如 1080")
    common.add_argument("--re-path", default=None, help="N_m3u8DL-RE 可执行文件路径")
    common.add_argument("-y", "--yes", action="store_true", help="非交互模式")

    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("login", parents=[common], help="登录动画疯")
    p.add_argument("--timeout", type=int, default=600, help="等待登录的秒数")

    sub.add_parser("logout", parents=[common], help="清除本地登录态")

    p = sub.add_parser("info", parents=[common], help="查看作品与剧集列表")
    p.add_argument("target", help="播放页/作品页 URL 或 sn 号")
    p.add_argument("--dump-json", default=None, help="导出剧集列表为 JSON")

    p = sub.add_parser("dl", parents=[common], help="下载单集")
    p.add_argument("target", help="播放页 URL 或 sn 号")

    p = sub.add_parser("batch", parents=[common], help="批量下载剧集")
    p.add_argument("target", help="作品页/播放页 URL 或 sn 号")
    p.add_argument("--select", default="all",
                   help="选集表达式：all / 1-12 / 1,3,5-8 / 8- / last")

    p = sub.add_parser("manual", parents=[common], help="手动输入模式（旧版回退）")
    sub.add_parser("config", parents=[common], help="查看配置").add_argument(
        "--reset", action="store_true", help="重置为默认配置")

    parser.set_defaults(command="menu")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load(Path(args.config))
    if not Path(args.config).exists():
        save(cfg, Path(args.config))
        info(f"已生成默认配置: {args.config}")

    if args.cdp_port:
        cfg.cdp_port = args.cdp_port
    if args.resolution:
        cfg.resolution = args.resolution
    if args.save_dir:
        cfg.save_dir = args.save_dir

    handlers = {
        "login": lambda: cmd_login(cfg, args),
        "logout": lambda: cmd_logout(cfg, args),
        "info": lambda: cmd_info(cfg, args),
        "dl": lambda: cmd_download(cfg, args, batch=False),
        "batch": lambda: cmd_download(cfg, args, batch=True),
        "manual": lambda: cmd_manual(cfg, args),
        "config": lambda: cmd_config(cfg, args),
        "menu": lambda: cmd_menu(cfg, args),
    }
    handler = handlers.get(args.command, handlers["menu"])

    try:
        return handler()
    except KeyboardInterrupt:
        warn("\n已中断")
        return 130
    except Exception as exc:  # noqa: BLE001
        error(f"未预期的错误: {exc}")
        if "-v" in (argv or sys.argv):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
