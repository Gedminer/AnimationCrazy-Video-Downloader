# AnimationCrazy-Video-Downloader v2.0.0

巴哈姆特動畫瘋下载器 v2 —— 浏览器自动取流 + 批量下载 + 无头后台。

## 主要特性

- **自动取流**：通过 Playwright 响应拦截，自动捕获 `m3u8` / 密钥 / `IV` / `sn`，无需手动抓包。三阶段状态机（监听 → 判流 → 等广告结束锁定正片）。
- **双登录态**：支持持久化 profile 目录登录，以及 CDP（Chrome DevTools Protocol）连接已打开的浏览器。
- **无头后台**：headless 模式下使用完整 Chromium（绕过残缺的 `headless_shell`），广告自动推进，年龄确认 / 跳过广告 / 取流 / 密钥全程自动，无需人工干预。
- **批量下载**：`batch` 命令串行下载整季，集间自动冷却，支持 `--select` 选择集数；`--yes` 跳过确认。
- **后台 CLI 可见**：`N_m3u8DL-RE` 以独立控制台窗口运行（config `show_cli = True`；环境变量 `ACDL_SHOW_CLI=0` 可关闭弹窗并改写 `.re.log`）。
- **档位锁定（修复解密崩溃）**：锁定 720p 并修复跨档位密钥不匹配（`Padding is invalid`）与 360p 列表 IV 与实际分片 IV 不一致的异常，保证解密稳定。
- **跳过已存在**：已下载的文件自动跳过，避免重复拉取。

## 使用方法

详见仓库内 `使用教程.md`。典型流程：

1. `ac-dl.py login`（或配置 CDP 登录态）
2. `ac-dl.py info <sn>` 查看番剧与集数
3. `ac-dl.py batch <sn> --select 1-12 --yes` 批量下载
4. 文件输出至 `downloads/<番剧名>/`

## 安装

详见 `README.md`。核心依赖：

- Python 3.10+
- Playwright（Chromium）
- `N_m3u8DL-RE`（已随仓库附带于 `tools/N_m3u8DL-RE/`）

## 说明

本工具依赖浏览器自动化，请仅用于下载你拥有合法观看 / 下载权限的内容。
