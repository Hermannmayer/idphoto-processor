# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for idphoto-processor (GUI).

关键处理：
1. collect_all('numpy') — 避免 "Importing the numpy C-extensions failed"
2. collect_all('PIL') / collect_all('customtkinter') — 自动收集插件和动态导入
3. models/ 目录 — YuNet 人脸检测 ONNX 模型
"""
from PyInstaller.utils.hooks import collect_all

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
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
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
