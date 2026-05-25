"""证件照核心处理：人脸检测 → 旋转校正 → 智能裁切 → 缩放 → 压缩"""

import math
import io

import mediapipe as mp
from PIL import Image, ImageOps

# 输出参数
TARGET_W = 190
TARGET_H = 260
MAX_SIZE_KB = 20
FACE_RATIO = 0.62        # 人脸在最终输出中的占比
SHIFT_DOWN = 0.05         # 人脸中心下移比例（预留肩部空间）
CONFIDENCE_THRESHOLD = 0.5

mp_face_detection = mp.solutions.face_detection


def _detect_face(img: Image.Image):
    """返回 (face_cx, face_cy, face_height, angle_deg) 或 None"""
    w, h = img.size
    rgb = img.convert("RGB")
    with mp_face_detection.FaceDetection(
        model_selection=1, min_detection_confidence=CONFIDENCE_THRESHOLD
    ) as fd:
        results = fd.process(rgb)

    if not results.detections:
        return None

    # 取置信度最高的人脸
    det = max(results.detections, key=lambda d: d.score[0])

    bbox = det.location_data.relative_bounding_box
    face_cx = (bbox.xmin + bbox.width / 2) * w
    face_cy = (bbox.ymin + bbox.height / 2) * h
    face_h = bbox.height * h

    # 关键点: right_eye(0), left_eye(1)
    kp = det.location_data.relative_keypoints
    reye = (kp[0].x * w, kp[0].y * h)
    leye = (kp[1].x * w, kp[1].y * h)

    angle_deg = math.degrees(math.atan2(leye[1] - reye[1], leye[0] - reye[0]))
    return face_cx, face_cy, face_h, angle_deg


def _rotate_point(
    pt_x: float, pt_y: float,
    angle_deg: float,
    orig_w: int, orig_h: int,
    rot_w: int, rot_h: int,
):
    """将原图上的点映射到旋转后图像上的坐标。"""
    rad = math.radians(angle_deg)
    dx = pt_x - orig_w / 2
    dy = pt_y - orig_h / 2
    nx = dx * math.cos(rad) - dy * math.sin(rad) + rot_w / 2
    ny = dx * math.sin(rad) + dy * math.cos(rad) + rot_h / 2
    return nx, ny


def _compress_jpeg(img: Image.Image, max_bytes: int) -> bytes:
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
            lo = mid + 1  # 尝试更高 quality
        else:
            hi = mid - 1

    if best is not None:
        return best
    # quality=1 仍超限 → 缩小尺寸
    scale = math.sqrt(max_bytes / len(buf.getvalue())) * 0.95
    new_w = max(1, int(TARGET_W * scale))
    new_h = max(1, int(TARGET_H * scale))
    smaller = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    smaller.save(buf, format="JPEG", quality=1, optimize=True)
    return buf.getvalue()


def process_image(img: Image.Image) -> Image.Image:
    """处理单张图片，返回符合要求的 Image 对象。"""
    img = ImageOps.exif_transpose(img) or img
    orig_w, orig_h = img.size

    result = _detect_face(img)

    if result is None:
        # 未检测到人脸 → 居中裁切，保留尽可能多的内容
        angle = 0.0
        face_cx, face_cy = orig_w / 2, orig_h / 2
        face_h = min(orig_w, orig_h) * 0.4  # 保守估计
        raw = img
        rot_w, rot_h = orig_w, orig_h
    else:
        face_cx, face_cy, face_h, angle = result
        # 旋转校正
        raw = img.rotate(angle, expand=True, resample=Image.BICUBIC)
        rot_w, rot_h = raw.size
        # 映射人脸中心
        face_cx, face_cy = _rotate_point(
            face_cx, face_cy, angle,
            orig_w, orig_h, rot_w, rot_h,
        )

    # 以人脸为中心计算裁切区，按 FACE_RATIO 归一化人脸大小
    crop_h = face_h / FACE_RATIO
    crop_w = crop_h * TARGET_W / TARGET_H

    # 下移裁切中心
    cx = face_cx
    cy = face_cy + crop_h * SHIFT_DOWN

    # 计算裁切矩形
    x1 = cx - crop_w / 2
    y1 = cy - crop_h / 2
    x2 = cx + crop_w / 2
    y2 = cy + crop_h / 2

    # 如果裁切区超出图像边界 → 反向偏移（尽量保留内容）
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
    # 再次 clamp（如果图像本身太小）
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(rot_w, x2)
    y2 = min(rot_h, y2)

    # 裁切 & 缩放
    cropped = raw.crop((int(x1), int(y1), int(x2), int(y2)))
    scaled = cropped.resize((TARGET_W, TARGET_H), Image.LANCZOS)
    return scaled


def process_image_to_bytes(img: Image.Image) -> bytes:
    """处理并压缩到 <20KB，返回 JPEG bytes。"""
    processed = process_image(img)
    return _compress_jpeg(processed, MAX_SIZE_KB * 1024)
