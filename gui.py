#!/usr/bin/env python3
"""证件照批量处理 —— 桌面外壳。

窗口由 pywebview 创建（Windows 上是 WinForms 宿主 + 内嵌 WebView2 原生窗口，
由系统提供浏览器内核，不随包分发）。界面是 web/ 下的静态页面，通过 js_api
调回本进程；进度用 evaluate_js 推回前端。

入口文件名保持 gui.py 不变 —— PyInstaller spec、Inno Setup 的 AppExeName 与
CI 的 APP_NAME 都依赖它。
"""

import base64
import ctypes
import io
import logging
import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path

# ── 单实例守卫 ──────────────────────────────────────────
# 必须放在 webview（pythonnet / clr）与 process（opencv）等重导入之前：
# 安装版启动时这些导入要花明显时间，低配机上用户等不及会反复双击图标，
# 从而开出多个窗口各处理一遍。这里在最早的时机拦掉重复启动。
_MUTEX_NAME = "IDPhotoProcessor_SingleInstance_v1"
_WINDOW_TITLE = "证件照批量处理工具"
_mutex_handle = None  # 必须由模块级变量持有到进程结束，否则互斥体提前释放
_DND_ERROR = None     # 拖拽注册失败的原因（None 表示成功），供自检与排障用


def _ensure_single_instance():
    """已有实例在运行时，把它的窗口切到前台并结束本进程。"""
    global _mutex_handle
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    except Exception:
        return  # 非 Windows 或调用失败：不阻断启动

    _mutex_handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not _mutex_handle or kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return

    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        hwnd = user32.FindWindowW(None, _WINDOW_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        else:
            # 第一个实例还在启动中，明确告诉用户"已经在启动了"，避免他以为没反应
            user32.MessageBoxW(None, "程序正在启动，请稍候…\n（无需重复打开）",
                               _WINDOW_TITLE, 0x40 | 0x00010000)  # MB_ICONINFORMATION | MB_SETFOREGROUND
    except Exception:
        pass
    sys.exit(0)


import webview  # noqa: E402
from PIL import Image, ImageDraw, ImageOps  # noqa: E402

from process import (DEFAULT_H, DEFAULT_W, check_face_model, compress_to_bytes,  # noqa: E402
                     detect_face, process_image, process_image_to_bytes,
                     stretch_factor, stretch_image)

# 标记：某图已检测过但无人脸（缓存用）
_NO_FACE = object()

MAX_PREVIEW_SRC = 900      # 预览处理/绘制的源图最长边上限（大幅提速，构图与批量一致）
MAX_PREVIEW_W = 460        # 预览框显示上限（前端再按容器缩放）
MAX_PREVIEW_H = 560
FACE_CACHE_MAX = 32        # 人脸检测结果缓存条数（按 路径+拉伸倍率 分桶）

SUPPORTED_EXT = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}

# 扫描输入文件夹时跳过的系统/隐藏目录，以及总数上限（防止误选 C:\ 之类的根目录）
_SKIP_DIRS = {'$recycle.bin', 'system volume information', '__recycle',
              'recycler', 'windows', 'program files', 'program files (x86)',
              'programdata', 'appdata', '$windows.~s', '$windows.~ws', '$windows.~bt',
              'node_modules', '.git', '.svn', '.hg', '__pycache__'}
MAX_SCAN_FILES = 5000

PRESET_SIZES = {
    "默认 190×260":             (190, 260),
    "小1寸 (22×32mm) 260×378": (260, 378),
    "1寸 (25×35mm) 295×413":   (295, 413),
    "大一寸 (33×48mm) 390×567": (390, 567),
    "小2寸 (35×45mm) 413×531": (413, 531),
    "2寸 (35×49mm) 413×579":   (413, 579),
    "大2寸 (35×53mm) 413×635": (413, 635),
    "自定义":                   None,
}

# 源图比例校正的预设比值（语义：这张源图本来应该是这个宽高比）
RATIO_PRESETS = [
    {"label": "3:4", "value": 0.75},
    {"label": "2:3", "value": 0.6667},
    {"label": "4:5", "value": 0.8},
    {"label": "9:16", "value": 0.5625},
    {"label": "1:1", "value": 1.0},
]


