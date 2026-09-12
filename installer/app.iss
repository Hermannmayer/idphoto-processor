; 证件照批量处理工具 —— Inno Setup 安装脚本
;
; 由 .github/workflows/release.yml 调用：
;   ISCC.exe /DAppVersion=<版本号> installer\app.iss
;
; 安装形态：一键装到 C:\IDPhotoProcessor（ASCII 路径，避免非 Unicode 代码页下乱码），
; 桌面图标 + 开始菜单项 + 控制面板卸载项。安装包内是 PyInstaller 的 onedir 产物，
; 启动时不再像单文件那样把两百多 MB 解压到 %TEMP%，所以低配机上也能秒开。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "证件照批量处理工具"
#define AppExeName "idphoto-processor.exe"
; 固定 AppId：升级时 Inno 靠它识别出旧版本并原地覆盖，而不是又装一份
#define AppId "{{8F3A2C41-9E7B-4D52-B0A6-1C5E7D9F2A33}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName=C:\IDPhotoProcessor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 装到 C:\ 根目录需要管理员权限（会弹一次 UAC，属正常安装行为）
PrivilegesRequired=admin
OutputDir=out
OutputBaseFilename=IDPhotoProcessor-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 升级时若程序正在运行，自动关掉它，否则 exe 被占用会导致替换失败
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "cn"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\idphoto-processor\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[Code]
function GetUninstallString(): String;
var
  s: String;
begin
  Result := '';
  { 64 位系统上管理员安装写的是 64 位视图的注册表 }
  if not RegQueryStringValue(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppId}_is1',
                             'InstallLocation', s) then
    RegQueryStringValue(HKLM32, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppId}_is1',
                        'InstallLocation', s);
  Result := s;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '') or DirExists(ExpandConstant('{app}'));
end;

procedure CurPageChanged(CurPageID: Integer);
var
  idx: Integer;
begin
  { 首次安装：默认勾选创建桌面图标（TasksList 在 InitializeWizard 时尚未创建，
    必须等到选择任务这一页才访问）。升级时不主动勾选，避免把客户删掉的桌面图标又建回来。 }
  if (CurPageID = wpSelectTasks) and (not IsUpgrade()) then begin
    idx := WizardForm.TasksList.Items.IndexOf(ExpandConstant('{cm:CreateDesktopIcon}'));
    if (idx >= 0) and (not WizardForm.TasksList.Checked[idx]) then
      WizardForm.TasksList.Checked[idx] := True;
  end;
end;


