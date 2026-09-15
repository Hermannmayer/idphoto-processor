# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for idphoto-processor (GUI)。

采用 onedir 模式：单文件(onefile)模式每次启动都要把两百多 MB、两千多个文件解压到
%TEMP% 再运行，低配机上要几十秒且期间没有任何界面，既慢又容易在解压中途被打断
（杀软拦截 / 用户强关）而留下缺文件的临时目录。安装包用 onedir 一次装好，启动不再解压。

关键处理：
1. collect_all('numpy') — 避免 "Importing the numpy C-extensions failed"
2. collect_all('PIL') — 自动收集图像格式插件与动态导入
3. models/ 目录 — YuNet 人脸检测 ONNX 模型（另有 model_data.py 内嵌兜底）
4. web/ 目录 — 前端静态页（pywebview 从这一目录提供服务）
5. 剔除 ffmpeg 视频库与 AVIF 编解码 — 本程序只处理静态图片，用不到
6. 剔除 Qt 系与 PyGObject — PyInstaller 会把装了的包全打进去，即使 pywebview
   走的是 EdgeChromium 后端（浏览器内核由系统的 WebView2 提供，不随包分发）

pywebview 与 pythonnet 都通过 pyinstaller40 入口点自带 hook（分别收集
webview/lib 的 WebView2 程序集、以及 pythonnet/runtime 的 CLR 托管 DLL），
PyInstaller 会自动发现，这里不需要再写 collect_all / runtime hook。
"""
from PyInstaller.utils.hooks import collect_all

# 用不到、且体积可观的二进制/数据（按目标名匹配，小写子串）
#   opencv_videoio_ffmpeg*.dll — PyInstaller 的 hook-cv2 会无差别收集 cv2 目录下所有 DLL，
#                                本程序不碰 VideoCapture/VideoWriter，白占约 31 MB
#   *_avif / libavif / libaom — 只输出 JPEG，白占约 8 MB
_DROP_PATTERNS = ("opencv_videoio_ffmpeg", "_avif", "libavif", "libaom", "aom-")

datas = [
    ("models", "models"),  # YuNet 人脸检测 ONNX 模型
    ("web", "web"),        # 前端静态页（index.html / style.css / app.js）
]
binaries = []
hiddenimports = []

# ── numpy: 必须 collect_all，否则 C 扩展无法加载 ──
tmp_ret = collect_all("numpy")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# ── PIL: 收集所有图像格式插件 ──
tmp_ret = collect_all("PIL")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# ── opencv-python 隐藏依赖 ──
hiddenimports += [
    "cv2",
    "cv2.data",
]

# PyInstaller 会把环境里装了的包全部收进来，哪怕 pywebview 根本不用它们。
# 这些是 Qt / PyGObject 系（pywebview 走 EdgeChromium 后端，用不到）。
_EXCLUDES = [
    "PyQt5", "PyQt6", "PySide2", "PySide6",
    "gi",          # PyGObject（Linux 后端）
    "tkinter",     # 界面已换成 pywebview，不再需要 Tk
]

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=0,
)


def _drop(entries):
    """按目标名剔除不需要的条目。"""
    return TOC([e for e in entries
                if not any(pat in str(e[0]).lower() for pat in _DROP_PATTERNS)])


_before = len(a.binaries) + len(a.datas)
a.binaries = _drop(a.binaries)
a.datas = _drop(a.datas)
# 注意：这里的输出必须是纯 ASCII —— PyInstaller 在 Windows 控制台（英文版是 cp1252）
# 里 exec 本文件，中文 print 会直接 UnicodeEncodeError 让整个构建失败
print("[spec] dropped %d unused entries (ffmpeg video lib / AVIF codec)"
      % (_before - len(a.binaries) - len(a.datas)))

pyz = PYZ(a.pure)

# 应用图标：由 tools/make_icon.py 从 assets/icon-source.png 生成多尺寸 .ico。
# 文件不存在时留空，避免还没放图标就构建失败。
import os as _os
_ICON = _os.path.join(SPECPATH, "assets", "icon.ico")
_ICON_ARG = _ICON if _os.path.isfile(_ICON) else None
print("[spec] app icon: %s" % (_ICON if _ICON_ARG else "NOT FOUND, using default"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="idphoto-processor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        "numpy*",
        "*.pyd",
        "*.dll",
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON_ARG,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        "numpy*",
        "*.pyd",
        "*.dll",
    ],
    name="idphoto-processor",
)

