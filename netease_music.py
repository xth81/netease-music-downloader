#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐命令行小工具：Cookie 登录 + 搜索 + 下载
仅使用 Python 标准库，无需安装任何第三方依赖。

功能：
  1. 通过粘贴浏览器 Cookie 登录（保存到 ~/.netease_cli/cookie.txt）
  2. 验证登录状态（显示账号昵称）
  3. 搜索歌曲并交互选择下载
  4. 支持 128k 标准 / 320k 高品 / 999k 无损（无损需会员账号）

用法示例：
  python3 netease_music.py --cookie "MUSIC_U=xxxx; __csrf=yyyy"   # 设置并保存 Cookie
  python3 netease_music.py                                        # 进入交互模式
  python3 netease_music.py --query "周杰伦"                       # 搜索列表面板
  python3 netease_music.py --id 1858990676 --quality 320          # 直接下载指定歌曲
"""

import argparse
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".netease_cli")
COOKIE_FILE = os.path.join(CONFIG_DIR, "cookie.txt")

# 网易云音质体系（level 参数 -> (显示名, 采样/码率说明)）
# 官方文档映射：
#   audiovivid = 臻音全景声 Audio Vivid (SVIP, 7.1声道, 需单独配置)
#   sky        = 沉浸环绕声 Surround Audio (SVIP, 5.1声道)
#   jymaster   = 超清母带 Master (SVIP, 192kHz/24bit)
#   jyeffect   = 高清臻音 Spatial Audio (VIP, 96kHz/24bit)
#   hires      = 高解析度无损 Hi-Res (VIP, 192kHz/24bit)
#   lossless   = 无损 SQ (VIP, 48kHz/16bit)
#   exhigh     = 极高 HQ (320kbps)
#   higher     = 较高 (192kbps)
#   standard   = 标准 (128kbps)
# 注意：高级音质需歌曲本身有对应音源，接口自动降级到实际可用级别。
QUALITY_LEVELS = {
    "audiovivid": ("臻音全景声 Audio Vivid", "7.1声道"),
    "sky":        ("沉浸环绕声 Surround Audio", "5.1声道"),
    "jymaster":   ("超清母带 Master", "192kHz/24bit"),
    "jyeffect":   ("高清臻音 Spatial Audio", "96kHz/24bit"),
    "hires":      ("高解析度无损 Hi-Res", "192kHz/24bit"),
    "lossless":   ("无损 (SQ)", "48kHz/16bit"),
    "exhigh":     ("极高 (HQ)", "320kbps"),
    "higher":     ("较高", "192kbps"),
    "standard":   ("标准", "128kbps"),
}
# 命令行可选音质（从高到低）
QUALITY_CHOICES = ["audiovivid", "sky", "jymaster", "jyeffect", "hires",
                   "lossless", "exhigh", "higher", "standard"]
# 音质降级顺序：从高到低逐级尝试（按官方音质层级）
QUALITY_DESC = ["audiovivid", "sky", "jymaster", "jyeffect", "hires",
                "lossless", "exhigh", "higher", "standard"]


def http_request(url, data=None, headers=None, timeout=20):
    """发送 HTTP 请求，统一带上 UA / Referer，返回 (bytes, response)。"""
    h = {
        "User-Agent": UA,
        "Referer": "https://music.163.com/",
        "Accept": "*/*",
        "Connection": "close",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp


# ---------------------------------------------------------------- eapi 加密
EAPI_KEY = "e82ckenh8dichen8"


def aes_ecb_encrypt(data_bytes, key):
    """AES-128-ECB 加密（无填充），使用 openssl 命令实现。

    返回加密后的密文 bytes。openssl 在 Linux/macOS 上预装。
    """
    proc = subprocess.run(
        ["openssl", "enc", "-aes-128-ecb", "-K", key.encode("utf-8").hex(),
         "-nosalt", "-nopad"],
        input=data_bytes, capture_output=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError("openssl AES 加密失败: " + proc.stderr.decode("utf-8", "ignore"))
    return proc.stdout


def eapi_encrypt(eapi_path, params):
    """网易云 eapi 加密（PC/安卓客户端 API）。

    params 为请求参数字典 -> 返回加密后的 hex 大写字符串（用于 POST params）。
    """
    enc_url = "/api" + eapi_path
    params_str = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
    md5_src = "nobody" + enc_url + "use" + params_str + "md5forencrypt"
    md5 = hashlib.md5(md5_src.encode("utf-8")).hexdigest()
    plain = enc_url + "-36cd479b6b5-" + params_str + "-36cd479b6b5-" + md5
    # PKCS#7 填充
    pad = 16 - len(plain.encode("utf-8")) % 16
    data = plain.encode("utf-8") + bytes([pad]) * pad
    enc = aes_ecb_encrypt(data, EAPI_KEY)
    return binascii.hexlify(enc).upper().decode("ascii")


def eapi_request(eapi_path, params, cookie="", timeout=20):
    """调用网易云 eapi 接口，返回解析后的 JSON。"""
    enc = eapi_encrypt(eapi_path, params)
    body = urllib.parse.urlencode({"params": enc}).encode("utf-8")
    raw, _ = http_request(
        "https://music.163.com/eapi" + eapi_path, data=body,
        headers={"Cookie": cookie,
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout)
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------- Cookie 管理
def load_cookie():
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def save_cookie(raw):
    """规范化并保存 Cookie 字符串，保留所有键值对。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    pairs = {}
    for part in raw.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().strip('"').strip()
        v = v.strip().strip('"').strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", k):
            # 只保留看起来像 cookie 名的键
            continue
        pairs[k] = v
    if not pairs:
        raise ValueError("未能从输入中解析出任何 Cookie 键值对")
    out = "; ".join(f"{k}={v}" for k, v in pairs.items())
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(out)
    return out


