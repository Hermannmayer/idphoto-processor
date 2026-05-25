# ID Photo Processor / 证件照批量处理工具

自动检测人脸、校正歪头、智能裁切并压缩到指定尺寸和大小。支持**图形界面**和**命令行**两种方式。

## 功能

- **人脸检测** — OpenCV Haar Cascade 自动定位人脸
- **旋转校正** — 根据双眼位置自动摆正歪头
- **人脸大小归一化** — 不同距离拍摄的照片输出后中人脸占比一致
- **智能裁切** — 以人脸为中心，预留肩部空间
- **自动压缩** — 二分法调整 JPEG quality，保证文件大小符合要求
- **图形界面** — 文件夹选择、拖入图片、批量处理、原图/处理后对比预览

## 安装

```bash
pip install -r requirements.txt
```

依赖：Pillow, opencv-python, customtkinter, windnd（可选，用于拖放支持）

## 使用

### 图形界面

```bash
python gui.py
```

1. 选择输入文件夹或点击「添加图片」
2. 选择输出文件夹
3. 选择预设尺寸（小1寸/1寸/大一寸/小2寸/2寸/大2寸/自定义）
4. 设置最大文件大小
5. 点击「开始处理」

### 命令行

```bash
# 处理单张图片
python batch.py input.jpg -o output.jpg

# 批量处理目录
python batch.py input_dir/ -o output_dir/

# 批量处理目录（覆盖已有文件）
python batch.py input_dir/ -o output_dir/ --overwrite
```

## 预设尺寸

| 名称 | 像素 (px) | 物理尺寸 |
|------|-----------|----------|
| 默认 | 190×260 | — |
| 小1寸 | 260×378 | 22×32mm |
| 1寸 | 295×413 | 25×35mm |
| 大一寸 | 390×567 | 33×48mm |
| 小2寸 | 413×531 | 35×45mm |
| 2寸 | 413×579 | 35×49mm |
| 大2寸 | 413×626 | 35×53mm |

## 项目结构

```
├── process.py     # 核心处理：人脸检测 → 旋转校正 → 裁切 → 压缩
├── batch.py       # 命令行批量处理
├── gui.py         # 图形界面（CustomTkinter）
└── requirements.txt
```
