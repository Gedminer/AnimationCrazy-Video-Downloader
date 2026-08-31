# 动画疯视频下载器 AnimationCrazy Video Downloader（ac-dl v2）

> 一个用于下载巴哈姆特**动画疯**（ani.gamer.com.tw）视频的命令行工具。
> 本项目无任何商业用途，仅为个人学习、方便个人使用。使用或转发时请标明原作者（CrymanChen）。

📘 **新手？先看 [使用教程](使用教程.md)。**

---

## 一、项目简介

原始版本（ac-dl.py v1）需要用户手动从浏览器开发者工具复制 **m3u8 链接、密钥链接、sn 号**，操作繁琐且容易出错。

**v2 重构版**在此基础上做了完整升级：

- **自动取流**：通过内置浏览器渲染播放页，自动捕获正片的 `m3u8` 地址、密钥地址、密钥本体与 `IV`（在贴片广告播完之后才锁定正片流）。
- **批量下载**：根据作品页 / 播放页自动列出整部剧集，支持按区间、枚举、最新集等方式选择后批量下载。
- **无头后台运行**：可选用完整 Chromium 的 `headless` 模式在后台静默取流，并弹出 `N_m3u8DL-RE` 独立控制台窗口实时显示进度。
- **稳定解密**：显式下发 AES-128 密钥与 `IV`，并以 `_locked` 机制锁定已验证可用的清晰度，规避跨清晰度密钥错位与异常 `IV` 导致的解密失败。

### 版权与致谢

本项目由两部分组成：

- `ac-dl.py` + `acdl/`：Python 取流与调度逻辑（本项目 v2 重构）。
- `N_m3u8DL-RE`（下载器本体）：由 **nilaoda** 开发，本项目仅调用其代为下载，在此致谢。

---

## 二、功能特性

| 能力 | 说明 |
|---|---|
| 自动取流 | 渲染播放页 + 网络响应拦截，广告后锁定正片 m3u8 / 密钥 / IV |
| 批量下载 | 区间 / 枚举 / 最新集选择，串行 + 集间冷却降低风控 |
| 无头后台 | 完整 Chromium 的 headless 模式，验证可正常取流 |
| 实时进度 | `show_cli` 弹独立控制台窗口；亦可切日志模式 |
| 防重复 | `skip_existing` 命中已存在产物直接跳过 |
| 双登录态 | `profile`（持久化目录）与 `cdp`（接管已开 Chrome） |
| 稳定解密 | 显式 key/IV + `_locked` 锁清晰度，规避 Padding 错误 |
| 跨平台 | 纯 Python CLI，核心逻辑不依赖特定 OS（弹窗特性 Windows 默认开） |

---

## 三、环境要求

- **Python 3.9+**
- 能正常访问 `ani.gamer.com.tw` 的网络环境（相关地区用户需自备可用网络）
- 一个**已登录动画疯**的账号（用于获取有权限观看的内容）
- **Playwright** 浏览器内核（首次使用需安装）
- **ffmpeg**（可选但强烈推荐）：用于封装为 `mp4` / `mkv`；缺失时退化为二进制合并输出 `.ts`
- `N_m3u8DL-RE` 下载器**已打包在 `tools/` 目录**，开箱即用，无需另行下载

---

## 四、安装步骤

```bash
# 1. 获取仓库
git clone https://github.com/Gedminer/AnimationCrazy-Video-Downloader.git
cd AnimationCrazy-Video-Downloader

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器内核（仅首次）
playwright install chromium

# 4.（可选）安装 ffmpeg 并确保在 PATH 中，以获得 mp4/mkv 输出
#    Windows 可将 ffmpeg.exe 放到仓库根目录，程序会自动发现
```

> **关于下载器二进制**：`tools/N_m3u8DL-RE/N_m3u8DL-RE.exe` 已随仓库提供；若需升级，到
> [nilaoda/N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE/releases) 下载对应版本替换即可。

---

## 五、配置说明

首次运行任意命令会自动生成 `config.ini`（已被 `.gitignore` 排除，**不会提交**）。
可参考仓库内的 **`config.example.ini`** 了解全部选项。

完整配置项如下（方括号为默认值）：

### 输出

| 配置 | 说明 | 默认 |
|---|---|---|
| `save_dir` | 输出根目录（相对仓库或绝对路径） | `downloads` |
| `video_ext` | 封装格式：`mp4` / `mkv` / `ts` | `mp4` |
| `zerofill` | 集数补零位数（如 `2` → `第01集`） | `2` |
| `add_resolution` | 文件名是否附加清晰度（如 `1080p`） | `True` |

### 下载

| 配置 | 说明 | 默认 |
|---|---|---|
| `thread_count` | 并发下载线程数 | `8` |
| `http_timeout` | 单段 HTTP 请求超时（秒） | `100` |
| `download_retry` | 分片下载失败重试次数 | `3` |
| `max_speed` | 限速，留空不限速（如 `15M` / `2000K`） | `` |

### 批量

