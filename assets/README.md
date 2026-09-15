# assets/

## 应用图标

把图标原图存成 **`assets/icon-source.png`**（方形、建议 1024×1024 以上），然后运行：

```bash
.venv/Scripts/python.exe tools/make_icon.py
```

会在同目录生成：

- `icon.ico` —— 内含 16/24/32/48/64/128/256 七档尺寸，供 PyInstaller 与 Inno Setup 使用
- `icon-256.png` —— 256 像素预览图，方便肉眼核对

生成后重新打包即可，图标会出现在 exe、任务栏、桌面快捷方式、开始菜单和安装程序上。

> **改图标只需重跑 `tools/make_icon.py`，不用改任何配置** ——
> `idphoto-processor.spec` 的 `EXE(icon=...)` 与 `installer/app.iss` 的
> `SetupIconFile` 都指向 `assets/icon.ico`。两个文件都做了"不存在就跳过"的处理，
> 所以还没放图标时也能正常构建（只是用 PyInstaller 的默认图标）。

### 原图带水印 / 需要裁边

```bash
# 裁掉底部 10%（例如去掉右下角的水印）
.venv/Scripts/python.exe tools/make_icon.py --trim-bottom 10

# 四周各裁掉 5%
.venv/Scripts/python.exe tools/make_icon.py --trim-edges 5
```

非正方形的原图会被**居中裁成正方形**（而不是压扁），避免图标变形。
