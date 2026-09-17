#define MyAppName "Ozon RFBS 上品工具"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "heibaiyuanfen"
#define MyAppExeName "Ozon_RFBS上品工具.exe"

[Setup]
AppId={{D3496D1A-1A7E-4FA7-8F15-D0E0842C3F2D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\OzonRFBSListingTool
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=安装包
OutputBaseFilename=Ozon_RFBS上品工具_完整数据安装版
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
SetupLogging=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "发布\Ozon_RFBS上品工具\*"; DestDir: "{app}"; Excludes: "程序数据\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "RFBS上品工具\config.json"; DestDir: "{app}\程序数据"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "RFBS上品工具\auto_jobs.json"; DestDir: "{app}\程序数据"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "RFBS上品工具\workspace_state.json"; DestDir: "{app}\程序数据"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "RFBS上品工具\*.ozon-api.json"; DestDir: "{app}\程序数据"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "RFBS上品工具\*.xlsx"; DestDir: "{app}\程序数据"; Excludes: "~$*"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "RFBS上品工具\*.db"; DestDir: "{app}\程序数据"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "RFBS上品工具\cache\*"; DestDir: "{app}\程序数据\cache"; Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs
Source: "RFBS上品工具\输出\*"; DestDir: "{app}\程序数据\输出"; Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
