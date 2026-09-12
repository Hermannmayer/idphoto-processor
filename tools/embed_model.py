#!/usr/bin/env python3
"""把 models/face_detection_yunet_2023mar.onnx 内嵌进 model_data.py。

模型是程序的核心（人脸检测），但作为独立文件随 EXE 分发时，可能被杀软拦截或
被临时目录清理策略删除，导致运行时报 "Can't read ONNX file"。内嵌后模型直接从
可执行文件里取，不再依赖任何外部文件。

改了 models/ 下的模型后需要重新执行本脚本：
    python tools/embed_model.py
"""

import base64
import hashlib
import os
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODEL_NAME = "face_detection_yunet_2023mar.onnx"
SRC = os.path.join(ROOT, "models", MODEL_NAME)
DST = os.path.join(ROOT, "model_data.py")


def main():
    raw = open(SRC, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    # 先 zlib 再 base64：源码里的字符串更短，exe 内的压缩包也不会重复膨胀
    b64 = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    lines = [b64[i:i + 100] for i in range(0, len(b64), 100)]

    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write('"""内嵌的 YuNet 人脸检测模型（由 /tools/embed_model.py 生成，请勿手工修改）。\n\n')
        f.write("模型原名 %s，原始大小 %d 字节，sha256 %s。\n" % (MODEL_NAME, len(raw), sha))
        f.write('内容为 zlib 压缩后再 base64，见下方 _DATA；原始字节数见 ORIGINAL_SIZE。\n"""\n\n')
        f.write("# 原始（解压后）字节数，用于校验解出来的文件是否完整\n")
        f.write("ORIGINAL_SIZE = %d\n\n" % len(raw))
        f.write("# 原始文件的 sha256\n")
        f.write('SHA256 = "%s"\n\n' % sha)
        f.write("_DATA = (\n")
        for ln in lines:
            f.write('    "%s"\n' % ln)
        f.write(")\n")

    print("已写入 %s" % DST)
    print("  原始 %d 字节, sha256 %s" % (len(raw), sha))
    print("  base64 行数 %d, 文件大小 %.1f KB" % (len(lines), os.path.getsize(DST) / 1024))


if __name__ == "__main__":
    main()
