#!/usr/bin/env python3
"""把一张方形原图转成 Windows 应用图标（多尺寸 .ico）。

用法：
    python tools/make_icon.py                       # 读 assets/icon-source.png → 写 assets/icon.ico
    python tools/make_icon.py 源图.png 输出.ico
    python tools/make_icon.py --trim-bottom 8       # 裁掉底部 8%（例如去掉右下角的水印）

生成的 ico 内含 16/24/32/48/64/128/256 七档，覆盖任务栏、桌面快捷方式、
资源管理器各视图和安装程序向导。PyInstaller 的 EXE(icon=...) 与 Inno Setup 的
SetupIconFile 都指向这一个文件，所以改了图标只需重跑本脚本。
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SIZES = [256, 128, 64, 48, 32, 24, 16]      # 从大到小，供 PIL 逐档降采样


def build(src: str, out: str, trim_bottom: float = 0.0, trim_edges: float = 0.0) -> str:
    img = Image.open(src)
    img = img.convert("RGBA") if img.mode != "RGBA" else img

    w, h = img.size
    if trim_bottom > 0 or trim_edges > 0:
        top = int(h * trim_edges / 100)
        bottom = h - int(h * trim_bottom / 100)
        left = int(w * trim_edges / 100)
        right = w - int(w * trim_edges / 100)
        img = img.crop((left, top, right, bottom))
        w, h = img.size

    # 居中裁成正方形：图标必须方正，非方图直接压扁会变形
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2,
                    (w - side) // 2 + side, (h - side) // 2 + side))

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    # 先降到一个够大的基准，再由 PIL 逐档生成，避免它内部用低质量重采样
    base = img.resize((256, 256), Image.LANCZOS)
    base.save(out, format="ICO", sizes=[(s, s) for s in SIZES])

    # 顺手留一张 256 的 png，方便肉眼核对
    png = os.path.splitext(out)[0] + "-256.png"
    base.save(png)

    return out


def main():
    ap = argparse.ArgumentParser(description="生成 Windows 应用图标")
    ap.add_argument("src", nargs="?",
                    default=os.path.join(ROOT, "assets", "icon-source.png"))
    ap.add_argument("out", nargs="?",
                    default=os.path.join(ROOT, "assets", "icon.ico"))
    ap.add_argument("--trim-bottom", type=float, default=0.0,
                    help="裁掉底部百分比（例如 8 去掉右下角水印）")
    ap.add_argument("--trim-edges", type=float, default=0.0,
                    help="四周各裁掉百分比")
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        print("找不到源图：%s" % args.src)
        print("请把图标原图存到该路径（或作为第一个参数传入）后重试。")
        return 1

    out = build(args.src, args.out, args.trim_bottom, args.trim_edges)

    with Image.open(out) as ic:
        got = sorted({s for s in ic.info.get("sizes", [])}, reverse=True)
    print("已生成 %s" % out)
    print("内含尺寸：%s" % ", ".join("%dx%d" % s for s in got))
    print("预览图：%s" % (os.path.splitext(out)[0] + "-256.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
