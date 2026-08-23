"""证件照核心处理：人脸检测(YuNet) → 旋转校正 → 智能裁切 → 缩放 → 压缩"""

import io
import math
import os
import sys
import threading

import cv2
import numpy as np
from PIL import Image, ImageOps

# 默认输出参数
DEFAULT_W = 190
DEFAULT_H = 260
DEFAULT_MAX_SIZE_KB = 20
HEAD_RATIO = 0.60         # 头部总高（发顶→下巴）占画面高度的比例（GA/T 461 通用区间 0.60-0.75H）
HEAD_TOP = 0.10           # 头顶到照片上边缘的留白（通用 0.08-0.15H，取 0.10H）
                          # 头顶 10% + 头 60% ⇒ 下巴距下边约 30%，为肩膀/颈部留足空间

MIN_ROTATE_DEG = 2.5      # 倾斜超过该角度才校正（避免对接近竖直的照片误转/过转）
MAX_ROTATE_DEG = 45.0     # 超过该角度视为侧脸/异常，不校正
EYE_LEVEL_TOL = 0.02      # 规范校验：双眼高度差 ≤ 0.02H（超过才需要旋转摆正）
DETECT_MAX = 640          # 检测用图最长边上限（人脸检测无需原图分辨率，能大幅提速）

# 由眼距 d 推导头部区域的系数（对真人证件照实测校准：眼线→发顶约 1.5-2.2d，取 2.0d 含头发）
_HEAD_ABOVE_EYE = 2.0     # 发顶到眼线的距离 ≈ 2.0·d（含头发，宁多勿切）
_CHIN_BELOW_MOUTH = 0.55  # 下巴到嘴角中间线的距离 ≈ 0.55·d（嘴角线在眼线下方约 1.07·d）
_HEAD_HALF_W = 1.0        # 头部半宽 ≈ 1.0·d（仅用于预览画框）

# YuNet 人脸检测模型（OpenCV DNN，输出人脸框 + 5 关键点：右眼/左眼/鼻尖/右嘴角/左嘴角）
_YUNET_MODEL = "face_detection_yunet_2023mar.onnx"
if getattr(sys, 'frozen', False):
    _MODEL_DIR = os.path.join(sys._MEIPASS, "models")
else:
    _MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
_YUNET_PATH = os.path.join(_MODEL_DIR, _YUNET_MODEL)

# 检测器按线程缓存，避免批量/预览并发时共享同一实例发生竞态
_yunet_local = threading.local()


def _get_yunet():
    """返回当前线程的 YuNet 检测器（懒加载并缓存）。"""
    det = getattr(_yunet_local, "detector", None)
    if det is None:
        det = cv2.FaceDetectorYN.create(
            _YUNET_PATH, "", (320, 320),
            score_threshold=0.6, nms_threshold=0.3, top_k=5000,
        )
        if det is None:
            raise RuntimeError(f"无法加载人脸检测模型：{_YUNET_PATH}\n"
                               f"请确认 models/{_YUNET_MODEL} 存在（随 EXE 一起分发）。")
        _yunet_local.detector = det
    return det


def _rotate_point(pt_x, pt_y, angle_deg, orig_w, orig_h, rot_w, rot_h):
    """将原图上的点映射到旋转后图像（expand 画布）上的坐标。"""
    rad = math.radians(angle_deg)
    dx = pt_x - orig_w / 2
    dy = pt_y - orig_h / 2
    nx = dx * math.cos(rad) - dy * math.sin(rad) + rot_w / 2
    ny = dx * math.sin(rad) + dy * math.cos(rad) + rot_h / 2
    return nx, ny