def get_csrf(cookie=""):
    """从 Cookie 中提取 __csrf。"""
    m = re.search(r"(?:^|;\s*)__csrf=([^;]+)", cookie)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 登录验证
def verify_login():
    """校验已保存的 Cookie 是否有效，返回 (ok, 信息)。"""
    cookie = load_cookie()
    if not cookie:
        return False, "尚未设置 Cookie，请先登录"
    url = ("https://music.163.com/api/nuser/account/get"
           "?timestamp=" + str(int(time.time() * 1000)))
    try:
        body, _ = http_request(url, headers={"Cookie": cookie})
        data = json.loads(body.decode("utf-8"))
    except Exception as e:
        return False, f"请求失败: {e}"
    code = data.get("code")
    profile = data.get("profile")
    account = data.get("account")
    if code == 200 and profile:
        nickname = profile.get("nickname") or f"账号 {account.get('id')}" if account else "未知"
        return True, str(nickname)
    if code == 200:
        # 无效/过期 Cookie 也会返回 code=200 但 account/profile 为 null
        return False, "Cookie 已过期或无效，请重新登录"
    if code == 301:
        return False, "Cookie 已过期或无效，请重新登录"
    return False, f"未知返回码 {code}"


# ---------------------------------------------------------------- 搜索
def search_songs(keyword, limit=30, cookie=""):
    """搜索歌曲，返回歌曲列表。"""
    url = "https://music.163.com/api/search/get"
    data = urllib.parse.urlencode({
        "s": keyword,
        "type": 1,
        "offset": 0,
        "limit": limit,
        "total": "true",
        "csrf_token": get_csrf(cookie),
    }).encode("utf-8")
    body, _ = http_request(url, data=data, headers={
        "Cookie": cookie,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    j = json.loads(body.decode("utf-8"))
    if j.get("code") != 200:
        raise RuntimeError(f"搜索失败（code={j.get('code')}）")
    return j.get("result", {}).get("songs") or []


def song_line(song):
    artists = "/".join(a["name"] for a in song.get("artists") or [])
    album = (song.get("album") or {}).get("name", "")
    dur = song.get("duration") or 0
    mins, secs = divmod(dur // 1000, 60)
    return artists, album, f"{mins:02d}:{secs:02d}"


# ---------------------------------------------------------------- 歌单
def get_playlist(playlist_id, cookie=""):
    """获取歌单信息 + 完整歌曲列表。

    返回 (playlist_info, songs)。
    detail 接口的 tracks 字段只带前 ~10 首，
    完整 ID 列表在 trackIds 里，需要分批补全歌曲信息。
    """
    url = ("https://music.163.com/api/v6/playlist/detail"
           f"?id={playlist_id}&n=100000&csrf_token={get_csrf(cookie)}")
    body, _ = http_request(url, headers={"Cookie": cookie})
    j = json.loads(body.decode("utf-8"))
    if j.get("code") != 200:
        raise RuntimeError(f"获取歌单失败（code={j.get('code')}）")
    pl = j.get("playlist") or {}
    if not pl:
        raise RuntimeError("歌单不存在或为私密歌单")

    tracks = pl.get("tracks") or []
    track_ids = [t.get("id") for t in (pl.get("trackIds") or [])]

    # 若返回的 tracks 不足 trackIds 数量，用 song/detail 分批补全
    if len(tracks) < len(track_ids) and track_ids:
        tracks = _fetch_song_details(track_ids, cookie=cookie)
    return pl, tracks


def _fetch_song_details(ids, cookie="", batch=100):
    """分批调用 song/detail 获取歌曲详情，返回与搜索一致的歌曲结构。"""
    songs = []
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        c = json.dumps([{"id": x} for x in chunk])
        url = ("https://music.163.com/api/v3/song/detail?"
               "c=" + urllib.parse.quote(c))
        body, _ = http_request(url, headers={"Cookie": cookie})
        j = json.loads(body.decode("utf-8"))
        if j.get("code") != 200:
            raise RuntimeError(f"获取歌曲详情失败（code={j.get('code')}）")
        songs.extend(j.get("songs") or [])
        time.sleep(0.3)  # 每批间稍作停顿，避免风控
    return songs


def download_playlist(playlist_id, level="lossless", cookie="", outdir=".", start=0):
    """下载整个歌单，返回 (成功数, 失败数, 总数)。"""
    pl, songs = get_playlist(playlist_id, cookie=cookie)
    name = pl.get("name", f"歌单_{playlist_id}")
    print(f"\n  歌单: {name}（共 {len(songs)} 首）")
    count = pl.get("trackCount") or len(songs)
    if len(songs) < count:
        print(f"  ⚠ 仅能获取 {len(songs)} 首，歌单实际 {count} 首")

    if not songs:
        print("  ✖ 歌单为空\n")
        return 0, 0, 0

    # 可选预览：列出前若干首
    if "T" not in os.environ.get("NCM_NO_PREVIEW", ""):
        preview = 15
        print_song_list(songs[:preview])
        if len(songs) > preview:
            print(f"  ... 共 {len(songs)} 首\n")

    outdir = os.path.join(os.path.abspath(outdir), safe_filename(name))
    ok_n = fail_n = 0
    total = len(songs)
    for i, song in enumerate(songs, 1):
        print(f"  [{i}/{total}] ", end="")
        if download_one(song, level=level, cookie=cookie, outdir=outdir):
            ok_n += 1
        else:
            fail_n += 1
        # 批量下载之间稍作停顿，避免触发风控
        if i < total:
            time.sleep(1.0)
    print(f"  ✔ 完成: 成功 {ok_n} 首，失败 {fail_n} 首")
    return ok_n, fail_n, total


# ---------------------------------------------------------------- 取播放地址
def get_song_url(song_id, level="lossless", cookie=""):
    """通过 eapi v1 接口获取歌曲下载地址。

    level: standard/higher/exhigh/lossless/hires/jyeffect/sky/dolby/jymaster
    歌曲达不到目标级别时接口自动降级（返回实际可用级别）。
    返回 (url, actual_level) 或 (None, None)。
    """
    params = {
        "ids": f"[{song_id}]",
        "level": level,
        "encodeType": "mp3",
        "csrf_token": get_csrf(cookie),
    }
    j = eapi_request("/song/enhance/player/url/v1", params, cookie=cookie)
    data = j.get("data") or []
    if not data:
        return None, None
    item = data[0]
    url = item.get("url")
    actual = item.get("level")

    # 网易在 API 渠道常把 Hi-Res 源标成 lossless（cdntag=os_web,quality_lossless），
    # 但 URL 的实际文件却来自 hrMusic（Hi-Res 源）。用 musicId 对比 hrMusic.id
    # 来识别真实音质，避免误标成"降级为无损"。
    try:
        detail = eapi_request("/song/detail",
                              {"ids": f"[{song_id}]", "csrf_token": get_csrf(cookie)},
                              cookie=cookie)
        song = (detail.get("songs") or [{}])[0]
        hr = song.get("hrMusic") or {}
        if (hr.get("id") is not None and item.get("musicId") is not None
                and str(hr.get("id")) == str(item.get("musicId"))):
            actual = "hires"
    except Exception:
        pass

    # 试听限制（freeTrialInfo）也视为拿不到完整音频
    free_trial = item.get("freeTrialInfo")
    ffp = item.get("freeTimeTrialPrivilege") or {}
    is_trial = bool(free_trial) or bool(
        ffp.get("resConsumable") or ffp.get("userConsumable"))
    if url and is_trial:
        return None, None
    return url, actual


def get_outer_url(song_id):
    """兜底：使用官方外链接口（通常仅试听音质可用）。"""
    return f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"


def check_max_quality(song_id, cookie=""):
    """查询歌曲真实最高可用音质级别。

    网易的 /song/enhance/privilege 接口返回 maxBrLevel，
    是判断歌曲真实最高音质的权威来源（客户端也用它）。
    返回 (max_level, max_br, 完整权限dict) 或 (None, None, None)。
    """
    try:
        j = eapi_request("/song/enhance/privilege",
                         {"ids": f"[{song_id}]", "csrf_token": get_csrf(cookie)},
                         cookie=cookie)
        priv = (j.get("data") or [{}])[0]
        max_level = priv.get("maxBrLevel") or priv.get("plLevel")
        max_br = priv.get("maxbr") or priv.get("pl")
        return max_level, max_br, priv
    except Exception:
        return None, None, None


def check_song_status(song_id, cookie=""):
    """查询歌曲详细权限状态，返回可读的诊断信息字符串。

    优先使用 /song/enhance/privilege 判断真实可用音质，
    返回具体的最高音质级别，避免误导用户。
    """
    max_level, max_br, priv = check_max_quality(song_id, cookie=cookie)
    if max_level and max_level in QUALITY_LEVELS:
        name = QUALITY_LEVELS[max_level][0]
        return f"该歌曲最高仅提供 {name} 音质（{max_br // 1000}k），没有更高音源"
    # 老接口兜底
    c = json.dumps([{"id": song_id}])
    url = ("https://music.163.com/api/v3/song/detail?"
           "c=" + urllib.parse.quote(c))
    try:
        body, _ = http_request(url, headers={"Cookie": cookie})
        j = json.loads(body.decode("utf-8"))
        song = (j.get("songs") or [{}])[0]
        priv = (j.get("privileges") or [{}])[0]

        st = priv.get("st")
        fee = song.get("fee")
        if st == -100:
            return ("歌曲已下架或版权不可用（地区/版权限制），"
                    "VIP 会员也无法播放或下载")
        if fee in (1, 4, 6, 8) and not priv.get("payed"):
            return "歌曲为会员/付费曲目，可能需要开通相应会员或购买"
        if not priv.get("dl"):
            return "当前账号无下载权限，或歌曲禁止下载"
        return "无法获取下载链接（可能受版权保护或接口限制）"
    except Exception:
        return "无法获取下载链接（可能受版权保护或接口限制）"


# ---------------------------------------------------------------- 下载
def safe_filename(name):
    """清理文件名中的非法字符。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "untitled"


def download_song(url, filepath, label=""):
    """流式下载并显示进度，返回是否成功。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://music.163.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(filepath, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        print(f"\r  {label} 已下载 {pct:3d}% "
                              f"({done // 1024}KB/{total // 1024}KB)",
                              end="", flush=True)
                    else:
                        print(f"\r  {label} 已下载 {done // 1024}KB",
                              end="", flush=True)
        print()
        return True
    except Exception as e:
        print(f"\r  {label} 下载失败: {e}")
        return False


def download_one(song, level="lossless", cookie="", outdir="."):
    """下载单曲：优先目标音质，不可用时逐级降级，再不行走外链兜底。

    level 为目标音质级别（如 hires/lossless/exhigh...），
    从目标开始按高->低逐级尝试（jymaster -> ... -> standard）。
    先查询歌曲真实最高音质（maxBrLevel），目标过高时直接降级。
    """
    song_id = song["id"]
    title = f"{song['name']} - {'/'.join(a['name'] for a in song.get('artists') or [])}"
    print(f"  开始下载: {title}")

    # 查询歌曲真实最高可用音质，避免无谓的降级尝试
    max_level, max_br, _ = check_max_quality(song_id, cookie=cookie)
    if max_level in QUALITY_DESC:
        # 目标超过歌曲上限时，直接从歌曲最高音质开始尝试
        cap_idx = QUALITY_DESC.index(max_level)
        target_idx = QUALITY_DESC.index(level) if level in QUALITY_DESC else -1
        if target_idx >= 0 and target_idx < cap_idx:
            from_name = QUALITY_LEVELS.get(level, (level, ''))[0]
            to_name = QUALITY_LEVELS.get(max_level, (max_level, ''))[0]
            print(f"  ℹ {from_name} 超出该歌曲上限，"
                  f"改用最高音质 {to_name}（{max_br // 1000}k）")
            level = max_level

    # 从目标音质开始，按降级顺序逐级尝试
    start = QUALITY_DESC.index(level) if level in QUALITY_DESC else len(QUALITY_DESC) - 1
    tried = QUALITY_DESC[start:]

    url = None
    got_level = None
    for lv in tried:
        url, got_level = get_song_url(song_id, level=lv, cookie=cookie)
        if url:
            break
    if url and got_level != level:
        from_name = QUALITY_LEVELS.get(level, (level, ''))[0]
        to_name = QUALITY_LEVELS.get(got_level, (got_level, ''))[0]
        # 歌曲理论上限高于实际拿到的级别 -> 渠道被限制在 lossless
        if max_level in QUALITY_DESC and QUALITY_DESC.index(max_level) < QUALITY_DESC.index(got_level or 'standard'):
            print(f"  ⚠ {from_name} 需要通过网易云客户端获取"
                  f"（API 渠道最高给到 {to_name}），已降级为 {to_name}")
        else:
            print(f"  ⚠ {from_name} 不可用，降级为 {to_name}")
    if not url:
        # 外链兜底：官方 outer 链接，仅对可直接播放的免费曲目有效
        outer = get_outer_url(song_id)
        try:
            req = urllib.request.Request(outer, headers={
                "User-Agent": UA, "Referer": "https://music.163.com/"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                # 网易对不可播放曲目会重定向到 404 页面，只认音频类型
                if "audio" in ctype or "octet-stream" in ctype \
                        or "mpeg" in ctype or "video" in ctype:
                    url = resp.geturl()
        except Exception:
            url = None

    if not url:
        reason = check_song_status(song_id, cookie=cookie)
        print(f"  ✖ {reason}\n")
        return False

    ext = "mp3"
    low = url.lower()
    if ".flac" in low:
        ext = "flac"
    elif ".m4a" in low:
        ext = "m4a"

    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{safe_filename(title)}.{ext}")
    ok = download_song(url, path, label=safe_filename(title))
    if ok:
        print(f"  ✓ 已保存: {path}\n")
    return ok


# ---------------------------------------------------------------- 交互面板
def print_song_list(songs):
    """打印歌曲列表（带序号）。"""
    print(f"\n  共 {len(songs)} 首:\n")
    for i, s in enumerate(songs, 1):
        artists, album, dur = song_line(s)
        print(f"  [{i:>3}] {s['name']}  --  {artists}  |  {album}  |  {dur}")


def parse_index_selection(text, total):
    """解析用户输入的选择，支持 1,3 或 1-5 或 a(全部)，返回序号列表。"""
    text = text.strip().lower()
    if text in ("a", "all", "*"):
        return list(range(1, total + 1))
    idx = []
    for part in re.split(r"[,\s，、]+", text):
        if not part:
            continue
        if "-" in part and part.count("-") == 1:
            lo, hi = part.split("-")
            if lo.isdigit() and hi.isdigit():
                idx.extend(range(int(lo), int(hi) + 1))
        elif part.isdigit():
            idx.append(int(part))
    idx = [i for i in idx if 1 <= i <= total]
    return sorted(set(idx))


def choose_quality(current_level="lossless"):
    """让用户选择目标音质，返回选中的 level 名。"""
    print("\n  请选择音质（按截图官方命名）:")
    print("  [1] 标准 (standard, 128kbps)")
    print("  [2] 较高 (higher, 192kbps)")
    print("  [3] 极高 HQ (exhigh, 320kbps)")
    print("  [4] 无损 SQ (lossless, 48kHz/16bit)（VIP）")
    print("  [5] 高解析度无损 Hi-Res (hires, 192kHz/24bit)（VIP）")
    print("  [6] 高清臻音 Spatial Audio (jyeffect, 96kHz/24bit)（VIP）")
    print("  [7] 臻音全景声 Audio Vivid (audiovivid, 7.1声道)（SVIP）")
    print("  [8] 沉浸环绕声 Surround Audio (sky, 5.1声道)（SVIP）")
    print("  [9] 超清母带 Master (jymaster, 192kHz/24bit)（SVIP）")
    print("  [0] 保持当前设置")
    choice = input("  请选择 > ").strip()
    mapping = {"1": "standard", "2": "higher", "3": "exhigh", "4": "lossless",
               "5": "hires", "6": "jyeffect", "7": "audiovivid",
               "8": "sky", "9": "jymaster"}
    if choice in mapping:
        return mapping[choice]
    print(f"  保持当前音质（{QUALITY_LEVELS.get(current_level, (current_level, ''))[0]}）")
    return current_level


def interactive():
    """主交互菜单。"""
    print("=" * 56)
    print("  网易云音乐 CLI —— Cookie 登录 / 搜索 / 下载")
    print("=" * 56)
    quality_level = "lossless"
    while True:
        ok, info = verify_login()
        status = f"已登录: {info}" if ok else f"未登录: {info}"
        qname = QUALITY_LEVELS.get(quality_level, (quality_level, 0))[0]
        print(f"\n  [{status}]  当前音质: {qname}")
        print("  [1] 登录（粘贴 Cookie）")
        print("  [2] 搜索并下载")
        print("  [3] 下载歌单")
        print("  [4] 设置音质")
        print("  [5] 退出")
        choice = input("  请选择 > ").strip()
        if choice == "1":
            do_login()
        elif choice == "2":
            do_search_download(quality_level=quality_level)
        elif choice == "3":
            do_playlist_download(quality_level=quality_level)
        elif choice == "4":
            quality_level = choose_quality(quality_level)
        elif choice == "5":
            print("  再见！")
            break
        else:
            print("  无效选择")


def do_login():
    print("\n  请从浏览器复制网易云音乐 Cookie：")
    print("  网页版 music.163.com 登录后，F12 -> 应用/Application -> Cookie，")
    print("  复制整串 Cookie（含 MUSIC_U 和 __csrf）粘贴到下面：\n")
    raw = input("  Cookie > ").strip()
    if not raw:
        print("  ✖ 输入为空\n")
        return
    try:
        saved = save_cookie(raw)
    except ValueError as e:
        print(f"  ✖ {e}\n")
        return
    cookies = dict(p.split("=", 1) for p in saved.split("; "))
    has_uid = "MUSIC_U" in cookies
    print(f"  已保存 {len(cookies)} 个 Cookie 键值对"
          f"{'（包含 MUSIC_U）' if has_uid else ''}")
    ok, info = verify_login()
    if ok:
        print(f"  ✓ 登录成功: {info}\n")
    else:
        print(f"  ✖ {info}\n")


def do_search_download(quality_level="lossless", outdir="."):
    kw = input("  搜索关键词 > ").strip()
    if not kw:
        print("  ✖ 关键词为空\n")
        return
    try:
        songs = search_songs(kw, cookie=load_cookie())
    except Exception as e:
        print(f"  ✖ {e}\n")
        return
    if not songs:
        print("  ✖ 没有找到相关歌曲\n")
        return
    print_song_list(songs)
    sel = input("\n  输入序号下载（逗号分隔/范围如 1-3，a=全部，回车取消）> ").strip()
    if not sel:
        print("  已取消\n")
        return
    idxs = parse_index_selection(sel, len(songs))
    if not idxs:
        print("  ✖ 序号无效\n")
        return
    for i in idxs:
        download_one(songs[i - 1], level=quality_level, cookie=load_cookie(), outdir=outdir)


def do_playlist_download(quality_level="lossless", outdir="."):
    pid = input("  输入歌单 ID（歌单链接中的数字，如 3778678）> ").strip()
    if not pid:
        print("  ✖ 歌单 ID 为空\n")
        return
    if not pid.isdigit():
        # 支持粘贴完整链接，自动提取 ID
        m = re.search(r"\d{5,}", pid)
        if m:
            pid = m.group(0)
        else:
            print("  ✖ 无法从输入中识别歌单 ID\n")
            return
    cookie = load_cookie()
    try:
        download_playlist(int(pid), level=quality_level, cookie=cookie, outdir=outdir)
    except Exception as e:
        print(f"  ✖ {e}\n")


# ---------------------------------------------------------------- 入口
def main():
    parser = argparse.ArgumentParser(
        description="网易云音乐 CLI：Cookie 登录 + 搜索 + 下载（纯标准库）")
    parser.add_argument("--cookie", help="直接粘贴 Cookie 字符串，保存并验证后退出")
    parser.add_argument("--query", "-q", help="搜索关键词并列出歌曲，随后可选择下载")
    parser.add_argument("--playlist", "-p", help="歌单 ID 或歌单链接，下载整个歌单")
    parser.add_argument("--id", type=int, help="按歌曲 ID 直接下载")
    parser.add_argument("--quality", "-b", default="lossless",
                        choices=QUALITY_CHOICES,
                        help="音质级别（按官方命名）：standard/higher/exhigh/"
                             "lossless(SQ)/hires(Hi-Res)/jyeffect(高清臻音)/"
                             "audiovivid(臻音全景声)/sky(沉浸环绕声)/"
                             "jymaster(超清母带)，达不到时自动降级"
                             "（默认 lossless）")
    parser.add_argument("--dir", "-d", default=os.getcwd(), help="下载目录（默认当前目录）")
    parser.add_argument("--limit", type=int, default=20, help="搜索返回数量（默认 20）")
    parser.add_argument("--no-prompt", action="store_true",
                        help="搜索后只列出歌曲，不进入交互选择")
    args = parser.parse_args()

    quality = args.quality

    if args.cookie:
        try:
            saved = save_cookie(args.cookie)
        except ValueError as e:
            print(f"✖ {e}")
            return 1
        ok, info = verify_login()
        if ok:
            print(f"✓ Cookie 已保存，登录成功: {info}")
        else:
            print(f"✖ Cookie 已保存，但验证失败: {info}")
            return 1
        return 0

    if args.playlist:
        pid = args.playlist
        m = re.search(r"\d{5,}", pid)
        if not m:
            print("✖ 无法从输入中识别歌单 ID")
            return 1
        try:
            download_playlist(int(m.group(0)), level=quality,
                              cookie=load_cookie(), outdir=args.dir)
        except Exception as e:
            print(f"✖ {e}")
            return 1
        return 0

    if args.id:
        # 按 ID 直接下载（先查一首占位歌曲信息）
        cookie = load_cookie()
        try:
            songs = search_songs(str(args.id), limit=5, cookie=cookie)
            song = None
            for s in songs:
                if s.get("id") == args.id:
                    song = s
                    break
            if song is None:
                # 搜不到时用最小信息构造
                song = {"id": args.id, "name": f"song_{args.id}",
                        "artists": [{"name": "unknown"}]}
        except Exception as e:
            print(f"✖ 获取歌曲信息失败: {e}")
            song = {"id": args.id, "name": f"song_{args.id}",
                    "artists": [{"name": "unknown"}]}
        if not download_one(song, level=quality, cookie=cookie, outdir=args.dir):
            return 1
        return 0

    if args.query:
        cookie = load_cookie()
        try:
            songs = search_songs(args.query, limit=args.limit, cookie=cookie)
        except Exception as e:
            print(f"✖ {e}")
            return 1
        if not songs:
            print("✖ 没有找到相关歌曲")
            return 1
        print_song_list(songs)
        if args.no_prompt:
            return 0
        if not sys.stdin.isatty():
            print("  非交互终端，跳过选择（加 --no-prompt 静默列出）")
            return 0
        sel = input("\n  输入序号下载（逗号分隔/范围如 1-3，a=全部，回车取消）> ").strip()
        if not sel:
            print("  已取消")
            return 0
        idxs = parse_index_selection(sel, len(songs))
        for i in idxs:
            download_one(songs[i - 1], level=quality, cookie=cookie, outdir=args.dir)
        return 0

    # 无参数：进入交互菜单
    try:
        interactive()
    except KeyboardInterrupt:
        print("\n  已中断，再见！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
