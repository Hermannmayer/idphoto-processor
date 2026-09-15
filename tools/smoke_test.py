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
    import webview  # noqa: F401
    from importlib.metadata import version
    return "(cv2 %s, numpy %s, Pillow %s, pywebview %s)" % (
        cv2.__version__, numpy.__version__, __import__("PIL").__version__,
        version("pywebview"))


def _webview_assets():
    """pywebview 靠 webview/lib 下的 WebView2 程序集建窗口。

    打包时漏收这些文件不会报错，只会让窗口起不来或拖拽静默失效 —— 正好是
    这个项目历史上被坑过的那一类问题（见 README 的 "Can't read ONNX file"）。
    """
    import webview

    root = os.path.dirname(os.path.abspath(webview.__file__))
    lib = os.path.join(root, "lib")
    if not os.path.isdir(lib):
        raise RuntimeError("缺少 webview/lib 目录：%s" % lib)
    need = ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll")
    missing = [n for n in need if not os.path.isfile(os.path.join(lib, n))]
    if missing:
        raise RuntimeError("webview/lib 缺文件：%s" % ", ".join(missing))
    return "(%s)" % ", ".join(need)


def _web_assets():
    """前端静态文件必须在（打包时由 spec 的 datas 收集 web/）。

    漏收在运行期的表现是白屏，而不是报错 —— 正是这个脚本要挡住的那类问题。
    """
    need = ("index.html", "style.css", "app.js")
    root = os.path.join(ROOT, "web")
    missing = [n for n in need if not os.path.isfile(os.path.join(root, n))]
    if missing:
        raise RuntimeError("web/ 缺文件：%s" % ", ".join(missing))

    # 宿主模块也要能导入 —— 它会连带加载 webview / pythonnet
    import gui
    if not gui._WINDOW_TITLE:
        raise RuntimeError("宿主缺少窗口标题常量")
    return "(%s)" % ", ".join(need)


def _js_api():
    """js_api 上不能有会被 pywebview 递归展开的公开属性。

    pywebview 建 API 表时会 dir() 遍历 js_api 对象，把「非下划线开头、不可调用、
    有 __module__」的属性当子 API 递归挖下去。挂个 Window 上去就会一路走进
    WinForms 的 AccessibilityObject.Bounds.Empty.Empty… 递归爆栈。

    ⚠️ 这个失败的**表现是静默的**：API 表建不起来，前端不报错，只是所有按钮失灵。
    所以必须在打包前挡住，而不是等用户发现。
    """
    import gui

    api = gui.Api()
    api._window = object()          # 模拟 build_window 挂上窗口后的真实状态
    problems = gui._js_api_surface_problems(api)
    if problems:
        raise RuntimeError("Api 暴露了会被递归展开的属性：%s" % problems)
    n = len([x for x in dir(api)
             if not x.startswith('_') and callable(getattr(api, x))])
    return "(%d 个公开方法，无多余属性)" % n


def _stretch():
    """比例校正只该改源图几何，不该改最终输出尺寸。"""
    from PIL import Image, ImageDraw
    import process

    img = Image.new("RGB", (600, 800), (235, 235, 240))
    ImageDraw.Draw(img).ellipse([200, 250, 400, 500], fill=(230, 195, 170))

    # k≈1 必须短路返回同一对象，不能白白多一次重采样
    if process.stretch_image(img, 1.0) is not img:
        raise RuntimeError("stretch=1.0 未走短路")

    for k in (1.25, 0.8, 1.0):
        out = process.process_image(img, target_w=190, target_h=260, stretch=k)
        if out.size != (190, 260):
            raise RuntimeError("stretch=%.2f 的输出尺寸变成了 %s" % (k, out.size))

    # 拉伸倍率要夹在安全区间内
    if process.stretch_factor(0.75, 600, 800, 99.0) > process.STRETCH_MAX:
        raise RuntimeError("stretch_factor 未夹到上限")

    return "(1.25 / 0.8 / 1.0 输出尺寸均正确)"


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
    check("pywebview 资源", _webview_assets)
    check("前端静态文件", _web_assets)
    check("js_api 接口面", _js_api)
    check("内嵌模型一致性", _embedded_model)
    check("人脸检测模型加载", _model)
    check("处理流水线", _pipeline)
    check("比例校正", _stretch)

    if failures:
        print("\n自检失败 %d 项：" % len(failures))
        for name, e in failures:
            print("  - %s: %s" % (name, e))
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