| 配置 | 说明 | 默认 |
|---|---|---|
| `cooldown` | 每集之间的冷却秒数（降低风控） | `3` |
| `task_retry` | 单集整体失败后的重试次数 | `2` |
| `ad_wait` | 等待广告播完并取得正片流的超时（秒） | `90` |
| `skip_existing` | 产物已存在则跳过（避免重复下载 / 覆盖） | `True` |

### 清晰度偏好

| 配置 | 说明 | 默认 |
|---|---|---|
| `resolution` | 指定清晰度（如 `1080` / `720` / `360`），留空取最佳 | `` |

### 网络

| 配置 | 说明 | 默认 |
|---|---|---|
| `user_agent` | 请求使用的 UA | 内置 Chrome UA |
| `proxy` | 自定义代理（如 `http://127.0.0.1:7890`） | `` |
| `use_system_proxy` | 是否使用系统代理 | `True` |

### 浏览器

| 配置 | 说明 | 默认 |
|---|---|---|
| `cdp_port` | CDP 远程调试端口（`--mode cdp` 时） | `9222` |
| `headless` | 无头模式（`True`=后台无窗口；已适配完整 Chromium，取流正常） | `False` |
| `nav_timeout` | 页面导航 / 等待超时（秒） | `60` |
| `show_cli` | 后台运行时是否弹出 `N_m3u8DL-RE` 独立控制台窗口（Windows 默认开） | `True` |

> **环境变量覆盖**：无论 `show_cli` 如何设置，运行时设置 `ACDL_SHOW_CLI=0` 都会强制关闭弹窗、改为写入
> `<保存名>.re.log` 日志文件，便于无界面环境监控。

---

## 六、使用方法

通用参数（各子命令均可附加）：

```
--mode {profile,cdp}   登录态方式：profile=持久化目录，cdp=接管已开 Chrome
--cdp-port PORT        CDP 远程调试端口
--headless             无头模式（后台无窗口）
--cookie STR           手动指定 Cookie 请求头字符串
--save-dir DIR         覆盖输出根目录
--resolution RES       覆盖清晰度，如 1080
--re-path PATH         N_m3u8DL-RE 可执行文件路径
-y, --yes             非交互模式（不再询问确认）
```

### 1. 登录

浏览器会打开动画疯登录页，请手动登录一次（Cookie 保存在本地 `.auth/`，之后免登录）：

```bash
python ac-dl.py login
```

> 若你已用 Chrome 登录动画疯，也可改用 **CDP 模式**复用现有浏览器：
> 先以 `chrome.exe --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\ac-dl-cdp"` 启动，
> 再运行 `python ac-dl.py login --mode cdp`。

退出登录（清除本地登录态）：

```bash
python ac-dl.py logout
```

### 2. 查看作品剧集列表

```bash
python ac-dl.py info 50646          # 传入作品页/播放页 URL 或任意一集的 sn
python ac-dl.py info 50646 --dump-json episodes.json   # 导出剧集列表为 JSON
```

### 3. 下载单集

```bash
python ac-dl.py dl 50646            # 默认下载该作品最新一集
```

### 4. 批量下载

```bash
python ac-dl.py batch 50646 --select 1-12      # 下载第 1~12 集
python ac-dl.py batch 50646 --select all -y    # 非交互下载全部集
```

**选集表达式**：

| 写法 | 含义 |
|---|---|
| `all` / 留空 | 全部集 |
| `1-12` | 第 1 到第 12 集 |
| `1,3,5-8` | 第 1、3、5、6、7、8 集 |
| `8-` 或 `8+` | 从第 8 集到最后一集 |
| `-6` | 第 1 到第 6 集 |
| `last` | 仅最新一集 |

### 5. 手动输入模式（旧版回退）

当自动取流因特殊页面结构失败时，可退回手动模式，自行从浏览器开发者工具获取三项信息：

```bash
python ac-dl.py manual
```

### 6. 查看 / 重置配置

```bash
python ac-dl.py config              # 打印当前配置
python ac-dl.py config --reset      # 重置为默认配置
```

### 7. 交互式主菜单

不带子命令直接运行即进入菜单：

```bash
python ac-dl.py
```

---

## 七、无头模式与后台运行（v2 重点）

`v2` 推翻了早期"必须 headful"的结论：

- Playwright 默认的 `headless` 会调用**残缺的 `chromium_headless_shell`**，功能不全、易被动画疯反爬识别，且广告不会自动推进。
- `v2` 在 `headless=True` 时自动改用 **完整 Chromium**（`ms-playwright/chromium-*/chrome-win64/chrome.exe`），广告自动推进、年龄确认 / 跳过广告 / 取流 / 密钥全部正常。

后台运行推荐命令（弹独立控制台实时看进度）：

```bash
# 无头 + 弹窗（Windows）
python ac-dl.py batch 50646 --select all -y --headless
```

无界面 / 服务器环境（不弹窗，写日志）：

```bash
ACDL_SHOW_CLI=0 python ac-dl.py batch 50646 --select all -y --headless
```