def _detect_face(img: Image.Image):
    """返回 (face_cx, face_cy, face_height, angle_deg, info) 或 None

    info 是一个 dict：
      'bbox'      — YuNet 原始人脸框 (x, y, w, h)
      'head'      — 推导的完整头部区域 (x1, y1, x2, y2)，含头发，用于裁切与预览
      'eyes'      — (re_x, re_y, le_x, le_y) 双眼像素坐标
      'keypoints' — 通过校验时的双眼（用于预览红点）
    坐标均为原图像素值。
    """
    rgb = np.asarray(img.convert("RGB"))
    h_img, w_img = rgb.shape[:2]

    # 降采样检测，结果换算回原图坐标
    inv = 1.0
    longest = max(w_img, h_img)
    if longest > DETECT_MAX:
        inv = DETECT_MAX / longest
        small = cv2.resize(rgb, (max(1, int(w_img * inv)), max(1, int(h_img * inv))),
                           interpolation=cv2.INTER_AREA)
    else:
        small = rgb
    bgr = small[:, :, ::-1]  # RGB → BGR

    yunet = _get_yunet()
    yunet.setInputSize((bgr.shape[1], bgr.shape[0]))
    _, faces = yunet.detect(bgr)

    if faces is None or len(faces) == 0:
        return None

    # 取面积最大的人脸；faces 每行: [x,y,w,h, re_x,re_y, le_x,le_y, nose, rmouth, lmouth, score]
    best = max(faces, key=lambda f: f[2] * f[3])
    to_orig = 1.0 / inv
    fx = float(best[0]) * to_orig
    fy = float(best[1]) * to_orig
    fw = float(best[2]) * to_orig
    fh = float(best[3]) * to_orig
    re_x, re_y = float(best[4]) * to_orig, float(best[5]) * to_orig
    le_x, le_y = float(best[6]) * to_orig, float(best[7]) * to_orig

    face_cx = fx + fw / 2
    face_cy = fy + fh / 2

    # 由眼距/嘴角 + 图片内容推导完整头部区域（比 bbox 稳定）
    rm_x, rm_y = float(best[10]) * to_orig, float(best[11]) * to_orig
    lm_x, lm_y = float(best[12]) * to_orig, float(best[13]) * to_orig
    d = math.hypot(le_x - re_x, le_y - re_y)
    eye_x = (re_x + le_x) / 2
    eye_y = (re_y + le_y) / 2
    mouth_y = (rm_y + lm_y) / 2

    # 发顶：直接测内容。在眼线上方中央带找"明显暗于背景"的首行（=头发的顶部），
    # 自适应发型/裁剪；找不到（如深色背景）则退回按眼距估算。
    gray = rgb.mean(2)
    bg = float(np.argmax(np.bincount(gray.astype(np.uint8).ravel(), minlength=256)))
    hy1 = eye_y - 2.0 * d                                   # 兜底（含头发）
    y_hi = max(0, int(eye_y - 3.6 * d))
    y_lo = max(0, int(eye_y) - 5)
    bx0 = max(0, int(eye_x - 1.3 * d))
    bx1 = min(w_img, int(eye_x + 1.3 * d))
    for y in range(y_hi, y_lo):
        if gray[y, bx0:bx1].min() < bg - 55:
            hy1 = y
            break
    hy1 = min(hy1, fy)                                    # 不高于人脸框上沿
    hy2 = mouth_y + _CHIN_BELOW_MOUTH * d                 # 下巴（嘴角线在眼线下方约 1.07·d）
    hy1 = max(0.0, hy1)
    head = (int(round(eye_x - _HEAD_HALF_W * d)), int(round(hy1)),
            int(round(eye_x + _HEAD_HALF_W * d)), int(round(hy2)))

    # 双眼合理性校验（YuNet 已较稳，这只是兜底），通过才允许旋转
    angle_deg = 0.0
    keypoints = []
    if (fw * 0.2 <= d <= fw * 1.0 and       # 眼距与脸宽相称
            abs(le_y - re_y) < fh * 0.2 and  # 两眼的垂直间距不能太大
            eye_y < fy + fh * 0.6):          # 眼线应在人脸框上半部分
        keypoints = [(re_x, re_y), (le_x, le_y)]
        angle_deg = math.degrees(math.atan2(le_y - re_y, le_x - re_x))

    info = {
        "bbox": (int(fx), int(fy), int(fw), int(fh)),
        "head": head,
        "eyes": (re_x, re_y, le_x, le_y),
        "mouth": (rm_x, rm_y, lm_x, lm_y),
        "top_y": hy1,
        "chin_y": hy2,
        "keypoints": keypoints,
    }
    return face_cx, face_cy, fh, angle_deg, info


def detect_face(img: Image.Image):
    """公开的人脸检测接口，用于 GUI 预览绘制。"""
    return _detect_face(img)


def _point_in_content(px, py, angle_deg, orig_w, orig_h, rot_w, rot_h):
    """旋转后画布上的点是否落在真实图片内容内（排除 expand 产生的白色三角区）。"""
    cx, cy = rot_w / 2, rot_h / 2
    rad = math.radians(-angle_deg)
    dx = px - cx
    dy = py - cy
    x = dx * math.cos(rad) - dy * math.sin(rad)
    y = dx * math.sin(rad) + dy * math.cos(rad)
    return -orig_w / 2 <= x <= orig_w / 2 and -orig_h / 2 <= y <= orig_h / 2


