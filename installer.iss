; Windows installer for AIS Sales Support (built automatically by GitHub Actions with Inno Setup)
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{8F3C2A51-6B7E-4D2A-9C1E-0A5F7B3D2E41}
AppName=AIS Sales Support
AppVersion={#MyAppVersion}
AppVerName=AIS Sales Support {#MyAppVersion}
AppPublisher=Apartment Interior Supply
DefaultDirName={localappdata}\Programs\AIS Sales Support
DefaultGroupName=AIS Sales Support
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=AIS-Sales-Support-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\OrderFormApp.exe
UninstallDisplayName=AIS Sales Support
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on my desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\OrderFormApp.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "demo\*"; DestDir: "{app}\Demo files"; Excludes: "*.py"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; shortcuts from versions named "Order Form App"
Type: files; Name: "{autoprograms}\Order Form App.lnk"
Type: files; Name: "{autoprograms}\Order Form App demo files.lnk"
Type: files; Name: "{autodesktop}\Order Form App.lnk"

[Icons]
Name: "{autoprograms}\AIS Sales Support"; Filename: "{app}\OrderFormApp.exe"
Name: "{autoprograms}\AIS Sales Support demo files"; Filename: "{app}\Demo files"
Name: "{autodesktop}\AIS Sales Support"; Filename: "{app}\OrderFormApp.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\OrderFormApp.exe"; Description: "Open AIS Sales Support now"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=AIS Sales Support is installed. Open it any time from the Start menu or the desktop shortcut.%n%nYour customers and orders are stored separately, so installing a newer version keeps all your data.

[Code]
procedure StopRunningApp();
var
  R: Integer;
begin
  { the app keeps a small background server running; close it so files can be replaced }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM OrderFormApp.exe', '', SW_HIDE, ewWaitUntilTerminated, R);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningApp();
  Result := True;
end;
