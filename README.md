# ID Photo Processor / 证件照批量处理工具

自动检测人脸、校正歪头、智能裁切并压缩到指定尺寸和大小。支持**图形界面**和**命令行**两种方式。

## 功能

- **人脸检测** — OpenCV YuNet（DNN）自动定位人脸与双眼关键点
- **旋转校正** — 根据双眼位置自动摆正歪头
- **人脸大小归一化** — 不同距离拍摄的照片输出后中人脸占比一致
- **智能裁切** — 以人脸为中心，预留肩部空间
- **自动压缩** — 二分法调整 JPEG quality，保证文件大小符合要求
- **图形界面** — 文件夹选择、批量处理、原图/处理后对比预览
- **子文件夹递归** — 选择输入文件夹时，子文件夹里的照片也会一并载入
- **单实例运行** — 重复双击图标只会激活已有窗口，不会开出多个实例

## 安装

### 普通用户（推荐）

下载 `IDPhotoProcessor-Setup-<版本>.exe` 运行即可：

- 装到 `C:\IDPhotoProcessor`，自动创建桌面图标与开始菜单项
- 升级时直接再运行一次新版本安装包，会**覆盖**旧版本（正在运行的程序会被自动关闭）
- 卸载走「设置 → 应用」，或在安装目录运行 `unins000.exe`

> 安装需要管理员权限（装到 C 盘根目录），会弹一次 UAC 提示，属正常现象。

### 从源码运行

```bash
pip install -r requirements.txt
python gui.py
```

依赖：Pillow, opencv-python, customtkinter

> 人脸检测依赖 YuNet 模型 `models/face_detection_yunet_2023mar.onnx`（约 230 KB）。
> 该模型同时以 base64 内嵌在 `model_data.py` 中，**即便 `models/` 目录缺失或被杀毒软件
> 拦截，程序也会自动从内嵌数据恢复**，不会再出现 `Can't read ONNX file` 之类的报错。
>
> 更换模型文件后需重新生成内嵌数据：
>
> ```bash
> python tools/embed_model.py
> ```
>
> 打包前可用 `python tools/smoke_test.py` 做一次自检（依赖、模型、流水线）。


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
├── model_data.py           # 内嵌的 YuNet 模型（由 tools/embed_model.py 生成）
├── models/                 # YuNet 人脸检测模型原始文件
├── tools/
│   ├── embed_model.py      # 把 models/ 下的模型重新内嵌进 model_data.py
│   └── smoke_test.py       # 打包前自检：依赖 / 模型 / 流水线
├── installer/
│   └── app.iss             # Inno Setup 安装包脚本
├── requirements.txt
└── idphoto-processor.spec  # PyInstaller 打包配置（onedir）
```

## 打包

### 1. 编译程序（PyInstaller，onedir 模式）

```bash
pip install pyinstaller
python tools/smoke_test.py          # 先自检，避免打完包才发现跑不起来
pyinstaller --clean --noconfirm idphoto-processor.spec
```

产物在 `dist/idphoto-processor/`（免安装版，整个文件夹可直接拷走运行）。

> **为什么不用单文件(onefile)模式**：onefile 每次启动都要把两百多 MB、两千多个文件
> 解压到 `%TEMP%` 再运行，低配机上要几十秒且期间没有任何界面，客户等不及会反复点击，
> 还会因为解压中途被打断（杀软拦截 / 强制关进程）而出现缺文件的临时目录。
> onedir 装好后启动只需约 1 秒，不再有解压环节。
>
> spec 里还剔除了两个用不到的大家伙：`opencv_videoio_ffmpeg*.dll`（约 31 MB，
> PyInstaller 的 hook-cv2 会无差别收集 cv2 目录下所有 DLL）和 AVIF 编解码
> （约 8 MB）——本程序只处理静态图片并输出 JPEG。

### 2. 编译安装包（Inno Setup）

```bash
ISCC.exe /DAppVersion=1.0.0 installer\app.iss
```

产物在 `installer/out/IDPhotoProcessor-Setup-<版本>.exe`（约 48 MB）。

## 自动打包与发布（GitHub Actions）

仓库内置 `.github/workflows/release.yml`，推送到 GitHub 后自动在云端编译：

| 触发动作 | 结果 |
|---|---|
| 推 `master` 分支 | 编译，产出**安装程序**与**免安装版 zip** 两个构建工件（Actions 页面可下载） |
| 推格式为 `vX.Y.Z` 的**标签** | 编译并自动创建 **Release**，附有安装程序供直接下载 |
| 手动触发 | Actions 页面 → build-release → Run workflow |

版本号由 git 标签控制（`v1.0.0`、`v1.0.1`…），实现版本管理：

```bash
git tag v1.0.0          # 打标签
git push origin master  # 推代码
git push origin v1.0.0  # 推标签 → 触发自动打包 → 生成 Release
```

> CI 会自行下载安装 Inno Setup（GitHub 的 windows-latest 运行器自 2025-09 起不再预装）。

> 注意：受 Windows「受控文件夹访问」保护的程序（如桌面/文档）可能默认不允许写入。
> 若导出报 `Permission denied`，把使用的 python.exe（或打包后的 exe）加入
> 「Windows 安全中心 → 勒索软件防护 → 受控文件夹访问 → 允许应用」名单，或选择普通目录作为输出。