def _fit_crop_scale(cx, cy, crop_w, crop_h, angle_deg,
                    orig_w, orig_h, rot_w, rot_h):
    """求最大 s∈(0,1]，使以 (cx,cy) 为中心、尺寸 s·crop_w × s·crop_h 的
    矩形完全落在真实内容内。尺寸不足时缩小（人脸占比略放宽），保证填满整框、无白三角。
    """
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        hw = mid * crop_w / 2
        hh = mid * crop_h / 2
        pts = ((cx - hw, cy - hh), (cx + hw, cy - hh),
               (cx - hw, cy + hh), (cx + hw, cy + hh))
        if all(_point_in_content(px, py, angle_deg, orig_w, orig_h, rot_w, rot_h)
               for px, py in pts):
            lo = mid
        else:
            hi = mid
    # 留 0.1% 余量，配合下面的向内收缩，彻底避免取整露白
    return lo * 0.999


def _fit_crop_in_content(cx, cy, crop_w, crop_h, angle_deg,
                         orig_w, orig_h, rot_w, rot_h):
    """返回落在真实内容内的裁切框 (x1, y1, x2, y2)。

    优先平移（保持裁切尺寸 → 头部占比不被动放大，尽可能保留顶部/肩部留白）；
    平移后仍超出内容（如旋转产生的白色三角区）才整体缩小。
    """
    x1, y1 = cx - crop_w / 2, cy - crop_h / 2
    x2, y2 = cx + crop_w / 2, cy + crop_h / 2

    dx = dy = 0.0
    if x1 < 0:
        dx = -x1
    elif x2 > rot_w:
        dx = rot_w - x2
    if y1 < 0:
        dy = -y1
    elif y2 > rot_h:
        dy = rot_h - y2
    x1 += dx
    x2 += dx
    y1 += dy
    y2 += dy

    if (x1 >= 0 and y1 >= 0 and x2 <= rot_w and y2 <= rot_h and
            all(_point_in_content(px, py, angle_deg, orig_w, orig_h, rot_w, rot_h)
                for px, py in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)))):
        return (max(0, int(round(x1)) + 1), max(0, int(round(y1)) + 1),
                min(rot_w, int(round(x2)) - 1), min(rot_h, int(round(y2)) - 1))

    # 平移放不下（窗口比内容还大，或落在旋转白角区）→ 绕中心缩小
    s = _fit_crop_scale(cx, cy, crop_w, crop_h, angle_deg, orig_w, orig_h, rot_w, rot_h)
    cw, ch = crop_w * s, crop_h * s
    return (max(0, int(round(cx - cw / 2)) + 1), max(0, int(round(cy - ch / 2)) + 1),
            min(rot_w, int(round(cx + cw / 2)) - 1), min(rot_h, int(round(cy + ch / 2)) - 1))


def _compress_jpeg(img: Image.Image, max_bytes: int,
                   target_w=DEFAULT_W, target_h=DEFAULT_H) -> bytes:
    """二分法调整 JPEG quality 使文件大小 ≤ max_bytes。"""
    buf = io.BytesIO()
    lo, hi = 1, 95
    best = None

    while lo <= hi:
        mid = (lo + hi) // 2
        buf.seek(0)
        buf.truncate()
        img.save(buf, format="JPEG", quality=mid, optimize=True, exif=b"")
        size = buf.tell()
        if size <= max_bytes:
            best = buf.getvalue()
            lo = mid + 1
        else:
            hi = mid - 1

    if best is not None:
        return best
    # quality=1 仍超限 → 缩小尺寸
    scale = math.sqrt(max_bytes / len(buf.getvalue())) * 0.95
    new_w = max(1, int(target_w * scale))
    new_h = max(1, int(target_h * scale))
    smaller = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    smaller.save(buf, format="JPEG", quality=1, optimize=True, exif=b"")
    return buf.getvalue()


def compress_to_bytes(img: Image.Image, max_size_kb=DEFAULT_MAX_SIZE_KB,
                      target_w=DEFAULT_W, target_h=DEFAULT_H) -> bytes:
    """对已处理好的 Image 压缩为 JPEG bytes（预览可避免重复整条流水线）。"""
    return _compress_jpeg(img, max_size_kb * 1024,
                          target_w=target_w, target_h=target_h)


