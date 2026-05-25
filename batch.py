#!/usr/bin/env python3
"""批量证件照处理 CLI。"""

import argparse
import sys
from pathlib import Path

from PIL import Image

from process import process_image_to_bytes


def main():
    parser = argparse.ArgumentParser(description="批量证件照处理 → 190×260px, <20KB")
    parser.add_argument("input", help="输入图片路径或目录")
    parser.add_argument("-o", "--output", default="./output", help="输出路径（文件或目录）")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出文件")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = list(input_path.glob("*"))
        files = [f for f in files if f.suffix.lower() in
                 (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp")]
    else:
        print(f"错误：找不到输入路径 {input_path}")
        sys.exit(1)

    if not files:
        print("没有找到可处理的图片文件。")
        return

    if len(files) > 1 or not output_path.suffix:
        output_path.mkdir(parents=True, exist_ok=True)

    ok = 0
    fail = 0
    for f in files:
        out = output_path if output_path.suffix else output_path / f.with_suffix(".jpg").name
        if out.exists() and not args.overwrite:
            print(f"跳过：{out.name} 已存在")
            continue
        try:
            img = Image.open(f)
            data = process_image_to_bytes(img)
            out.write_bytes(data)
            size_kb = len(data) / 1024
            print(f"OK   {f.name} → {out.name}  ({size_kb:.1f}KB)")
            ok += 1
        except Exception as e:
            print(f"FAIL {f.name}: {e}")
            fail += 1

    print(f"\n完成：成功 {ok}，失败 {fail}")


if __name__ == "__main__":
    main()