# ── 工具 ────────────────────────────────────────────────

def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _parse_kb(text) -> int:
    try:
        return max(1, int(str(text).strip().split()[0]))
    except (ValueError, TypeError, IndexError):
        return 20


def _fit(img: Image.Image, max_w: int, max_h: int, allow_upscale: bool = False) -> Image.Image:
    """等比缩到框内。allow_upscale=False 时只缩不放（原图预览用）。"""
    w, h = img.size
    r = min(max_w / w, max_h / h)
    if not allow_upscale:
        r = min(r, 1.0)
    if abs(r - 1.0) < 0.01:
        return img
    return img.resize((max(1, round(w * r)), max(1, round(h * r))), Image.LANCZOS)


def _data_url(img: Image.Image) -> str:
    """编码成 data URL。预览图很小（几百像素），走 data URL 不需要临时文件，
    打包后也不会因为写入只读目录而失败。"""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _web_root() -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "web")


def _setup_logging() -> None:
    """可选的文件日志：设 IDPHOTO_LOG=<路径> 时把关键日志写到该文件。

    打包版是 console=False，stdout / stderr 会被完全丢弃，出了问题（比如
    pywebview 建 API 表失败）在客户机上看不到任何线索。排查时设一下这个
    环境变量就能拿到日志，平时零开销。

    ⚠️ root 只开到 WARNING：开 DEBUG 会把 PIL / numpy 的逐块解码日志也灌进来，
    一次使用就能撑出很大的文件。只把 pywebview（建窗口与 API 表）和我们自己的
    logger 开到 DEBUG。
    """
    path = os.environ.get("IDPHOTO_LOG")
    if not path:
        return
    try:
        import logging

        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.setLevel(logging.WARNING)
        root.addHandler(handler)
        for name in ("pywebview", "idphoto"):
            logging.getLogger(name).setLevel(logging.DEBUG)
    except Exception:
        pass


def _check_webview2() -> bool:
    """系统里有没有 WebView2 运行时。

    这是本程序唯一的外部依赖 —— pywebview 靠它渲染界面（浏览器内核由系统提供，
    不随包分发）。Win11 与 Win10 1803+ 都预装，但 LTSC / 精简版 / 长期离线的
    机器可能没有。

    ⚠️ 缺了的话 pywebview 建窗口会失败，而打包版是 console=False，什么都不会
    显示 —— 用户只会看到「双击了没反应」。所以在这里显式检查并给一句能照着做的
    提示，而不是让它静默失败。
    """
    # {F3017226-...} 是 WebView2 Runtime 在 EdgeUpdate 里的固定 GUID
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    try:
        import winreg
    except ImportError:
        return True                      # 非 Windows：交给 pywebview 自己报错

    for hive, sub in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\" + guid),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + guid),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients\\" + guid),
    ):
        try:
            with winreg.OpenKey(hive, sub) as key:
                if winreg.QueryValueEx(key, "pv")[0]:
                    return True
        except OSError:
            continue
    return False


def _icon_path() -> str | None:
    """应用图标（由 tools/make_icon.py 生成）。

    打包版不需要它：PyInstaller 已把图标嵌进 exe，WinForms 后端在没设图标时
    会自动从 sys.executable 提取。这个只在源码运行时用得上（否则窗口显示
    Python 的图标）。
    """
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(base, "assets", "icon.ico")
    return p if os.path.isfile(p) else None


# ── js_api：前端调进来的入口 ─────────────────────────────

