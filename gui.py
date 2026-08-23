#!/usr/bin/env python3
"""证件照批量处理 GUI"""

import os
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageOps

from process import process_image_to_bytes, process_image, detect_face, compress_to_bytes

# 标记：某图已检测过但无人脸（缓存用）
_NO_FACE = object()

MAX_PREVIEW_SRC = 900  # 预览处理/绘制的源图最长边上限（大幅提速，构图与批量一致）


def _scale_face_result(face_r, sc):
    """把 detect_face() 的结果按 sc 等比缩放到降采样图上（构图不变）。"""
    if face_r is None:
        return None
    cx, cy, fh, ang, info = face_r
    info2 = {k: tuple(v * sc for v in info[k]) for k in ("bbox", "head", "eyes", "mouth")}
    info2["top_y"] = info["top_y"] * sc
    info2["chin_y"] = info["chin_y"] * sc
    info2["keypoints"] = [(x * sc, y * sc) for x, y in info.get("keypoints", [])]
    return (cx * sc, cy * sc, fh * sc, ang, info2)

# ── 常量 ────────────────────────────────────────────────

SUPPORTED_EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}

PRESET_SIZES = {
    "默认 190×260":             (190, 260),
    "小1寸 (22×32mm) 260×378": (260, 378),
    "1寸 (25×35mm) 295×413":   (295, 413),
    "大一寸 (33×48mm) 390×567": (390, 567),
    "小2寸 (35×45mm) 413×531": (413, 531),
    "2寸 (35×49mm) 413×579":   (413, 579),
    "大2寸 (35×53mm) 413×626": (413, 626),
    "自定义": None,
}

MAX_PREVIEW_W = 380
MAX_PREVIEW_H = 480


# ── 工具 ────────────────────────────────────────────────

def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def _calc_display(img_size, max_w, max_h):
    w, h = img_size
    ratio = min(max_w / w, max_h / h, 1.0)
    return int(w * ratio), int(h * ratio)


def _parse_kb(text: str) -> int:
    try:
        return int(text.strip().split()[0])
    except (ValueError, TypeError):
        return 20


