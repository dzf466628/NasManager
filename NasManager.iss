; NasManager 安装包脚本 (Inno Setup 6)
#define MyAppName "NasManager"
#define MyAppVersion "1.1.77"
#define MyAppExeName "NasManager.exe"
#define MyAppId "{{8E4F7C2A-9B3D-4E5F-8A1C-6D2B3E4F5A6B}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=NAS Manager
AppPublisherURL=https://haitaobang.synology.me
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=.
OutputBaseFilename=NasManager_Setup_{#MyAppVersion}
SetupIconFile=app_multi.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "dist\NasManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall
