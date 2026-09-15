#!/usr/bin/env python3
"""临时集成测试：不建窗口，直接驱动 gui.Api 跑通整条后端链路。

覆盖：加图 / 列表 / 预览（含比例校正）/ 批量出图 / 输出目录结构。
这是一个独立的、按需运行的后端集成测试（不参与打包前的快速自检）。
"""
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

import gui  # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + name + (" " + detail if detail else ""))
    if not ok:
        FAIL.append(name)


def make_img(path, w=900, h=1200, color=(235, 235, 240)):
    img = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(img)
    d.ellipse([w * 0.33, h * 0.30, w * 0.67, h * 0.62], fill=(230, 195, 170))
    d.ellipse([w * 0.41, h * 0.40, w * 0.45, h * 0.44], fill=(60, 50, 45))
    d.ellipse([w * 0.55, h * 0.40, w * 0.59, h * 0.44], fill=(60, 50, 45))
    d.rectangle([w * 0.20, h * 0.62, w * 0.80, h], fill=(70, 90, 130))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)


def main():
    tmp = tempfile.mkdtemp(prefix="idphoto_apitest_")
    try:
        src = os.path.join(tmp, "in")
        # 每张源图宽高比刻意不同：3:4 预设下各自的拉伸倍率应当互不相同
        # （含子目录 + 中文名 + 空格名，覆盖相对路径与重名场景）
        make_img(os.path.join(src, "a.jpg"), 1200, 900)              # 4:3  → k=0.5625
        make_img(os.path.join(src, "我的 照片 01.jpg"), 900, 900)     # 1:1  → k=0.75
        make_img(os.path.join(src, "sub", "b.png"), 600, 900)        # 2:3  → k=1.125
        make_img(os.path.join(src, "sub", "dup.jpg"), 1000, 1200)    # 5:6  → k=0.9
        make_img(os.path.join(src, "sub2", "dup.jpg"), 1000, 1200)
        with open(os.path.join(src, "note.txt"), "w") as f:
            f.write("should be skipped")

        api = gui.Api()

        # ── 目录扫描 ──
        api._load_dir(src)
        names = [i["name"] for i in api._images]
        check("目录扫描只收图片", len(api._images) == 5, str(names))
        check("子目录用相对路径", "sub/b.png" in names, str(names))
        check("同名不同目录都保留", names.count("dup.jpg") == 0 and
              sum(1 for n in names if n.endswith("dup.jpg")) == 2, str(names))

        # ── 扩展名过滤与去重 ──
        added = api._add_files([os.path.join(src, "note.txt")])
        check("非图片被过滤", added == 0)
        before = len(api._images)
        api._add_files([os.path.join(src, "a.jpg")])
        check("重复路径被去重", len(api._images) == before)

        # ── 预览（默认，比例校正关）──
        api.select(0)
        p = api.preview()
        check("预览返回原图 data URL", str(p.get("orig", "")).startswith("data:image/jpeg;base64,"))
        check("预览返回处理后 data URL", str(p.get("proc", "")).startswith("data:image/jpeg;base64,"))
        check("预览无错误", not p.get("error"), str(p.get("error")))
        check("比例校正关时倍率为 ×1.00", p.get("ratioText") == "横向 ×1.00", str(p.get("ratioText")))
        check("处理后信息含目标尺寸", "190×260" in str(p.get("infoProc")), str(p.get("infoProc")))

        # ── 预览（比例校正开）──
        api.set_ratio({"on": True, "aspect": 0.75, "fine": 1.0})
        p2 = api.preview()
        check("开启后倍率随源图比例变化",
              p2.get("ratioText") != "横向 ×1.00", str(p2.get("ratioText")))
        check("倍率符合预期（4:3 源图拉到 3:4）",
              p2.get("ratioText") == "横向 ×0.56", str(p2.get("ratioText")))
        check("开启后仍无错误", not p2.get("error"), str(p2.get("error")))

        # 换一张不同源的图，倍率应当不同（逐图各算各的）
        # 注意列表是按名字排序的，别按硬编码索引取图
        idx = next(i for i, im in enumerate(api._images) if im["name"] == "sub/b.png")
        api.select(idx)
        p3 = api.preview()
        check("不同图各自算倍率", p3.get("ratioText") != p2.get("ratioText"),
              "%s vs %s" % (p2.get("ratioText"), p3.get("ratioText")))
        check("倍率符合预期（2:3 源图拉到 3:4）",
              p3.get("ratioText") == "横向 ×1.12", str(p3.get("ratioText")))

        # ── 批量处理 ──
        out = os.path.join(tmp, "out")
        api._out_dir = out
        api.set_ratio({"on": True, "aspect": 0.75, "fine": 1.0})
        api.set_params({"size": "1寸 (25×35mm) 295×413", "w": "295", "h": "413", "kb": "40"})
        started = api.run()
        check("run 返回已启动", started is True)

        import time
        for _ in range(600):
            if not api._running:
                break
            time.sleep(0.1)
        check("批量已结束", not api._running)

        ok_n = sum(1 for i in api._images if i["status"] == "success")
        check("全部成功", ok_n == len(api._images),
              "成功 %d / %d" % (ok_n, len(api._images)))

        # 输出目录结构：子目录保留、同名不同目录不互相覆盖
        outs = []
        for base, _, files in os.walk(out):
            for fn in files:
                outs.append(os.path.relpath(os.path.join(base, fn), out).replace("\\", "/"))
        outs.sort()
        check("输出文件数正确", len(outs) == 5, str(outs))
        check("子目录结构保留", "sub/b.jpg" in outs, str(outs))
        check("同名不同目录都出图",
              "sub/dup.jpg" in outs and "sub2/dup.jpg" in outs, str(outs))

        # 输出尺寸与体积
        with Image.open(os.path.join(out, "a.jpg")) as im:
            check("输出尺寸符合预设", im.size == (295, 413), str(im.size))
        kb = os.path.getsize(os.path.join(out, "a.jpg")) / 1024
        check("输出体积不超上限", kb <= 40, "%.1f KB" % kb)

        # ── 比例校正关闭时，输出应与不带校正完全一致 ──
        api2 = gui.Api()
        api2._load_dir(src)
        api2._out_dir = os.path.join(tmp, "out2")
        api2.set_params({"size": "1寸 (25×35mm) 295×413", "w": "295", "h": "413", "kb": "40"})
        api2.run()
        for _ in range(600):
            if not api2._running:
                break
            time.sleep(0.1)
        same = (open(os.path.join(out, "a.jpg"), "rb").read() ==
                open(os.path.join(tmp, "out2", "a.jpg"), "rb").read())
        check("关闭比例校正时输出与开启时不同（说明开关真的生效）", not same)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAIL:
        print("失败 %d 项：%s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
