"""配置管理：config.ini 的读写与默认值"""

from __future__ import annotations

import configparser
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from . import DEFAULT_UA

CONFIG_FILE = "config.ini"


@dataclass
class Config:
    # 输出
    save_dir: str = "downloads"
    video_ext: str = "mp4"          # mp4 | mkv | ts
    zerofill: int = 2               # 集数补零位数
    add_resolution: bool = True

    # 下载
    thread_count: int = 8
    http_timeout: int = 100
    download_retry: int = 3
    max_speed: str = ""             # 留空不限速，如 "15M"

    # 批量
    cooldown: int = 3               # 每集之间的冷却秒数
    task_retry: int = 2             # 单集失败后的整集重试次数
    ad_wait: int = 120             # 等待广告播完并取得正片流的超时（秒）
    skip_existing: bool = True     # 产物已存在则跳过（避免重复下载 / 覆盖）

    # 清晰度偏好（留空则取最佳）
    resolution: str = ""

    # 配音偏好（双语作品默认下载的配音类型；留空或无法匹配时回退原音）
    default_audio: str = "原音(日语)"

    # 网络
    user_agent: str = DEFAULT_UA
    proxy: str = ""
    use_system_proxy: bool = True

    # 浏览器
    cdp_port: int = 9222
    headless: bool = False
    nav_timeout: int = 60           # 页面导航/等待超时（秒）
    show_cli: bool = (sys.platform == "win32")  # 后台运行时是否弹出 N_m3u8DL-RE 控制台窗口


def load(config_path: Path | None = None) -> Config:
    path = config_path or Path(CONFIG_FILE)
    cfg = Config()
    if not path.exists():
        return cfg
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return cfg

    section = parser["acdl"] if "acdl" in parser else parser[parser.default_section]
    fields = {f: f for f in Config.__dataclass_fields__}

    for key in fields:
        if key not in section:
            continue
        raw = section[key]
        default = getattr(cfg, key)
        try:
            if isinstance(default, bool):
                setattr(cfg, key, raw.strip().lower() in ("1", "true", "yes", "on"))
            elif isinstance(default, int):
                setattr(cfg, key, int(raw))
            else:
                setattr(cfg, key, raw)
        except (ValueError, TypeError):
            pass
    return cfg


def save(cfg: Config, config_path: Path | None = None) -> Path:
    path = config_path or Path(CONFIG_FILE)
    parser = configparser.ConfigParser()
    parser["acdl"] = {k: str(v) for k, v in asdict(cfg).items()}
    with path.open("w", encoding="utf-8") as fh:
        parser.write(fh)
    return path
