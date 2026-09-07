# 网易云音乐下载器

通过 Cookie 登录网易云音乐，支持搜索、歌单下载、全音质（标准 → 超清母带）下载的桌面小工具。

- **图形界面版**：`netease_music_gui.py`（Windows 可直接用打包好的 EXE）
- **命令行版**：`netease_music.py`（纯标准库，Linux/macOS/Windows 通用）
- **零第三方依赖**：加密、下载全部基于 Python 标准库 / 系统自带组件

---

## 功能

- ✅ **Cookie 登录**：粘贴浏览器 Cookie 即登录，自动保存，验证账号昵称
- ✅ **搜索下载**：关键词搜索，勾选/序号批量下载
- ✅ **歌单下载**：输入歌单 ID 或链接，整单下载，自动按歌单名建文件夹
- ✅ **全音质支持**：标准(128k) → 较高 → 极高(320k) → 无损 SQ → Hi-Res → 高清臻音 → 臻音全景声 → 沉浸环绕声 → 超清母带，拿不到自动逐级降级
- ✅ **真实音质识别**：正确识别 Hi-Res 音源（网易 API 常把 Hi-Res 文件标记为 lossless，工具已修正）
- ✅ **进度显示**：批量下载进度条 + 日志面板（GUI）/ 实时百分比（CLI）
- ✅ **自动降级**：账号无权限或歌曲无该音源时，自动降级到可用最高音质并提示原因

---

## 快速开始

### 方式一：使用 Windows EXE（推荐）

1. 从 [Releases](https://github.com/xth81/netease-music-downloader/releases) 或 [Actions 产物](https://github.com/xth81/netease-music-downloader/actions) 下载 `网易云音乐下载器.exe`
2. 双击运行（Windows SmartScreen 提示“来自未知发布者”时点 **更多信息 → 仍要运行**）
3. 首次使用：浏览器打开 `music.163.com` 并登录，按 `F12` → **Application / 应用** → **Cookie**，复制整串（含 `MUSIC_U` 和 `__csrf`），粘贴到界面「Cookie」框 → 点 **保存并验证登录**
4. 搜索或粘贴歌单 ID，选择音质与保存目录，点击下载

> EXE 已打包全部依赖，无需安装 Python。

### 方式二：命令行版（Python）

```bash
# 需要 Python 3.8+，无任何第三方库
python3 netease_music.py                       # 进入交互菜单
python3 netease_music.py --cookie "MUSIC_U=xxx; __csrf=yyy"   # 保存 Cookie 并验证
python3 netease_music.py --query "周杰伦"      # 搜索并选择下载
python3 netease_music.py --playlist 3778678    # 下载整个歌单
python3 netease_music.py --id 2648894511 --quality hires -d ~/Music
```

命令行参数一览：

| 参数 | 说明 |
|---|---|
| `--cookie` | 粘贴 Cookie 字符串，保存并验证后退出 |
| `--query / -q` | 搜索关键词，列出歌曲后可输入序号下载 |
| `--playlist / -p` | 歌单 ID 或歌单链接，下载整个歌单 |
| `--id` | 按歌曲 ID 直接下载 |
| `--quality / -b` | 音质级别（默认 `lossless`） |
| `--dir / -d` | 下载目录（默认当前目录） |
| `--limit` | 搜索返回数量（默认 20） |
| `--no-prompt` | 搜索后只列出歌曲，不进入交互选择 |

---

## 音质体系

网易云音乐的真实音质层级（对应客户端「当前歌曲音质」面板）：

| level 参数 | 官方名称 | 规格 | 权限 |
|---|---|---|---|
| `audiovivid` | 臻音全景声 Audio Vivid | 沉浸三维空间音频，最高 7.1 声道 | SVIP |
| `sky` | 沉浸环绕声 Surround Audio | 环绕音感，最高 5.1 声道 | SVIP |
| `jymaster` | 超清母带 Master | 192kHz / 24bit | SVIP |
| `jyeffect` | 高清臻音 Spatial Audio | 96kHz / 24bit | VIP |
| `hires` | 高解析度无损 Hi-Res | 最高 192kHz / 24bit | VIP |
| `lossless` | 无损 (SQ) | 高保真无损，最高 48kHz / 16bit | VIP |
| `exhigh` | 极高 (HQ) | 近 CD 音质，320kbps | 免费 |
| `higher` | 较高 | 192kbps | 免费 |
| `standard` | 标准 | 128kbps | 免费 |

**降级策略**：目标音质不可用（账号无权限 / 歌曲无此音源）时，按 `audiovivid → sky → jymaster → jyeffect → hires → lossless → exhigh → higher → standard` 顺序自动降级，并在日志中说明原因。

**关于 Hi-Res 的说明**：网易 API 经常把 Hi-Res 音频文件在响应中标记为 `lossless`（CDN 标记 `quality_lossless`），但实际文件是 48kHz/24bit 的 Hi-Res FLAC。本工具通过比对 `musicId` 与歌曲详情的 `hrMusic.id` 来识别真实音质，因此 `--quality hires` 下载到的确实是 Hi-Res 文件。

---

## 从源码构建 EXE

### GitHub Actions（推荐）

推送 `main` / `master` 分支即自动触发 `.github/workflows/build-exe.yml`，在 Windows runner 上用 PyInstaller 打包，产物作为 Actions artifact 提供。

### 本地构建（Windows）

```powershell
pip install pyinstaller
pyinstaller --onefile --noconsole --name "网易云音乐下载器" netease_music_gui.py
```

产物在 `dist/网易云音乐下载器.exe`。

---

## 技术说明

- **接口加密**：本工具调用网易云音乐客户端 API（`eapi` 加密，MD5 摘要 + AES-128-ECB），而非网页版 `weapi`（双重 AES + RSA，更易触发风控）。`eapi` 对脚本调用更稳定。
- **加密实现**：`aes_ecb_encrypt` 通过系统 `openssl` 命令完成 AES 加密（Linux/macOS 预装）。Windows 若无 openssl 可安装 [Git for Windows](https://git-scm.com/downloads)（自带 openssl）或改用纯 Python 版。
- **Cookie 存储**：`~/.netease_cli/cookie.txt`，仅保存合法的 `key=value` 对。
- **登录凭证**：`MUSIC_U`（用户会话）+ `__csrf`（CSRF token）为关键字段，缺一可能影响部分接口。

---

## 常见问题

**Q: 为什么下载周杰伦等歌曲失败？**
版权/地区限制。部分曲目网易本身不提供播放链接（`privilege.st = -100`，详情接口返回 404），VIP 也无法解锁。工具会提示具体原因。

**Q: 有 VIP 权限，但下载不到「高清臻音」？**
两种可能：① 歌曲本身没有该音源（可用 `check_max_quality` 确认 `maxBrLevel`）；② 高等级音质仅通过官方客户端提供，API 渠道上限为 `lossless`。工具会在日志中明确提示是哪种情况。

**Q: 提示「该歌曲最高仅提供 无损 (SQ) 音质」？**
说明查询到该歌的 `maxBrLevel` 为 `lossless`——歌曲本身无更高音源，属正常现象。

**Q: Cookie 过期提示？**
粘贴新的 Cookie 覆盖即可。建议从登录状态下的浏览器重新复制。

**Q: SmartScreen 警告？**
未签名的 exe 会提示，点「更多信息 → 仍要运行」即可。

---

## 免责声明

本工具仅供个人学习与技术研究使用。请尊重音乐版权，支持正版，在网易云音乐官方客户端/平台购买会员与付费内容。
