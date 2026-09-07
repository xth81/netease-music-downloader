#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐下载器 - 图形界面版
复用 netease_music.py 的全部后端功能（eapi 加密、音质识别、下载等）。
仅依赖 Python 标准库（tkinter 打包进 Windows 自带）。

打包：pyinstaller -F -w -n 网易云音乐下载器 netease_music_gui.py
"""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

import netease_music as n

APP_TITLE = "网易云音乐下载器"
QUALITY_KEYS = ["audiovivid", "sky", "jymaster", "jyeffect", "hires",
                "lossless", "exhigh", "higher", "standard"]
QUALITY_LABELS = {
    "audiovivid": "臻音全景声 Audio Vivid (SVIP)",
    "sky": "沉浸环绕声 Surround Audio (SVIP)",
    "jymaster": "超清母带 Master (SVIP)",
    "jyeffect": "高清臻音 Spatial Audio (VIP)",
    "hires": "高解析度无损 Hi-Res (VIP)",
    "lossless": "无损 SQ (VIP)",
    "exhigh": "极高 HQ (320kbps)",
    "higher": "较高 (192kbps)",
    "standard": "标准 (128kbps)",
}


class NeteaseGui:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("860x640")
        root.minsize(760, 560)

        self.cookie = n.load_cookie()
        self.songs = []
        self.quality_level = "lossless"
        self.download_dir = os.path.join(os.path.expanduser("~"), "Music")
        self._busy = False

        self._build_ui()
        self._refresh_login_status()

    # ---------------------------------------------------------- UI 构建
    def _build_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        # 登录区
        login_frame = ttk.LabelFrame(main, text="账号登录", padding=6)
        login_frame.grid(row=0, column=0, sticky="ew")
        login_frame.columnconfigure(1, weight=1)
        ttk.Label(login_frame, text="Cookie:").grid(row=0, column=0, sticky="w")
        self.cookie_var = tk.StringVar(value=self.cookie)
        cookie_entry = ttk.Entry(login_frame, textvariable=self.cookie_var)
        cookie_entry.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(login_frame, text="保存并验证登录",
                   command=self.do_login).grid(row=0, column=2, padx=4)
        self.login_status = ttk.Label(login_frame, text="")
        self.login_status.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(login_frame, text="提示：浏览器登录 music.163.com，F12→Application→Cookie 复制整串",
                  foreground="#888").grid(row=2, column=0, columnspan=3, sticky="w")

        # 搜索+下载区
        action_frame = ttk.Frame(main)
        action_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        action_frame.columnconfigure(1, weight=1)

        ttk.Label(action_frame, text="搜索:").grid(row=0, column=0)
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(action_frame, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(action_frame, text="搜索",
                   command=self.do_search).grid(row=0, column=2, padx=2)
        ttk.Button(action_frame, text="下载选中",
                   command=self.do_download_selected).grid(row=0, column=3, padx=2)

        # 歌单区
        pl_frame = ttk.LabelFrame(main, text="歌单下载", padding=6)
        pl_frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        pl_frame.columnconfigure(1, weight=1)
        ttk.Label(pl_frame, text="歌单 ID / 链接:").grid(row=0, column=0)
        self.playlist_var = tk.StringVar()
        pl_entry = ttk.Entry(pl_frame, textvariable=self.playlist_var)
        pl_entry.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(pl_frame, text="下载歌单",
                   command=self.do_download_playlist).grid(row=0, column=2, padx=4)

        # 设置区
        set_frame = ttk.Frame(main)
        set_frame.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        set_frame.columnconfigure(3, weight=1)
        set_frame.rowconfigure(2, weight=1)

        ttk.Label(set_frame, text="音质:").grid(row=0, column=0, sticky="w")
        self.quality_var = tk.StringVar(value=QUALITY_LABELS["lossless"])
        self.quality_combo = ttk.Combobox(
            set_frame, textvariable=self.quality_var, state="readonly", width=38,
            values=[QUALITY_LABELS[k] for k in QUALITY_KEYS])
        self.quality_combo.grid(row=0, column=1, sticky="w", padx=(4, 12))
        self.quality_combo.bind("<<ComboboxSelected>>", self._on_quality_change)

        ttk.Label(set_frame, text="保存目录:").grid(row=0, column=2, sticky="e")
        self.dir_var = tk.StringVar(value=self.download_dir)
        dir_entry = ttk.Entry(set_frame, textvariable=self.dir_var)
        dir_entry.grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Button(set_frame, text="浏览...",
                   command=self.choose_dir).grid(row=0, column=4, padx=4)

        # 进度条
        self.progress = ttk.Progressbar(set_frame, mode="determinate")
        self.progress.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(6, 2))
        self.progress_label = ttk.Label(set_frame, text="")
        self.progress_label.grid(row=2, column=0, columnspan=5, sticky="w")

        # 歌曲列表
        list_frame = ttk.Frame(main)
        list_frame.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        cols = ("idx", "name", "artist", "album", "dur", "fee")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings",
                                 selectmode="extended")
        headers = [("idx", "序", 50), ("name", "歌名", 240), ("artist", "歌手", 160),
                   ("album", "专辑", 180), ("dur", "时长", 60), ("fee", "权限", 50)]
        for cid, text, width in headers:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor="w",
                             stretch=(cid in ("name", "artist", "album")))
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda e: self.do_download_selected())

        # 日志区
        log_frame = ttk.LabelFrame(main, text="日志", padding=4)
        log_frame.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        self.log = scrolledtext.ScrolledText(log_frame, height=9, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

    # ---------------------------------------------------------- 日志/线程
    def log_msg(self, msg):
        """线程安全写日志。"""
        def _write():
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(0, _write)

    def run_bg(self, task, *args, **kwargs):
        """后台线程执行，防止界面卡死。"""
        if self._busy:
            self.log_msg("※ 有任务正在执行，请稍候")
            return
        self._busy = True
        self.progress.configure(mode="indeterminate")
        self.progress.start(10)

        def wrapper():
            try:
                task(*args, **kwargs)
            except Exception as e:
                self.log_msg(f"✖ 出错: {e}")
            finally:
                self.root.after(0, self._task_done)

        threading.Thread(target=wrapper, daemon=True).start()

    def _task_done(self):
        self._busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.progress_label.configure(text="")

    def set_progress(self, value, label=""):
        self.root.after(0, lambda: (
            self.progress.configure(mode="determinate"),
            self.progress.configure(value=value),
            self.progress_label.configure(text=label)))

    # ---------------------------------------------------------- 动作
    def _refresh_login_status(self):
        ok, info = n.verify_login()
        if ok:
            self.login_status.configure(text=f"✓ 已登录: {info}", foreground="#0a0")
        else:
            self.login_status.configure(text=f"✖ 未登录: {info}", foreground="#a00")

    def do_login(self):
        raw = self.cookie_var.get().strip()
        if not raw:
            messagebox.showwarning("提示", "请先粘贴 Cookie")
            return
        try:
            saved = n.save_cookie(raw)
            self.cookie = saved
            self.log_msg(f"✓ Cookie 已保存（{len(saved.split(';'))} 项）")
        except ValueError as e:
            messagebox.showerror("错误", str(e))
            return
        ok, info = n.verify_login()
        self._refresh_login_status()
        self.log_msg(f"✓ 登录验证: {info}" if ok else f"✖ 登录验证失败: {info}")

    def do_search(self):
        kw = self.search_var.get().strip()
        if not kw:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        self.log_msg(f"🔍 搜索: {kw}")
        self.run_bg(self._search_task, kw)

    def _search_task(self, kw):
        songs = n.search_songs(kw, cookie=self.cookie)
        self.root.after(0, self._show_songs, songs)
        self.log_msg(f"✓ 搜索到 {len(songs)} 首")

    def _show_songs(self, songs):
        self.songs = songs
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(songs, 1):
            artists = "/".join(a["name"] for a in (s.get("ar") or s.get("artists") or []))
            album = (s.get("al") or s.get("album") or {}).get("name", "")
            dur = s.get("duration") or 0
            mins, secs = divmod(dur // 1000, 60)
            fee = s.get("fee", 0)
            fee_txt = "免费" if fee == 0 else ("VIP" if fee in (1, 4, 6, 8) else str(fee))
            self.tree.insert("", "end", values=(i, s.get("name", ""), artists,
                                                album, f"{mins:02d}:{secs:02d}", fee_txt))

    def do_download_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在列表中选择歌曲")
            return
        idxs = [int(self.tree.item(i)["values"][0]) for i in sel]
        self.log_msg(f"▶ 开始下载 {len(idxs)} 首（{QUALITY_LABELS[self.quality_level]}）")
        self.run_bg(self._download_task, idxs)

    def _download_task(self, idxs):
        outdir = self.download_dir
        total = len(idxs)
        for k, i in enumerate(idxs, 1):
            song = self.songs[i - 1]
            self.set_progress(k * 100 // total, f"({k}/{total}) {song.get('name', '')}")
            self.log_msg(f"  下载: {song.get('name', '')} - "
                         f"{'/'.join(a['name'] for a in (song.get('ar') or song.get('artists') or []))}")
            try:
                n.download_one(song, level=self.quality_level,
                               cookie=self.cookie, outdir=outdir)
            except Exception as e:
                self.log_msg(f"  ✖ {song.get('name')} 失败: {e}")
        self.log_msg(f"✔ 本批完成（{total} 首） → {outdir}")

    def do_download_playlist(self):
        pid = self.playlist_var.get().strip()
        if not pid:
            messagebox.showwarning("提示", "请输入歌单 ID 或链接")
            return
        m = __import__("re").search(r"\d{5,}", pid)
        if not m:
            messagebox.showerror("错误", "无法识别歌单 ID")
            return
        pid = m.group(0)
        self.log_msg(f"▶ 下载歌单 {pid}")
        self.run_bg(self._playlist_task, int(pid))

    def _playlist_task(self, pid):
        pl, songs = n.get_playlist(pid, cookie=self.cookie)
        name = pl.get("name", f"歌单_{pid}")
        self.log_msg(f" 歌单: {name}（{len(songs)} 首）")
        outdir = os.path.join(self.download_dir, n.safe_filename(name))
        total = len(songs)
        ok_n = fail_n = 0
        for i, song in enumerate(songs, 1):
            self.set_progress(i * 100 // total, f"({i}/{total}) {song.get('name', '')}")
            try:
                if n.download_one(song, level=self.quality_level,
                                  cookie=self.cookie, outdir=outdir):
                    ok_n += 1
                else:
                    fail_n += 1
            except Exception:
                fail_n += 1
            time.sleep(0.5)
        self.log_msg(f"✔ 歌单完成: 成功 {ok_n} 首，失败 {fail_n} 首 → {outdir}")

    def on_download_progress(self, song_id, done, total):
        pass  # 预留：单曲进度回调（当前 download_one 内部打印进度）

    def choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.download_dir)
        if chosen:
            self.download_dir = chosen
            self.dir_var.set(chosen)

    def _on_quality_change(self, _event=None):
        # 由下拉框 value 反查 key
        for k in QUALITY_KEYS:
            if QUALITY_LABELS[k] == self.quality_var.get():
                self.quality_level = k
                break
        self.log_msg(f"音质切换为: {QUALITY_LABELS[self.quality_level]}")


def main():
    root = tk.Tk()
    try:
        # 部分系统主题
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except Exception:
        pass
    NeteaseGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
