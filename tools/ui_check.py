#!/usr/bin/env python3
"""临时桥接测试：起真实窗口，让 Python 反向查询页面状态，验证 js_api 双向通。

覆盖：方法表 / 初始化 / 列表 / 预览 / 拖拽注册与落点 / 提示框 / 进度推送 / 自绘下拉。
用于改界面后快速核对：起真实窗口、双向验证桥接、并留下截图。
"""
import json
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import webview  # noqa: E402

import gui  # noqa: E402

RESULT = {}


def grab(name):
    """抓真实窗口的截图（用于肉眼核对最终外观）。

    ⚠️ 必须先声明 DPI 感知：否则脚本拿到的是 Windows 虚拟化后的坐标，
    而抓屏用的是物理像素，在 125% / 150% 缩放的屏幕上整张图会错位。
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()

        u = ctypes.windll.user32
        u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        u.FindWindowW.restype = ctypes.c_void_p
        hwnd = u.FindWindowW(None, gui._WINDOW_TITLE)
        if not hwnd:
            RESULT["shot_err_" + name] = "找不到窗口"
            return
        u.SetForegroundWindow(ctypes.c_void_p(hwnd))
        time.sleep(0.7)
        rect = wt.RECT()
        u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
        from PIL import ImageGrab

        shot = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
        if max(shot.size) > 1000:                       # 控制回传体积
            r = 1000 / max(shot.size)
            shot = shot.resize((round(shot.width * r), round(shot.height * r)))
        out = os.path.join(ROOT, "_tmp", "win_%s.png" % name)
        shot.save(out)
        RESULT.setdefault("shots", []).append(out)
    except Exception as e:
        RESULT["shot_err_" + name] = "%s: %s" % (type(e).__name__, e)


def seed_images(src_dir):
    """造几张比例各异的测试图，让列表和预览更有代表性。"""
    from PIL import Image, ImageDraw

    os.makedirs(os.path.join(src_dir, "子目录"), exist_ok=True)
    specs = [("人像 a.jpg", 1200, 900), ("我的 照片 01.png", 900, 900),
             ("b.jpg", 600, 900)]
    for name, w, h in specs:
        img = Image.new("RGB", (w, h), (230, 232, 236))
        d = ImageDraw.Draw(img)
        d.ellipse([w * .33, h * .30, w * .67, h * .62], fill=(228, 190, 165))
        d.ellipse([w * .41, h * .40, w * .45, h * .44], fill=(58, 48, 42))
        d.ellipse([w * .55, h * .40, w * .59, h * .44], fill=(58, 48, 42))
        d.rectangle([w * .20, h * .62, w * .80, h], fill=(70, 90, 130))
        img.save(os.path.join(src_dir, name))
    sub = os.path.join(src_dir, "子目录", "c.jpg")
    Image.new("RGB", (1000, 1200), (232, 228, 224)).save(sub)


def main():
    webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True

    src = os.path.join(ROOT, "_tmp", "in")
    seed_images(src)

    api = gui.Api()
    api._load_dir(src)
    api._out_dir = os.path.join(ROOT, "_tmp", "out")
    api.set_ratio({"on": True, "aspect": 0.75, "fine": 1.0})

    def on_loaded(win):
        # 拖拽已由 build_window 注册，这里只记录结果
        RESULT["dnd_bound"] = gui._DND_ERROR is None
        RESULT["dnd_error"] = gui._DND_ERROR
        threading.Thread(target=probe, daemon=True).start()

    window = gui.build_window(api, on_loaded=on_loaded)

    def probe():
        time.sleep(2.5)                      # 等前端 boot
        try:
            RESULT["probe"] = window.evaluate_js("""JSON.stringify({
                hasApi: !!(window.pywebview && window.pywebview.api),
                apiMethods: Object.keys(window.pywebview.api).sort().join(','),
                isMock: bridge.isMock,
                images: S.images.length,
                listRows: document.querySelectorAll('.list .item').length,
                presets: document.querySelectorAll('.preset').length,
                sizeItems: document.querySelectorAll('#sizeList .dd-item').length,
                nativeSelects: document.querySelectorAll('select').length,
                title: document.querySelector('.title').textContent,
                cardShadow: getComputedStyle(document.querySelector('.card')).boxShadow,
                origSet: !!document.getElementById('imgOrig').getAttribute('src'),
                outPath: document.getElementById('inOut').value,
                ratioText: document.getElementById('ratioOut').textContent,
                dragRegion: !!document.querySelector('.pywebview-drag-region'),
                // 布局体检：预览图必须严格落在预览框内、且不能盖住导航与主按钮
                overflow: (function(){
                  var shots=[].slice.call(document.querySelectorAll('.preview .shot'));
                  var n=0;
                  shots.forEach(function(s){
                    var sr=s.getBoundingClientRect(), im=s.querySelector('img');
                    if(!im) return;
                    var ir=im.getBoundingClientRect();
                    if(ir.bottom>sr.bottom+1||ir.right>sr.right+1||
                       ir.top<sr.top-1||ir.left<sr.left-1) n++;
                  });
                  return n;
                })(),
                overlap: (function(){
                  var run=document.getElementById('btnRun').getBoundingClientRect();
                  var nav=document.querySelector('.nav').getBoundingClientRect();
                  var n=0;
                  [].slice.call(document.querySelectorAll('.preview img')).forEach(function(im){
                    var r=im.getBoundingClientRect();
                    [run,nav].forEach(function(b){
                      if(!(r.bottom<=b.top||b.bottom<=r.top)) n++;
                    });
                  });
                  return n;
                })()
            })""")
        except Exception as e:
            RESULT["probe_err"] = "%s: %s" % (type(e).__name__, e)

        grab("normal")

        # 提示框（Python → 前端）
        try:
            window.evaluate_js("window.pvAlert('测试标题','第一行\\n第二行')")
            time.sleep(0.4)
            RESULT["alert"] = window.evaluate_js("""JSON.stringify({
                shown: !document.getElementById('modal').hidden,
                title: document.getElementById('modalTitle').textContent })""")
            window.evaluate_js("document.getElementById('modal').hidden = true")
            # 进度推送
            window.evaluate_js("window.pvApplyProgress({running:true, progress:0.42, status:'处理中 3/7'})")
            time.sleep(0.3)
            RESULT["progress"] = window.evaluate_js("""JSON.stringify({
                bar: document.getElementById('bar').style.width,
                status: document.getElementById('status').textContent,
                runDisabled: document.getElementById('btnRun').disabled })""")
            window.evaluate_js("window.pvApplyProgress({running:false, progress:0, status:'就绪'})")
            # 逐张状态更新
            window.evaluate_js("window.pvSetItemStatus(1,'success')")
            time.sleep(0.2)
            RESULT["item_status"] = window.evaluate_js(
                "document.querySelectorAll('.list .item')[1].querySelector('.mark').textContent")
        except Exception as e:
            RESULT["push_err"] = "%s: %s" % (type(e).__name__, e)

        # 自绘下拉：展开 → 截图 → 选中 → 确认收起
        try:
            window.evaluate_js("document.getElementById('sizeDd').click()")
            time.sleep(0.4)
            RESULT["dd_open"] = window.evaluate_js("""JSON.stringify({
                open: !document.getElementById('sizeList').hidden,
                expanded: document.getElementById('sizeDd').getAttribute('aria-expanded') })""")
            grab("dropdown")
            window.evaluate_js(
                "document.querySelectorAll('#sizeList .dd-item')[2].click()")
            time.sleep(0.8)
            RESULT["dd_after"] = window.evaluate_js("""JSON.stringify({
                closed: document.getElementById('sizeList').hidden,
                label: document.getElementById('sizeValue').textContent })""")
        except Exception as e:
            RESULT["dd_err"] = "%s: %s" % (type(e).__name__, e)

        # 拖拽落点（模拟：文件夹 → 目录扫描；重复文件 → 去重）
        try:
            api.add_paths([src])
            n1 = len(api._images)
            one = [os.path.join(src, f) for f in os.listdir(src)
                   if f.lower().endswith(('.jpg', '.png'))][:1]
            api.add_paths(one)
            RESULT["drop_logic"] = "目录后 %d 张，再拖同一张后 %d 张" % (n1, len(api._images))
        except Exception as e:
            RESULT["drop_err"] = "%s: %s" % (type(e).__name__, e)

        finally:
            time.sleep(0.4)
            window.destroy()

    webview.start(debug=False)

    print(json.dumps(RESULT, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
