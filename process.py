"""证件照核心处理：人脸检测 → 旋转校正 → 智能裁切 → 缩放 → 压缩"""

import math
import io
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageOps

# 默认输出参数
DEFAULT_W = 190
DEFAULT_H = 260
DEFAULT_MAX_SIZE_KB = 20
FACE_RATIO = 0.48          # 人脸在最终输出中的占比（保证全头约占 2/3）
SHIFT_DOWN = 0.03           # 人脸中心下移比例（预留肩部空间）

# OpenCV 级联分类器路径（兼容 PyInstaller 打包）
if getattr(sys, 'frozen', False):
    _CASCADE_DIR = sys._MEIPASS + os.sep
else:
    _CASCADE_DIR = cv2.data.haarcascades


def _detect_face(img: Image.Image):
    """返回 (face_cx, face_cy, face_height, angle_deg, info) 或 None

    info 是一个 dict：{'bbox': (x, y, w, h), 'keypoints': [(rx,ry), (lx,ly)]}
    坐标均为像素值。
    """
    gray = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    h_img, w_img = gray.shape

    # ── 人脸检测 ──
    face_cascade = cv2.CascadeClassifier(_CASCADE_DIR + "haarcascade_frontalface_default.xml")
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                          minSize=(int(w_img * 0.1), int(h_img * 0.1)))

    if len(faces) == 0:
        return None

    # 取面积最大的人脸
    (fx, fy, fw, fh) = max(faces, key=lambda f: f[2] * f[3])

    face_cx = fx + fw / 2
    face_cy = fy + fh / 2
    face_h = fh

    # ── 眼睛检测（在人脸区域内）──
    roi_gray = gray[fy:fy + fh, fx:fx + fw]
    eye_cascade = cv2.CascadeClassifier(_CASCADE_DIR + "haarcascade_eye.xml")
    eyes = eye_cascade.detectMultiScale(roi_gray, scaleFactor=1.05, minNeighbors=8,
                                        minSize=(max(10, int(fw * 0.08)), max(10, int(fh * 0.08))))

    angle_deg = 0.0
    keypoints = []

    if len(eyes) >= 2:
        # 按水平位置排序，取左右两端各一个作为左右眼
        eyes = sorted(eyes, key=lambda e: e[0])
        # 左侧可能检测到多个，取靠左的；右侧取靠右的
        left_candidates = [e for e in eyes if e[0] < fw * 0.5]
        right_candidates = [e for e in eyes if e[0] >= fw * 0.5]

        if left_candidates and right_candidates:
            # left_candidates: 人脸左半边的眼睛 → 人的右眼
            # right_candidates: 人脸右半边的眼睛 → 人的左眼
            re_x = fx + left_candidates[0][0] + left_candidates[0][2] / 2
            re_y = fy + left_candidates[0][1] + left_candidates[0][3] / 2
            le_x = fx + right_candidates[-1][0] + right_candidates[-1][2] / 2
            le_y = fy + right_candidates[-1][1] + right_candidates[-1][3] / 2

            keypoints = [(re_x, re_y), (le_x, le_y)]  # (右眼, 左眼)

            angle_deg = math.degrees(math.atan2(le_y - re_y, le_x - re_x))

    info = {
        "bbox": (fx, fy, fw, fh),
        "keypoints": keypoints,
    }
    return face_cx, face_cy, face_h, angle_deg, info


def detect_face(img: Image.Image):
    """公开的人脸检测接口，用于 GUI 预览绘制。"""
    return _detect_face(img)


def _rotate_point(pt_x, pt_y, angle_deg, orig_w, orig_h, rot_w, rot_h):
    """将原图上的点映射到旋转后图像上的坐标。"""
    rad = math.radians(angle_deg)
    dx = pt_x - orig_w / 2
    dy = pt_y - orig_h / 2
    nx = dx * math.cos(rad) - dy * math.sin(rad) + rot_w / 2
    ny = dx * math.sin(rad) + dy * math.cos(rad) + rot_h / 2
    return nx, ny


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
        img.save(buf, format="JPEG", quality=mid, optimize=True)
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
    smaller.save(buf, format="JPEG", quality=1, optimize=True)
    return buf.getvalue()


def process_image(img: Image.Image, target_w=DEFAULT_W, target_h=DEFAULT_H,
                  face_ratio=FACE_RATIO, shift_down=SHIFT_DOWN) -> Image.Image:
    """处理单张图片，返回符合要求的 Image 对象。"""
    img = ImageOps.exif_transpose(img) or img
    orig_w, orig_h = img.size

    result = _detect_face(img)

    if result is None:
        angle = 0.0
        face_cx, face_cy = orig_w / 2, orig_h / 2
        face_h = min(orig_w, orig_h) * 0.4
        raw = img
        rot_w, rot_h = orig_w, orig_h
    else:
        face_cx, face_cy, face_h, angle, _ = result

        if abs(angle) > 1.0:
            # 旋转（不扩展），再适当放大让黑角离开裁切区域
            raw = img.rotate(angle, expand=False, resample=Image.BICUBIC)
            face_cx, face_cy = _rotate_point(
                face_cx, face_cy, angle,
                orig_w, orig_h, orig_w, orig_h,
            )
            angle_rad = math.radians(abs(angle))
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)
            aspect = max(orig_w / orig_h, orig_h / orig_w)
            scale = 1.0 / (cos_a - sin_a * aspect)
            if 1.01 < scale < 1.5:
                new_size = (int(orig_w * scale), int(orig_h * scale))
                raw = raw.resize(new_size, Image.LANCZOS)
                rot_w, rot_h = new_size
                face_cx *= scale
                face_cy *= scale
                face_h *= scale
            else:
                rot_w, rot_h = orig_w, orig_h
        else:
            raw = img.rotate(angle, expand=False, resample=Image.BICUBIC)
            rot_w, rot_h = orig_w, orig_h

    crop_h = face_h / face_ratio
    crop_w = crop_h * target_w / target_h

    cx = face_cx
    cy = face_cy + crop_h * shift_down

    x1 = cx - crop_w / 2
    y1 = cy - crop_h / 2
    x2 = cx + crop_w / 2
    y2 = cy + crop_h / 2

    if x1 < 0:
        x2 += -x1
        x1 = 0
    if y1 < 0:
        y2 += -y1
        y1 = 0
    if x2 > rot_w:
        x1 -= (x2 - rot_w)
        x2 = rot_w
    if y2 > rot_h:
        y1 -= (y2 - rot_h)
        y2 = rot_h
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(rot_w, x2)
    y2 = min(rot_h, y2)

    cropped = raw.crop((int(x1), int(y1), int(x2), int(y2)))
    scaled = cropped.resize((target_w, target_h), Image.LANCZOS)
    return scaled


def process_image_to_bytes(img: Image.Image, target_w=DEFAULT_W, target_h=DEFAULT_H,
                           max_size_kb=DEFAULT_MAX_SIZE_KB, face_ratio=FACE_RATIO,
                           shift_down=SHIFT_DOWN) -> bytes:
    """处理并压缩，返回 JPEG bytes。"""
    processed = process_image(img, target_w=target_w, target_h=target_h,
                              face_ratio=face_ratio, shift_down=shift_down)
    return _compress_jpeg(processed, max_size_kb * 1024,
                          target_w=target_w, target_h=target_h)