def process_image(img: Image.Image, target_w=DEFAULT_W, target_h=DEFAULT_H,
                  head_ratio=HEAD_RATIO, head_top=HEAD_TOP,
                  face_result=None) -> Image.Image:
    """处理单张图片，返回符合要求的 Image 对象。

    face_result 可传入 detect_face() 的结果以跳过重复检测（预览复用）。
    """
    img = ImageOps.exif_transpose(img) or img

    # RGBA → RGB（JPEG 不支持透明度），透明部分填充白色
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])  # alpha 通道作蒙版
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    orig_w, orig_h = img.size

    if face_result is None:
        face_result = _detect_face(img)

    if face_result is None:
        # 未检出人脸：以图片中心的窗口兜底（内容＝整图，天然无白三角）
        angle = 0.0
        raw = img
        rot_w, rot_h = orig_w, orig_h
        crop_h = orig_h * 0.80
        crop_w = crop_h * target_w / target_h
        cx, cy = orig_w / 2, orig_h / 2
    else:
        _, _, _, angle, info = face_result
        ex1, ey1, ex2, ey2 = info["eyes"]
        rm_x, rm_y, lm_x, lm_y = info["mouth"]
        bx, by, _, _ = info["bbox"]
        eye_x0 = (ex1 + ex2) / 2              # 原图坐标系下的眼心 x
        top_y = info["top_y"]                 # 原图坐标系下的发顶（内容法测得）

        if (MIN_ROTATE_DEG < abs(angle) <= MAX_ROTATE_DEG and
                abs(ey1 - ey2) > EYE_LEVEL_TOL * orig_h):
            # 旋转并扩展画布；空白区域用白色填充，但裁切会约束在真实内容内
            raw = img.rotate(angle, expand=True, resample=Image.BICUBIC,
                             fillcolor=(255, 255, 255))
            rot_w, rot_h = raw.size
            ex1, ey1 = _rotate_point(ex1, ey1, angle, orig_w, orig_h, rot_w, rot_h)
            ex2, ey2 = _rotate_point(ex2, ey2, angle, orig_w, orig_h, rot_w, rot_h)
            rm_x, rm_y = _rotate_point(rm_x, rm_y, angle, orig_w, orig_h, rot_w, rot_h)
            lm_x, lm_y = _rotate_point(lm_x, lm_y, angle, orig_w, orig_h, rot_w, rot_h)
            # 发顶 / 人脸框上沿随旋转映射到新画布
            _, top_y = _rotate_point(eye_x0, top_y, angle, orig_w, orig_h, rot_w, rot_h)
            _, by_t = _rotate_point(bx, by, angle, orig_w, orig_h, rot_w, rot_h)
        else:
            angle = 0.0
            raw = img
            rot_w, rot_h = orig_w, orig_h
            by_t = by

        # 头部区域（含头发/下巴）与理想裁切窗口
        d = math.hypot(ex2 - ex1, ey2 - ey1)
        eye_x = (ex1 + ex2) / 2
        eye_y = (ey1 + ey2) / 2
        mouth_y = (rm_y + lm_y) / 2
        hy1 = min(top_y, by_t)                 # 发顶（内容法测得，框上沿仅作兜底）
        hy2 = mouth_y + _CHIN_BELOW_MOUTH * d  # 下巴
        head_h = hy2 - hy1

        crop_h = head_h / head_ratio
        crop_w = crop_h * target_w / target_h
        cx = eye_x
        cy = (hy1 - head_top * crop_h) + crop_h / 2     # 头顶留白（下移预留肩部）

    # 窗口先按内容尺寸封顶（保持宽高比）：当理想窗口比源图还高时，
    # 不缩小把头放大，而是保留源图自带的上方留白（头占比随之略大，仍符合 0.60-0.75H）
    # 用原图尺寸 orig_w×orig_h（旋转后画布 expand 会更大，内容仍以原图尺寸为上限）
    _cap = min(1.0, orig_h / max(crop_h, 1.0), orig_w / max(crop_w, 1.0))
    if _cap < 1.0:
        crop_w *= _cap
        crop_h *= _cap

    # 放入真实内容：优先平移保留尺寸（留白最大化），必要时缩小防白
    x1, y1, x2, y2 = _fit_crop_in_content(cx, cy, crop_w, crop_h, angle,
                                          orig_w, orig_h, rot_w, rot_h)

    cropped = raw.crop((x1, y1, x2, y2))
    scaled = cropped.resize((target_w, target_h), Image.LANCZOS)
    return scaled


def process_image_to_bytes(img: Image.Image, target_w=DEFAULT_W, target_h=DEFAULT_H,
                           max_size_kb=DEFAULT_MAX_SIZE_KB, head_ratio=HEAD_RATIO,
                           head_top=HEAD_TOP, face_result=None) -> bytes:
    """处理并压缩，返回 JPEG bytes。"""
    processed = process_image(img, target_w=target_w, target_h=target_h,
                              head_ratio=head_ratio, head_top=head_top,
                              face_result=face_result)
    return _compress_jpeg(processed, max_size_kb * 1024,
                          target_w=target_w, target_h=target_h)