# ID Photo Processor / 证件照批量处理工具

自动检测人脸、校正歪头、智能裁切并压缩到指定尺寸和大小。支持**图形界面**和**命令行**两种方式。

## 功能

- **人脸检测** — OpenCV YuNet（DNN）自动定位人脸与双眼关键点
- **旋转校正** — 根据双眼位置自动摆正歪头
- **人脸大小归一化** — 不同距离拍摄的照片输出后中人脸占比一致
- **智能裁切** — 以人脸为中心，预留肩部空间
- **自动压缩** — 二分法调整 JPEG quality，保证文件大小符合要求
- **图形界面** — 文件夹选择、拖入图片、批量处理、原图/处理后对比预览

## 安装

```bash
pip install -r requirements.txt
```

依赖：Pillow, opencv-python, customtkinter

> 人脸检测依赖 YuNet 模型文件 `models/face_detection_yunet_2023mar.onnx`（约 230 KB），
> 该文件已随仓库提供，打包为 EXE 时会被一并捆绑；请勿删除 `models/` 目录。

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
├── process.py              # 核心处理：人脸检测 → 旋转校正 → 裁切 → 压缩
├── batch.py                # 命令行批量处理
├── gui.py                  # 图形界面（CustomTkinter）
├── models/                 # YuNet 人脸检测模型（face_detection_yunet_2023mar.onnx）
├── requirements.txt
└── idphoto-processor.spec  # PyInstaller 打包配置
```

## 打包为 EXE

```bash
pip install pyinstaller
pyinstaller idphoto-processor.spec
```

生成的可执行文件在 `dist/idphoto-processor.exe`（单文件，YuNet 模型已随包捆绑）。

## 自动打包与发布（GitHub Actions）

仓库内置 `.github/workflows/release.yml`，推送到 GitHub 后自动在云端编译出 Windows 可执行文件：

| 触发动作 | 结果 |
|---|---|
| 推 `master` 分支 | 编译 exe，作为**构建工件**（仓库 Actions 页面可直接下载） |
| 推格式为 `vX.Y.Z` 的**标签** | 编译 exe 并自动创建 **Release**，附有 `idphoto-processor.exe` 供直接下载 |
| 手动触发 | Actions 页面 → build-release → Run workflow |

版本号由 git 标签控制（`v1.0.0`、`v1.0.1`…），实现版本管理：

```bash
git tag v1.0.0          # 打标签
git push origin master  # 推代码
git push origin v1.0.0  # 推标签 → 触发自动打包 → 生成 Release
```

> 注意：受 Windows「受控文件夹访问」保护的程序（如桌面/文档）可能默认不允许写入。
> 若导出报 `Permission denied`，把使用的 python.exe（或打包后的 exe）加入
> 「Windows 安全中心 → 勒索软件防护 → 受控文件夹访问 → 允许应用」名单，或选择普通目录作为输出。
