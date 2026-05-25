# ID Photo Processor

批量证件照处理工具。自动检测人脸、校正歪头、智能裁切并压缩到指定尺寸和大小。

## 功能

- 人脸检测（MediaPipe）→ 自动定位人脸中心
- 旋转校正 → 根据两眼连线自动摆正歪头
- 人脸大小归一化 → 不同拍摄距离的照片输出后人脸占比一致
- 智能裁切 → 以人脸为中心，预留肩部空间
- 自动压缩 → 二分法调整 JPEG quality，保证 <20KB

## 安装

```bash
pip install -r requirements.txt
```

## 使用

```bash
# 处理单张图片
python batch.py input.jpg -o output.jpg

# 批量处理目录
python batch.py input_dir/ -o output_dir/

# 批量处理目录（覆盖已有文件）
python batch.py input_dir/ -o output_dir/ --overwrite
```

## 输出

- 尺寸：190 × 260 px
- 格式：JPEG
- 大小：< 20KB
- 人脸居中、未经拉伸变形
