; FIN Desktop v1: per-user one-click installer, self-contained Python and OS-user data.
#define MyAppName "TRIAID FIN Desktop"
#define MyAppVersion GetEnv("TRIAID_DESKTOP_VERSION")
#define MyAppPublisher "TRIAID Research Team"
#define MyAppExe "TRIAID-FIN-Desktop.exe"

[Setup]
AppId={{EC86694D-8513-4CB7-B02D-125C2D81F93E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\TRIAID FIN Desktop
DefaultGroupName=TRIAID FIN Desktop
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=TRIAID-FIN-Desktop-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#MyAppExe}
CloseApplications=yes
RestartApplications=no
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
ShowLanguageDialog=no

[Tasks]
Name: "autostart"; Description: "开机登录后自动运行 TRIAID 研究后台"; GroupDescription: "后台设置"; Flags: checkedonce

[Files]
Source: "dist\TRIAID-FIN-Desktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{userdesktop}\TRIAID FIN Desktop"; Filename: "{app}\{#MyAppExe}"; WorkingDir: "{app}"
Name: "{userprograms}\TRIAID FIN Desktop"; Filename: "{app}\{#MyAppExe}"; WorkingDir: "{app}"
Name: "{userstartup}\TRIAID FIN Background"; Filename: "{app}\{#MyAppExe}"; Parameters: "--supervisor"; Tasks: autostart; WorkingDir: "{app}"

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装微软桌面浏览引擎..."; Flags: waituntilterminated; Check: not HasWebView2
Filename: "{app}\{#MyAppExe}"; Description: "启动 TRIAID FIN Desktop"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[Code]
function RuntimeVersionOk(VersionText: String): Boolean;
begin
  Result := (VersionText <> '') and (VersionText <> '0.0.0.0');
end;

function HasWebView2: Boolean;
var
  VersionText: String;
  ClientKey: String;
begin
  ClientKey := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result :=
    (RegQueryStringValue(HKCU, ClientKey, 'pv', VersionText) and RuntimeVersionOk(VersionText))
    or
    (RegQueryStringValue(HKLM32, ClientKey, 'pv', VersionText) and RuntimeVersionOk(VersionText));
end;
