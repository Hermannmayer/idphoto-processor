# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for idphoto-processor (GUI)。

采用 onedir 模式：单文件(onefile)模式每次启动都要把两百多 MB、两千多个文件解压到
%TEMP% 再运行，低配机上要几十秒且期间没有任何界面，既慢又容易在解压中途被打断
（杀软拦截 / 用户强关）而留下缺文件的临时目录。安装包用 onedir 一次装好，启动不再解压。

关键处理：
1. collect_all('numpy') — 避免 "Importing the numpy C-extensions failed"
2. collect_all('PIL') / collect_all('customtkinter') — 自动收集插件和动态导入
3. models/ 目录 — YuNet 人脸检测 ONNX 模型（另有 model_data.py 内嵌兜底）
4. 剔除 ffmpeg 视频库与 AVIF 编解码 — 本程序只处理静态图片，用不到
"""
from PyInstaller.utils.hooks import collect_all

# 用不到、且体积可观的二进制/数据（按目标名匹配，小写子串）
#   opencv_videoio_ffmpeg*.dll — PyInstaller 的 hook-cv2 会无差别收集 cv2 目录下所有 DLL，
#                                本程序不碰 VideoCapture/VideoWriter，白占约 31 MB
#   *_avif / libavif / libaom — 只输出 JPEG，白占约 8 MB
_DROP_PATTERNS = ("opencv_videoio_ffmpeg", "_avif", "libavif", "libaom", "aom-")

datas = [
    ("models", "models"),  # YuNet 人脸检测 ONNX 模型
]
binaries = []
hiddenimports = [
    "PIL._tkinter_finder",
]

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

# ── customtkinter ──
tmp_ret = collect_all("customtkinter")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# ── opencv-python 隐藏依赖 ──
hiddenimports += [
    "cv2",
    "cv2.data",
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
    excludes=[],
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
print("[spec] 剔除 %d 个无用条目（ffmpeg 视频库 / AVIF 编解码）"
      % (_before - len(a.binaries) - len(a.datas)))

pyz = PYZ(a.pure)

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