class Api:
    """暴露给前端的接口。所有方法的返回值必须可 JSON 序列化。

    ⚠️ pywebview 会在**独立线程**里调用这些方法，因此状态访问一律加锁，
    且绝不能在这里直接碰 Tk/Qt 之类的 GUI 对象。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._window = None

        self._images: list[dict] = []
        self._selected = -1
        self._out_dir = ""
        self._face_cache: OrderedDict = OrderedDict()

        # 输出参数
        self._size_choice = next(iter(PRESET_SIZES))
        self._custom_w = str(DEFAULT_W)
        self._custom_h = str(DEFAULT_H)
        self._kb = "20"

        # 比例校正
        self._ratio_on = False
        self._aspect = 0.75
        self._fine = 1.0

        self._running = False
        self._cancel = False
        self._maximized = False

    # ── 状态快照 ────────────────────────────────────────

    def _dims(self) -> tuple[int, int]:
        preset = PRESET_SIZES.get(self._size_choice)
        if preset is not None:
            return preset
        try:
            return (max(1, int(self._custom_w)), max(1, int(self._custom_h)))
        except (TypeError, ValueError):
            return (DEFAULT_W, DEFAULT_H)

    def _params_payload(self) -> dict:
        keys = list(PRESET_SIZES.keys())
        return {
            "sizes": keys,
            "sizeIndex": keys.index(self._size_choice) if self._size_choice in keys else 0,
            "w": self._custom_w,
            "h": self._custom_h,
            "kb": self._kb,
            "out": self._out_dir,
            "ratioOn": self._ratio_on,
            "aspect": self._aspect,
            "fine": self._fine,
            "presets": RATIO_PRESETS,
        }

    def _push(self, js: str) -> None:
        """从任意线程推 JS 到前端；窗口已关时静默失败。"""
        try:
            if self._window:
                self._window.evaluate_js(js)
        except Exception:
            pass

    def _push_list(self) -> None:
        self._push("window.pvApplyList && window.pvApplyList(%s)"
                   % _json({"images": self._images, "selected": self._selected}))

    def _push_progress(self, progress=None, status=None, running=None) -> None:
        payload = {}
        if progress is not None:
            payload["progress"] = progress
        if status is not None:
            payload["status"] = status
        if running is not None:
            payload["running"] = running
        self._push("window.pvApplyProgress && window.pvApplyProgress(%s)" % _json(payload))

    # ── 前端调用：初始化 ────────────────────────────────

    def init(self) -> dict:
        with self._lock:
            # 这条是排查"界面没反应"的第一个判断点：前端连上了才会有人调 init
            logging.getLogger("idphoto").debug(
                "前端已连接（init）：%d 个尺寸预设", len(PRESET_SIZES))
            return self._params_payload()

    def list(self) -> dict:
        with self._lock:
            return {"images": list(self._images), "selected": self._selected}

    # ── 导入 ────────────────────────────────────────────

    def _add_files(self, paths) -> int:
        """追加图片（过滤扩展名、去重）。返回新增条数。"""
        added = 0
        with self._lock:
            known = {i["path"] for i in self._images}
            for p in paths:
                ext = os.path.splitext(p)[1].lower()
                if ext not in SUPPORTED_EXT or p in known:
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                self._images.append({
                    "path": p,
                    "name": os.path.basename(p),
                    "size": st.st_size,
                    "status": "pending",
                })
                known.add(p)
                added += 1
            if added and self._selected < 0:
                self._selected = 0
        return added

    def _load_dir(self, path) -> None:
        """扫描输入文件夹，递归包含子文件夹里的图片。"""
        with self._lock:
            self._images.clear()
            self._face_cache.clear()
            self._selected = -1

        root = Path(path)
        truncated = False
        out = []

        def _iter():
            stack = [root]
            while stack:
                if len(out) >= MAX_SCAN_FILES:
                    return
                d = stack.pop()
                try:
                    entries = list(os.scandir(d))
                except OSError:
                    continue
                for e in entries:
                    if e.is_dir(follow_symlinks=False):
                        if not e.name.startswith('.') and e.name.lower() not in _SKIP_DIRS:
                            stack.append(e.path)
                    elif e.is_file(follow_symlinks=False) and \
                            os.path.splitext(e.name)[1].lower() in SUPPORTED_EXT:
                        yield e
                        if len(out) >= MAX_SCAN_FILES:
                            return

        for entry in _iter():
            if len(out) >= MAX_SCAN_FILES:
                truncated = True
                break
            try:
                p = Path(entry.path)
                # 子文件夹里的图片显示相对路径，便于区分同名文件
                try:
                    rel = p.relative_to(root).as_posix()
                except ValueError:
                    rel = p.name
                out.append({
                    "path": str(p),
                    "name": rel,
                    "size": entry.stat().st_size,
                    "status": "pending",
                })
            except OSError:
                pass

        out.sort(key=lambda i: i["name"].lower())
        with self._lock:
            self._images = out
            if self._images:
                self._selected = 0

        if truncated:
            self._alert("图片过多",
                         f"该文件夹（含子文件夹）下的图片超过 {MAX_SCAN_FILES} 张，"
                         f"只载入了前 {MAX_SCAN_FILES} 张。\n请改选更具体的文件夹。")

    def _alert(self, title: str, message: str) -> None:
        """应用内提示框（新拟物样式）。

        pywebview 这一版没有 alert API，而且应用内弹窗能和整体设计保持一致，
        比系统弹窗更合适。
        """
        self._push("window.pvAlert && window.pvAlert(%s, %s)"
                   % (_json(title), _json(message)))

    def add_paths(self, paths) -> bool:
        """拖拽落点：文件夹走目录扫描，图片走追加。返回列表是否变化。"""
        dirs = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p)]

        if dirs:
            self._load_dir(dirs[0])          # 拖入文件夹＝载入该目录（与原「选文件夹」一致）
        added = self._add_files(files) if files else 0

        if not dirs and not files:
            return False
        self._push_list()
        return True

    def pick_files(self) -> bool:
        if self._running or not self._window:
            return False
        files = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True,
            file_types=("图片文件 (*.jpg;*.jpeg;*.png;*.bmp;*.tif;*.tiff;*.webp)",
                        "所有文件 (*.*)"))
        if not files:
            return False
        self._add_files(files)
        self._push_list()
        return True

    def pick_dir(self) -> bool:
        if self._running or not self._window:
            return False
        d = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not d:
            return False
        self._load_dir(d[0] if isinstance(d, (list, tuple)) else d)
        self._push_list()
        return True

    def pick_out_dir(self) -> bool:
        if self._running or not self._window:
            return False
        d = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not d:
            return False
        path = d[0] if isinstance(d, (list, tuple)) else d
        with self._lock:
            self._out_dir = str(path)
        self._push("window.pvSetOut && window.pvSetOut(%s)" % _json(self._out_dir))
        return True

    def remove_selected(self) -> bool:
        with self._lock:
            if self._running or self._selected < 0 or self._selected >= len(self._images):
                return False
            del self._images[self._selected]
            self._selected = min(self._selected, len(self._images) - 1)
        self._push_list()
        return True

    def select(self, index) -> bool:
        with self._lock:
            if not (0 <= index < len(self._images)):
                return False
            self._selected = int(index)
        return True

    # ── 参数 ────────────────────────────────────────────

    def set_params(self, p) -> bool:
        p = p or {}
        with self._lock:
            if p.get("size") in PRESET_SIZES:
                self._size_choice = p["size"]
            if not PRESET_SIZES.get(self._size_choice):      # 自定义
                self._custom_w = str(p.get("w", self._custom_w))
                self._custom_h = str(p.get("h", self._custom_h))
            self._kb = str(p.get("kb", self._kb))
        return True

    def set_ratio(self, p) -> bool:
        p = p or {}
        with self._lock:
            self._ratio_on = bool(p.get("on"))
            try:
                self._aspect = float(p.get("aspect", self._aspect))
            except (TypeError, ValueError):
                pass
            try:
                self._fine = float(p.get("fine", self._fine))
            except (TypeError, ValueError):
                pass
        return True

    # ── 预览 ────────────────────────────────────────────

    def _face_for(self, path: str, k: float, img: Image.Image):
        """按 (路径, 拉伸倍率) 缓存检测结果 —— 拉伸会改变坐标空间，必须分桶。"""
        key = (path, round(k, 3))
        with self._lock:
            if key in self._face_cache:
                self._face_cache.move_to_end(key)
                return self._face_cache[key]

        err = None
        try:
            res = detect_face(img) or _NO_FACE
        except RuntimeError as e:
            # 模型加载失败：给出可操作的中文说明，不把 OpenCV 原始报错甩给用户
            res, err = _NO_FACE, str(e)
        except Exception:
            res = _NO_FACE

        with self._lock:
            self._face_cache[key] = (res, err)
            while len(self._face_cache) > FACE_CACHE_MAX:
                self._face_cache.popitem(last=False)
        return res, err

    def preview(self) -> dict:
        with self._lock:
            if not (0 <= self._selected < len(self._images)):
                return {}
            info = dict(self._images[self._selected])
            ratio_on, aspect, fine = self._ratio_on, self._aspect, self._fine
            tw, th = self._dims()
            max_kb = _parse_kb(self._kb)

        try:
            src = Image.open(info["path"])
            src = ImageOps.exif_transpose(src) or src
        except Exception as e:
            return {"error": f"无法加载：{e}"}

        # 1) 先降采样（构图与批量一致）
        longest = max(src.size)
        if longest > MAX_PREVIEW_SRC:
            sc = MAX_PREVIEW_SRC / longest
            disp = src.resize((round(src.width * sc), round(src.height * sc)), Image.LANCZOS)
        else:
            disp = src

        # 2) 拉伸倍率用**原图转正后**的尺寸算（避免降采样取整误差，也与批量口径一致）
        k = stretch_factor(aspect, src.width, src.height, fine) if ratio_on else 1.0
        disp_s = stretch_image(disp, k)      # 这张就是「拉伸后的源」，原图面板显示它

        # 3) 在拉伸后的图上检测，坐标天然属于同一空间
        cached, model_err = self._face_for(info["path"], k, disp_s)
        face_r = None if cached is _NO_FACE else cached

        orig_disp = disp_s.copy()
        if face_r:
            _, _, _, _, det = face_r
            box = det.get("head") or det["bbox"]
            ImageDraw.Draw(orig_disp).rectangle(box, outline="#6d5dfc", width=3)
            for px, py in det.get("keypoints", [])[:2]:
                ImageDraw.Draw(orig_disp).ellipse(
                    [px - 4, py - 4, px + 4, py + 4], fill="#c0564f")

        orig_url = _data_url(_fit(orig_disp, MAX_PREVIEW_W, MAX_PREVIEW_H))

        # 4) 处理：这张图已经拉伸过，stretch 保持默认 1.0
        try:
            processed = process_image(disp_s, target_w=tw, target_h=th, face_result=face_r)
            data = compress_to_bytes(processed, max_size_kb=max_kb, target_w=tw, target_h=th)
        except Exception as e:
            return {"orig": orig_url,
                    "infoOrig": f"{info['name']}  |  {src.width}×{src.height}  |  {_fmt_size(info['size'])}",
                    "ratioText": f"横向 ×{k:.2f}",
                    "error": model_err or str(e)}

        proc_url = _data_url(_fit(processed, MAX_PREVIEW_W, MAX_PREVIEW_H, allow_upscale=True))
        warn = "⚠ 未启用人脸检测，构图可能不准" if model_err else ""
        return {
            "orig": orig_url,
            "proc": proc_url,
            "infoOrig": f"{info['name']}  |  {src.width}×{src.height}  |  {_fmt_size(info['size'])}",
            "infoProc": f"{tw}×{th}  |  {_fmt_size(len(data))}",
            "ratioText": f"横向 ×{k:.2f}",
            "warn": warn,
        }

    # ── 批量处理 ────────────────────────────────────────

    def run(self) -> bool:
        with self._lock:
            if self._running:
                return False
            if not self._images:
                self._alert("提示", "请先添加图片")
                return False
            out_dir = self._out_dir
            if not out_dir:
                self._alert("提示", "请先选择输出文件夹")
                return False

            tw, th = self._dims()
            max_kb = _parse_kb(self._kb)
            ratio_on, aspect, fine = self._ratio_on, self._aspect, self._fine
            self._running = True
            self._cancel = False

        # 预检：人脸检测模型是否可用（不可用时整批都会失败，提前拦住）
        ok_model, model_err = check_face_model()
        if not ok_model:
            with self._lock:
                self._running = False
            self._alert("人脸检测模型不可用", model_err)
            return False

        Path(out_dir).mkdir(parents=True, exist_ok=True)

        # 预检：输出文件夹是否可写（避免整批 Permission denied）
        try:
            probe = Path(out_dir) / ".write_test"
            probe.write_bytes(b"")
            probe.unlink(missing_ok=True)
        except OSError:
            with self._lock:
                self._running = False
            self._alert("输出文件夹无法写入",
                         f"无法向「{out_dir}」写入文件（可能是只读 / OneDrive 同步 / 权限问题）。\n"
                         f"请换一个普通文件夹（例如在桌面上新建一个空文件夹）后再试。")
            return False

        threading.Thread(target=self._run_batch,
                         args=(out_dir, tw, th, max_kb, ratio_on, aspect, fine),
                         daemon=True).start()
        return True

    def _run_batch(self, out_dir, tw, th, max_kb, ratio_on, aspect, fine):
        total = len(self._images)
        ok = fail = 0
        first_err = None

        for i, info in enumerate(self._images):
            with self._lock:
                if self._cancel:
                    break
                info["status"] = "processing"
            self._push("window.pvSetItemStatus && window.pvSetItemStatus(%d,'processing')" % i)
            self._push_progress(progress=i / max(total, 1),
                                status=f"处理中 [{i + 1}/{total}] {info['name']}")

            try:
                img = Image.open(info["path"])
                # 必须先按 EXIF 转正再算倍率：手机竖拍的 orientation 6/8 会交换宽高，
                # 用原始 size 会把该拉宽的算成该拉高
                img = ImageOps.exif_transpose(img) or img
                k = stretch_factor(aspect, img.width, img.height, fine) if ratio_on else 1.0

                data = process_image_to_bytes(img, target_w=tw, target_h=th,
                                              max_size_kb=max_kb, stretch=k)

                # 保留相对路径（子文件夹里的图片跟着建同名子目录，避免重名互相覆盖）
                rel = Path(str(info["name"]).replace("/", os.sep))
                out_path = Path(out_dir) / rel.with_suffix(".jpg")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(data)

                status, ok = "success", ok + 1
            except Exception as e:
                status, fail = "fail", fail + 1
                if first_err is None:
                    first_err = str(e)

            with self._lock:
                info["status"] = status
            self._push("window.pvSetItemStatus && window.pvSetItemStatus(%d,'%s')" % (i, status))

        with self._lock:
            self._running = False
        self._push_progress(progress=1.0, running=False,
                            status=f"完成：成功 {ok}，失败 {fail}")

        # 全部失败时状态栏一句话不够，必须弹框说明原因
        if ok == 0 and fail > 0:
            self._alert("处理失败", f"{fail} 张照片全部处理失败。\n\n原因：{first_err or '未知错误'}")

    # ── 窗口控制 ────────────────────────────────────────

    def window_action(self, action) -> bool:
        """前端窗口按钮。方法名不能叫 window —— 会和 self._window 属性冲突。"""
        w = self._window
        if w is None:
            return False
        try:
            if action == "min":
                w.minimize()
            elif action == "max":
                # 这个版本的 pywebview 没有暴露窗口最大化状态查询，用事件维护的标志
                if self._maximized:
                    w.restore()
                else:
                    w.maximize()
            elif action == "close":
                w.destroy()
        except Exception:
            return False
        return True


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


# ── 拖拽 ────────────────────────────────────────────────

def _js_api_surface_problems(api) -> list:
    """检查 js_api 对象上有没有会被 pywebview **递归展开**的属性。

    pywebview 建 API 表时会对 js_api 对象做 dir() 遍历，凡是「非下划线开头、
    不可调用、但有 __module__」的属性都会被当成子 API 一路挖下去
    （见 pywebview/util.py 的 get_functions）。把 Window、连接、缓存这类对象
    直接挂成公开属性，就会一路走进 window.native → WinForms →
    AccessibilityObject.Bounds.Empty.Empty.Empty… 递归爆栈。

    ⚠️ 这个失败的**表现是静默的**：整个 API 表建不起来，界面不报错，
    只是所有按钮都没反应。所以必须在启动时把它钉死，而不是等用户发现。
    """
    import inspect

    problems = []
    for name in dir(api):
        if name.startswith("_"):
            continue
        attr = getattr(api, name)
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            continue
        problems.append(name)
    return problems


def _bind_drop(window, api: Api) -> bool:
    """把原生拖放接到 Api.add_paths。返回是否注册成功。

    ⚠️ JS 侧出于沙箱限制只能拿到文件名，**真实路径只有 Python 侧有**：
    pywebview 在原生 drop 里把路径存起来，再按文件名匹配注入 pywebviewFullPath。
    所以拖拽必须走这条 DOM 事件，不能用浏览器那套 DataTransfer。
    """
    global _DND_ERROR
    try:
        from webview.dom import DOMEventHandler

        def on_drop(e):
            files = (e.get("dataTransfer") or {}).get("files") or []
            paths = [f.get("pywebviewFullPath") for f in files]
            paths = [p for p in paths if p]
            if paths:
                api.add_paths(paths)

        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        _DND_ERROR = None
        return True
    except Exception as e:                                   # pragma: no cover
        _DND_ERROR = "%s: %s" % (type(e).__name__, e)
        print(f"[gui] 拖拽注册失败（不影响其它功能）：{_DND_ERROR}")
        return False


# ── 入口 ────────────────────────────────────────────────

def build_window(api: Api, on_loaded=None):
    """创建主窗口并把 api 接上。

    ⚠️ main() 与 tools/ui_check.py 共用这一条路径。之前这里写成了
    `api.window = window`（公开属性），触发 pywebview 递归展开把整个 API 表
    搞崩，而测试里恰好写的是 `api._window`，两条路分叉导致没能发现。
    现在只有这一个创建入口，且创建后立刻自检。
    """
    window = webview.create_window(
        _WINDOW_TITLE,
        url=os.path.join(_web_root(), "index.html"),
        js_api=api,
        width=1120, height=760, min_size=(1000, 680),
        frameless=True,          # 配自定义标题栏做新拟物
        easy_drag=False,         # 只让标题栏可拖，避免整窗乱拖
        text_select=False,       # 桌面应用观感：默认不可选中文本（输入框内仍可选）
        zoomable=False,
        background_color="#e0e5ec",
    )
    api._window = window          # 必须是私有属性，见 _js_api_surface_problems

    # 赋值之后再自检 —— 要检的是运行时真实状态，不是类定义
    problems = _js_api_surface_problems(api)
    if problems:
        raise RuntimeError(
            "Api 暴露了会被 pywebview 递归展开的属性 %s。"
            "这会让整个 API 表建不起来、前端所有按钮失灵，"
            "必须把它们改成下划线开头的私有属性。" % problems)

    def _loaded():
        _bind_drop(window, api)
        if on_loaded:
            on_loaded(window)

    window.events.loaded += _loaded
    # 用事件维护最大化标志，用户从别处（双击拖拽区 / Win+↑）改状态时也能同步
    window.events.maximized += lambda: setattr(api, "_maximized", True)
    window.events.restored += lambda: setattr(api, "_maximized", False)
    return window


def main():
    _setup_logging()

    # 缺 WebView2 时给一句能照着做的提示，别让用户面对「双击了没反应」
    if not _check_webview2():
        logging.getLogger("idphoto").error("未检测到 WebView2 运行时，无法创建窗口")
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                "本程序需要 Microsoft Edge WebView2 运行时才能显示界面，当前系统未检测到。\n\n"
                "请先安装再运行：\nhttps://go.microsoft.com/fwlink/p/?LinkId=2124703",
                _WINDOW_TITLE, 0x30 | 0x00010000)   # MB_ICONWARNING | MB_SETFOREGROUND
        except Exception:
            pass
        return

    # 拖拽区只认直接命中：窗口按钮若在拖拽区内，按下会同时触发窗口拖动和按钮点击
    webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True

    build_window(Api())

    # debug=False：pywebview 会据此关掉 DevTools、默认右键菜单与浏览器快捷键
    # （F5 / Ctrl+P / Ctrl+± / F12），这正是我们要的桌面应用行为
    webview.start(debug=False, icon=_icon_path())


if __name__ == "__main__":
    _ensure_single_instance()  # 必须在建窗口前，重复启动在这里就结束了
    main()