---

## 八、工作原理（简述）

1. **剧集列表**：调用动画疯官方接口 `/anime/v1/video.php`，直接返回作品全部剧集与 `sn`，不依赖页面 DOM，抗前端改版。
2. **取流（三阶段状态机）**：用 Playwright 渲染播放页并监听网络响应——
   - 监听：捕获所有 `m3u8` / 密钥响应；
   - 判流：区分广告流与正片流；
   - 锁正片：广告结束后识别并丢弃广告流，只在正片开始后锁定其 `media playlist` + 密钥 + `IV`。
3. **稳定解密**：把正片 `media playlist` + 显式 `AES-128` 密钥 / `IV` 交给 `N_m3u8DL-RE`。
   - 以 `_locked` 机制锁定已验证可用的清晰度（如 720p），避免播放器自动切低清导致密钥 / `IV` 错位；
   - 规避某些番剧 360p 列表声明 `IV` 与实际分片 `IV` 不符而触发的 `Padding is invalid` 解密失败。
4. **下载**：`N_m3u8DL-RE` 完成分片下载、解密与封装。

---

## 九、目录结构

```
AnimationCrazy-Video-Downloader/
├── ac-dl.py                 # CLI 入口（login/logout/info/dl/batch/manual/config/menu）
├── legacy_manual.py         # v1 旧版手动输入脚本（回退通道）
├── acdl/                    # v2 重构核心包
│   ├── __init__.py          # 版本号 / 默认 UA / 播放页地址
│   ├── config.py            # config.ini 读写与默认值
│   ├── browser.py           # 浏览器会话（profile / cdp，完整 Chromium 无头）
│   ├── extractor.py         # 三阶段取流状态机 + 解密参数捕获
│   ├── downloader.py        # 调用 N_m3u8DL-RE（弹窗 / 跳过 / 重试）
│   ├── api.py               # 动画疯官方接口封装
│   ├── series.py            # 剧集列表展示与选集解析
│   └── utils.py             # 日志 / 终端着色 / 文件名清洗等
├── tools/
│   └── N_m3u8DL-RE/
│       └── N_m3u8DL-RE.exe  # 下载器本体（已打包）
├── config.example.ini       # 配置示例
├── requirements.txt
├── README.md
└── .gitignore
```

> `.auth/`（登录态）、`.cache/`（临时与日志）、`downloads/`（产物）、`config.ini`（本地配置）、
> `.workbuddy/`（本地记忆）均被 `.gitignore` 排除，不会进入仓库。

---

## 十、常见问题 / 故障排查

**Q：`Padding is invalid` 解密失败？**
A：通常由跨清晰度密钥错位或异常 `IV` 引起。v2 已通过 `_locked` 锁定已验证清晰度规避；
若仍出现，尝试在 `config.ini` 显式指定 `resolution = 720` 后再跑。

**Q：无头模式取不到流 / 卡在广告？**
A：确认使用的是**完整 Chromium** 而非 `chromium_headless_shell`（v2 已自动处理）；
若仍异常，临时改用 `--mode cdp` 或带头模式排查登录态。

**Q：输出只有 `.ts` 而不是 `.mp4`？**
A：未检测到 `ffmpeg`。请安装 ffmpeg 并确保在 `PATH` 中，或把 `ffmpeg.exe` 放到仓库根目录。

**Q：弹窗太多 / 想静默运行？**
A：设置环境变量 `ACDL_SHOW_CLI=0`，下载器输出将写入 `<保存名>.re.log`。

---

## 十一、合规与风险提示

- 下载内容仅用于你**已获授权**的个人离线观看。
- Cookie 属于敏感凭据，已通过 `.gitignore` 排除，请勿提交到仓库。
- 批量连续请求存在账号风控风险，程序默认串行 + 集间冷却以降低风险。
- 请遵守动画疯平台的服务条款与当地法律法规。

---

## 十二、推送到 GitHub（PowerShell）

贡献者把本项目推到自己仓库时，可用附带的 `push-to-github.ps1`，采用**令牌不经过任何人**的方式：

```powershell
powershell -ExecutionPolicy Bypass -File push-to-github.ps1
```

脚本会在你本机提示输入 GitHub 用户名与令牌（`-AsSecureString`，输入不回显），
用 `https://<TOKEN>@github.com/<用户>/<仓库>.git` 临时作为 remote 地址推送，
**推送成功后立即 `git remote set-url` 抹掉令牌**，避免明文留在 `.git/config`。
前置：在 GitHub 网页建好空仓库、生成 Fine-grained PAT（Contents: Read and write，作用域仅该仓库）。
默认推到名为 `fork` 的远程（不动原有 `origin`）；想直接推到 `origin` 加 `-RemoteName origin` 即可。
详见脚本头部注释。

---

## 许可证

本项目沿用原始仓库许可证（见 `LICENSE`）。`N_m3u8DL-RE` 的版权归其作者 **nilaoda** 所有。
