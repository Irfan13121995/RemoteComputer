; Inno Setup Script for RemoteDesk Pro v2.0
; Can be compiled with ISCC.exe or replaced by the built-in RemoteDeskProSetup.spec

[Setup]
AppId={{8F9E4A21-3B7D-4C12-9A84-5E6D7C8B9A01}
AppName=RemoteDesk Pro
AppVersion=2.0.0
AppPublisher=RemoteDesk Open Source Project
DefaultDirName={autopf}\RemoteDeskPro
DefaultGroupName=RemoteDesk Pro
OutputDir=..\dist
OutputBaseFilename=RemoteDeskPro-InnoSetup
SetupIconFile=..\assets\icon.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Start RemoteDesk Pro automatically when Windows starts"; GroupDescription: "Unattended Access:"

[Files]
Source: "..\dist\RemoteDeskPro.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\RemoteDesk Pro"; Filename: "{app}\RemoteDeskPro.exe"
Name: "{autodesktop}\RemoteDesk Pro"; Filename: "{app}\RemoteDeskPro.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "RemoteDeskPro"; ValueData: """{app}\RemoteDeskPro.exe"" --tray"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\RemoteDeskPro.exe"; Description: "{cm:LaunchProgram,RemoteDesk Pro}"; Flags: nowait postinstall skipifsilent