# ── 主窗口 ──────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("证件照批量处理工具")
        self.geometry("1150x720")
        self.minsize(900, 600)

        # 状态
        self.image_infos: list[dict] = []
        self.selected_idx = -1
        self.is_processing = False
        self.current_dims = (190, 260)
        self.face_cache: dict[str, object] = {}  # path → detect_face() 结果或 _NO_FACE
        self._item_frames: list = []

        # 构建 UI
        self._built = False
        self._build_ui()
        self._built = True

        # 初始值
        self.kb_var.set("20")
        self.size_var.set("默认 190×260")
        self._on_change_size("默认 190×260")

    # ── 构建 UI ────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_top()
        self._build_settings()
        self._build_main()
        self._build_bottom()

    def _build_top(self):
        f = ctk.CTkFrame(self)
        f.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew")
        f.grid_columnconfigure(1, weight=1)
        f.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(f, text="输入:").grid(row=0, column=0, padx=(10, 3))
        self.in_entry = ctk.CTkEntry(f, placeholder_text="选择输入文件夹，或点击右侧添加图片…")
        self.in_entry.grid(row=0, column=1, columnspan=2, padx=3, sticky="ew")
        ctk.CTkButton(f, text="浏览", width=65, command=self._sel_in_dir).grid(row=0, column=3, padx=3)
        ctk.CTkButton(f, text="添加图片", width=85, command=self._add_dialog).grid(row=0, column=4, padx=(3, 10))

        ctk.CTkLabel(f, text="输出:").grid(row=1, column=0, padx=(10, 3))
        self.out_entry = ctk.CTkEntry(f, placeholder_text="选择输出文件夹…")
        self.out_entry.grid(row=1, column=1, columnspan=2, padx=3, sticky="ew")
        ctk.CTkButton(f, text="浏览", width=65, command=self._sel_out_dir).grid(row=1, column=3, padx=3)

    def _build_settings(self):
        f = ctk.CTkFrame(self)
        f.grid(row=1, column=0, padx=10, pady=5, sticky="ew")

        ctk.CTkLabel(f, text="尺寸:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(10, 2))
        self.size_var = ctk.StringVar()
        self.size_menu = ctk.CTkOptionMenu(
            f, values=list(PRESET_SIZES.keys()),
            variable=self.size_var, command=self._on_change_size,
            width=230, dynamic_resizing=False,
        )
        self.size_menu.pack(side="left", padx=2)

        ctk.CTkLabel(f, text="  自定义:").pack(side="left", padx=(10, 1))
        self.cw_entry = ctk.CTkEntry(f, width=55, placeholder_text="宽")
        self.cw_entry.pack(side="left", padx=1)
        ctk.CTkLabel(f, text="×").pack(side="left")
        self.ch_entry = ctk.CTkEntry(f, width=55, placeholder_text="高")
        self.ch_entry.pack(side="left", padx=1)
        ctk.CTkLabel(f, text="px").pack(side="left")

        ctk.CTkLabel(f, text="  最大文件:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(20, 2))
        self.kb_var = ctk.StringVar(value="20")
        self.kb_entry = ctk.CTkEntry(f, width=70, textvariable=self.kb_var)
        self.kb_entry.pack(side="left", padx=2)
        ctk.CTkLabel(f, text="KB").pack(side="left")

        # 自定义尺寸输入变更时更新预览
        self.cw_entry.bind("<KeyRelease>", self._on_custom_keyup)
        self.ch_entry.bind("<KeyRelease>", self._on_custom_keyup)
        self.kb_entry.bind("<KeyRelease>", lambda e: self._update_preview()
                           if self.selected_idx >= 0 else None)

    def _build_main(self):
        f = ctk.CTkFrame(self)
        f.grid(row=2, column=0, padx=10, pady=5, sticky="nsew")
        f.grid_columnconfigure(1, weight=1)
        f.grid_rowconfigure(0, weight=1)

        # 图片列表（左）
        left = ctk.CTkFrame(f, width=260)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(left, text="图片列表",
                      font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(8, 0))
        hint = "点击「添加图片」按钮添加图片"
        ctk.CTkLabel(left, text=hint, font=ctk.CTkFont(size=11),
                      text_color="gray").pack()

        self.list_scroll = ctk.CTkScrollableFrame(left)
        self.list_scroll.pack(fill="both", expand=True, padx=5, pady=5)

        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(pady=5)
        ctk.CTkButton(btn_row, text="清空列表", command=self._clear, width=80).pack(side="left", padx=3)
        ctk.CTkButton(btn_row, text="全选", command=self._select_all, width=60).pack(side="left", padx=3)

        # 预览（右）
        right = ctk.CTkFrame(f)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure((0, 1), weight=1)
        right.grid_rowconfigure(1, weight=1)

        nav = ctk.CTkFrame(right, fg_color="transparent")
        nav.grid(row=0, column=0, columnspan=2, pady=5)
        self.prev_btn = ctk.CTkButton(nav, text="◀", width=30, state="disabled",
                                       command=self._prev)
        self.prev_btn.pack(side="left", padx=2)
        self.nav_lbl = ctk.CTkLabel(nav, text="未选图片", width=180)
        self.nav_lbl.pack(side="left", padx=8)
        self.next_btn = ctk.CTkButton(nav, text="▶", width=30, state="disabled",
                                       command=self._next)
        self.next_btn.pack(side="left", padx=2)

        # 原图
        of = ctk.CTkFrame(right)
        of.grid(row=1, column=0, padx=4, pady=(0, 5), sticky="nsew")
        of.grid_rowconfigure(0, weight=1)
        oi = ctk.CTkFrame(of, fg_color="transparent")
        oi.grid(row=0, column=0)
        oi.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(oi, text="原图", font=ctk.CTkFont(size=13, weight="bold")).pack()
        self.orig_lbl = ctk.CTkLabel(oi, text="")
        self.orig_lbl.pack(padx=5, pady=5)
        self.orig_info = ctk.CTkLabel(oi, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.orig_info.pack()

        # 处理后
        pf = ctk.CTkFrame(right)
        pf.grid(row=1, column=1, padx=4, pady=(0, 5), sticky="nsew")
        pf.grid_rowconfigure(0, weight=1)
        pi = ctk.CTkFrame(pf, fg_color="transparent")
        pi.grid(row=0, column=0)
        pi.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(pi, text="处理后", font=ctk.CTkFont(size=13, weight="bold")).pack()
        self.proc_lbl = ctk.CTkLabel(pi, text="")
        self.proc_lbl.pack(padx=5, pady=5)
        self.proc_info = ctk.CTkLabel(pi, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.proc_info.pack()

    def _build_bottom(self):
        f = ctk.CTkFrame(self)
        f.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="ew")
        f.grid_columnconfigure(2, weight=1)

        self.go_btn = ctk.CTkButton(f, text="开始处理", width=110, height=32,
                                     command=self._process_all)
        self.go_btn.grid(row=0, column=0, padx=10, pady=8)

        self.pbar = ctk.CTkProgressBar(f, width=200)
        self.pbar.grid(row=0, column=1, padx=5, pady=8)
        self.pbar.set(0)

        self.status_lbl = ctk.CTkLabel(f, text="就绪", anchor="w")
        self.status_lbl.grid(row=0, column=2, padx=5, pady=8, sticky="ew")

    # ── 读取当前输出尺寸 ────────────────────────────────

    def _read_dims(self):
        """从当前预设或自定义输入框中读取尺寸。"""
        choice = self.size_var.get()
        dims = PRESET_SIZES.get(choice)
        if dims is None:  # 自定义
            try:
                return (int(self.cw_entry.get()), int(self.ch_entry.get()))
            except (ValueError, TypeError):
                return (190, 260)
        return dims

    def _on_custom_keyup(self, event=None):
        """自定义输入框内容变化时更新 current_dims 与预览。"""
        if self.size_var.get() != "自定义":
            return
        try:
            tw = int(self.cw_entry.get())
            th = int(self.ch_entry.get())
        except (ValueError, TypeError):
            return
        self.current_dims = (tw, th)
        if self.selected_idx >= 0:
            self._update_preview()

    # ── 文件管理 ────────────────────────────────────────

    def _sel_in_dir(self):
        d = filedialog.askdirectory(title="选择输入文件夹")
        if not d:
            return
        self.in_entry.delete(0, "end")
        self.in_entry.insert(0, d)
        self._load_dir(d)

    def _sel_out_dir(self):
        d = filedialog.askdirectory(title="选择输出文件夹")
        if d:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, d)

    def _add_dialog(self):
        files = filedialog.askopenfilenames(
            title="选择图片",
            filetypes=[("图片文件", " ".join(f"*{e}" for e in SUPPORTED_EXT)),
                       ("所有文件", "*.*")],
        )
        if files:
            self._add_files(files)

    def _add_files(self, paths):
        added = 0
        known = {info["path"] for info in self.image_infos}
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            if ext not in SUPPORTED_EXT or p in known:
                continue
            try:
                self.image_infos.append({
                    "path": p,
                    "name": os.path.basename(p),
                    "size": os.path.getsize(p),
                    "status": "pending",
                })
                added += 1
            except OSError:
                pass

        if added:
            self._refresh_list()
            if self.selected_idx == -1:
                self._select(0)

    def _load_dir(self, path):
        self.image_infos.clear()
        self.face_cache.clear()
        p = Path(path)
        for f in sorted(p.iterdir(), key=lambda x: x.name):
            if f.suffix.lower() in SUPPORTED_EXT and f.is_file():
                self.image_infos.append({
                    "path": str(f),
                    "name": f.name,
                    "size": f.stat().st_size,
                    "status": "pending",
                })
        self._refresh_list()
        self._clear_preview()
        if self.image_infos:
            self._select(0)

    def _clear(self):
        self.image_infos.clear()
        self.face_cache.clear()
        self.selected_idx = -1
        self._refresh_list()
        self._clear_preview()

    def _select_all(self):
        if self.image_infos:
            self._select(0)

    # ── 图片列表 UI ────────────────────────────────────

    def _refresh_list(self):
        for w in self.list_scroll.winfo_children():
            w.destroy()
        self._item_frames = []

        if not self.image_infos:
            ctk.CTkLabel(self.list_scroll, text="暂无图片",
                          text_color="gray").pack(pady=30)
            return

        for i, info in enumerate(self.image_infos):
            self._make_item(i, info)

    def _set_highlight(self):
        """仅更新选中高亮，避免每次点击都重建整列控件."""
        for i, item in enumerate(self._item_frames):
            item.configure(fg_color=("#d0e4f5", "#2a4a6a") if i == self.selected_idx
                           else getattr(item, "_default_fg", None))

    def _make_item(self, idx, info):
        item = ctk.CTkFrame(self.list_scroll)
        item.pack(fill="x", padx=2, pady=1)
        item.grid_columnconfigure(2, weight=1)
        item._default_fg = item.cget("fg_color")   # 记住默认色，供高亮切换恢复
        self._item_frames.append(item)

        if idx == self.selected_idx:
            item.configure(fg_color=("#d0e4f5", "#2a4a6a"))

        st_map = {"pending": "○", "processing": "◎", "success": "✓",
                  "fail": "✗", "skip": "–"}
        st_color = {"pending": "gray", "processing": "#3399FF",
                    "success": "green", "fail": "red", "skip": "orange"}
        st = info.get("status", "pending")

        ctk.CTkLabel(item, text=st_map.get(st, "○"),
                      text_color=st_color.get(st, "gray"),
                      font=ctk.CTkFont(size=14)).grid(row=0, column=0, padx=3)

        ctk.CTkLabel(item, text=info["name"], anchor="w").grid(
            row=0, column=1, padx=2, sticky="w")
        ctk.CTkLabel(item, text=_fmt_size(info["size"]), width=55,
                      anchor="e", font=ctk.CTkFont(size=11)).grid(
            row=0, column=3, padx=3)

        def on_click(e, i=idx):
            self._select(i)
        item.bind("<Button-1>", on_click)
        for c in item.winfo_children():
            c.bind("<Button-1>", on_click)

    def _select(self, idx):
        if idx < 0 or idx >= len(self.image_infos):
            return
        self.selected_idx = idx
        self._set_highlight()
        self._update_nav()
        self._update_preview()

    def _update_nav(self):
        n = len(self.image_infos)
        if n == 0:
            self.nav_lbl.configure(text="未选图片")
            self.prev_btn.configure(state="disabled")
            self.next_btn.configure(state="disabled")
            return
        self.nav_lbl.configure(text=f"{self.selected_idx + 1} / {n}")
        self.prev_btn.configure(state="normal" if self.selected_idx > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.selected_idx < n - 1 else "disabled")

    def _prev(self):
        if self.selected_idx > 0:
            self._select(self.selected_idx - 1)

    def _next(self):
        if self.selected_idx < len(self.image_infos) - 1:
            self._select(self.selected_idx + 1)

    def _clear_preview(self):
        self.orig_lbl.configure(image="", text="")
        self.proc_lbl.configure(image="", text="")
        self.orig_info.configure(text="")
        self.proc_info.configure(text="")
        self.nav_lbl.configure(text="未选图片")
        self.prev_btn.configure(state="disabled")
        self.next_btn.configure(state="disabled")

    # ── 预览 ───────────────────────────────────────────

    def _update_preview(self):
        if self.selected_idx < 0:
            return
        info = self.image_infos[self.selected_idx]

        try:
            pil = Image.open(info["path"])
            pil = ImageOps.exif_transpose(pil) or pil
        except Exception as e:
            self.orig_lbl.configure(text=f"无法加载: {e}")
            return

        # 预览统一在 ≤MAX_PREVIEW_SRC 的图上完成（坐标按比例缩放；构图与批量一致）
        longest = max(pil.size)
        if longest > MAX_PREVIEW_SRC:
            sc = MAX_PREVIEW_SRC / longest
            disp = pil.resize((round(pil.width * sc), round(pil.height * sc)), Image.LANCZOS)
        else:
            sc = 1.0
            disp = pil

        # 原图 + 人脸框（复用缓存的人脸检测，避免每次预览重复算）
        orig_disp = disp.copy()
        cached = self.face_cache.get(info["path"])
        if cached is None:
            try:
                cached = detect_face(pil) or _NO_FACE
            except Exception:
                cached = _NO_FACE  # 模型缺失等异常不阻断预览
            self.face_cache[info["path"]] = cached
        face_r = None if cached is _NO_FACE else cached
        if face_r:
            _, _, _, _, det = face_r
            draw = ImageDraw.Draw(orig_disp)
            hx1, hy1, hx2, hy2 = [v * sc for v in (det.get("head") or det["bbox"])]
            draw.rectangle([hx1, hy1, hx2, hy2], outline="#00DD00", width=3)
            r = max(2, round(4 * sc))
            for px, py in det.get("keypoints", []):
                draw.ellipse([px * sc - r, py * sc - r, px * sc + r, py * sc + r], fill="#FF3333")

        ds = _calc_display(disp.size, MAX_PREVIEW_W, MAX_PREVIEW_H)
        ctk_img = ctk.CTkImage(orig_disp, size=ds)
        self.orig_lbl.configure(image=ctk_img, text="")
        self.orig_info.configure(
            text=f"{info['name']}  |  {pil.width}×{pil.height}  |  {_fmt_size(info['size'])}")

        # 处理后（在降采样图上处理，构图与批量一致）
        try:
            # 每次都直接从当前来源读取尺寸（修复自定义尺寸不生效的 bug）
            tw, th = self._read_dims()
            max_kb = _parse_kb(self.kb_var.get())
            processed = process_image(disp, target_w=tw, target_h=th,
                                      face_result=_scale_face_result(face_r, sc))
            data = compress_to_bytes(processed, max_size_kb=max_kb,
                                     target_w=tw, target_h=th)
            ds2 = _calc_display((tw, th), MAX_PREVIEW_W, MAX_PREVIEW_H)
            ctk_p = ctk.CTkImage(processed, size=ds2)
            self.proc_lbl.configure(image=ctk_p, text="")
            self.proc_info.configure(
                text=f"{tw}×{th}  |  {_fmt_size(len(data))}")
        except Exception as e:
            self.proc_lbl.configure(text=f"处理失败: {e}")
            self.proc_info.configure(text="")

    # ── 设置变更 ───────────────────────────────────────

    def _on_change_size(self, choice):
        if not self._built:
            return
        dims = PRESET_SIZES.get(choice)
        if dims is None:  # 自定义
            self.cw_entry.configure(state="normal")
            self.ch_entry.configure(state="normal")
            self.current_dims = self._read_dims()
        else:
            tw, th = dims
            self.cw_entry.configure(state="normal")
            self.cw_entry.delete(0, "end")
            self.cw_entry.insert(0, str(tw))
            self.ch_entry.configure(state="normal")
            self.ch_entry.delete(0, "end")
            self.ch_entry.insert(0, str(th))
            self.cw_entry.configure(state="disabled")
            self.ch_entry.configure(state="disabled")
            self.current_dims = dims

        if self.selected_idx >= 0:
            self._update_preview()

    # ── 批量处理 ───────────────────────────────────────

    def _process_all(self):
        if self.is_processing:
            return
        if not self.image_infos:
            messagebox.showinfo("提示", "请先添加图片")
            return

        out_dir = self.out_entry.get().strip()
        if not out_dir:
            messagebox.showinfo("提示", "请先选择输出文件夹")
            return

        # 处理前重新读取当前尺寸（修复自定义不生效的 bug）
        tw, th = self._read_dims()
        self.current_dims = (tw, th)

        Path(out_dir).mkdir(parents=True, exist_ok=True)

        # 预检：输出文件夹是否可写（避免整批 Permission denied）
        try:
            probe = Path(out_dir) / ".write_test"
            probe.write_bytes(b"")
            probe.unlink(missing_ok=True)
        except OSError:
            messagebox.showwarning(
                "输出文件夹无法写入",
                f"无法向「{out_dir}」写入文件（可能是只读 / OneDrive 同步 / 权限问题）。\n"
                f"请换一个普通文件夹（例如在桌面上新建一个空文件夹）后再试。")
            return

        self.is_processing = True
        self.go_btn.configure(text="处理中…", state="disabled")
        self.pbar.set(0)

        max_kb = _parse_kb(self.kb_var.get())

        threading.Thread(target=self._process_thread,
                         args=(out_dir, tw, th, max_kb),
                         daemon=True).start()

    def _process_thread(self, out_dir, tw, th, max_kb):
        total = len(self.image_infos)
        ok = 0

        for i, info in enumerate(self.image_infos):
            try:
                info["status"] = "processing"
                self._update_list()
                self._set_status(f"处理中 [{i + 1}/{total}] {info['name']}")
                self._set_progress(i / max(total, 1))

                img = Image.open(info["path"])
                data = process_image_to_bytes(img, target_w=tw, target_h=th,
                                              max_size_kb=max_kb)

                out_name = Path(info["name"]).with_suffix(".jpg").name
                (Path(out_dir) / out_name).write_bytes(data)

                info["status"] = "success"
                ok += 1
            except Exception as e:
                info["status"] = "fail"
                print(f"失败 {info['name']}: {e}")

            self._update_list()

        self._set_progress(1.0)
        fail = total - ok
        self._set_status(f"完成: 成功 {ok}, 失败 {fail}")
        self.after(0, self._process_done)

    def _process_done(self):
        self.is_processing = False
        self.go_btn.configure(text="开始处理", state="normal")

    def _set_progress(self, val):
        self.after(0, lambda: self.pbar.set(val))

    def _set_status(self, text):
        self.after(0, lambda: self.status_lbl.configure(text=text))

    def _update_list(self):
        self.after(0, self._refresh_list)


# ── 入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
