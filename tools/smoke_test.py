#!/usr/bin/env python3
"""打包前的冒烟自检：依赖能否导入、人脸检测模型能否加载、整条流水线能否出图。

几秒钟就能跑完，用来在打 EXE 之前挡住"模型缺失/依赖不兼容"这类问题——
这类问题一旦漏到打包后，只有客户装上才会发现。

    python tools/smoke_test.py
"""

import os
import sys

# Windows / CI 上 stdout 可能不是 UTF-8（英文版 runner 是 cp1252），
# 会让下面的中文 print 直接抛 UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

failures = []


def check(name, fn):
    try:
        detail = fn()
        print("  [OK]   %s %s" % (name, detail or ""))
    except Exception as e:
        failures.append((name, e))
        print("  [FAIL] %s -> %s: %s" % (name, type(e).__name__, e))


def _imports():
    import cv2
    import numpy
    from PIL import Image, ImageOps, ImageDraw  # noqa: F401
    import customtkinter  # noqa: F401
    return "(cv2 %s, numpy %s, Pillow %s)" % (cv2.__version__, numpy.__version__,
                                              __import__("PIL").__version__)


def _model():
    import process
    ok, err = process.check_face_model()
    if not ok:
        raise RuntimeError(err)
    return "(模型 %d 字节)" % os.path.getsize(process._ensure_model_file())


def _pipeline():
    import io
    from PIL import Image, ImageDraw
    import process

    # 造一张简单的图跑完整条流水线：能出 JPEG 即说明处理链路没断
    img = Image.new("RGB", (600, 800), (235, 235, 240))
    ImageDraw.Draw(img).ellipse([200, 250, 400, 500], fill=(230, 195, 170))
    data = process.process_image_to_bytes(img, target_w=190, target_h=260, max_size_kb=20)
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError("输出不是 JPEG")
    if len(data) > 20 * 1024:
        raise RuntimeError("输出超过 20KB：%d 字节" % len(data))
    return "(%d 字节)" % len(data)


def _embedded_model():
    """内嵌模型必须与仓库里的 models/ 文件一致（防止改了模型忘了重新生成）。"""
    import base64
    import hashlib
    import zlib
    import model_data

    raw = zlib.decompress(base64.b64decode("".join(model_data._DATA)))
    if len(raw) != model_data.ORIGINAL_SIZE:
        raise RuntimeError("内嵌模型长度不符：%d != %d" % (len(raw), model_data.ORIGINAL_SIZE))
    if hashlib.sha256(raw).hexdigest() != model_data.SHA256:
        raise RuntimeError("内嵌模型 sha256 与记录不符")

    path = os.path.join(ROOT, "models", "face_detection_yunet_2023mar.onnx")
    if os.path.isfile(path) and open(path, "rb").read() != raw:
        raise RuntimeError("models/ 下的模型与 model_data.py 不一致，"
                           "请重新执行 tools/embed_model.py")
    return "(%d 字节)" % len(raw)


def main():
    print("冒烟自检：")
    check("依赖导入", _imports)
    check("内嵌模型一致性", _embedded_model)
    check("人脸检测模型加载", _model)
    check("处理流水线", _pipeline)

    if failures:
        print("\n自检失败 %d 项：" % len(failures))
        for name, e in failures:
            print("  - %s: %s" % (name, e))
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
