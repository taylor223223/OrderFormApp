; Windows installer for Order Form App (built automatically by GitHub Actions with Inno Setup)
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{8F3C2A51-6B7E-4D2A-9C1E-0A5F7B3D2E41}
AppName=Order Form App
AppVersion={#MyAppVersion}
AppVerName=Order Form App {#MyAppVersion}
AppPublisher=Apartment Interior Supply
DefaultDirName={localappdata}\Programs\Order Form App
DefaultGroupName=Order Form App
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=OrderFormApp-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\OrderFormApp.exe
UninstallDisplayName=Order Form App
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

[Icons]
Name: "{autoprograms}\Order Form App"; Filename: "{app}\OrderFormApp.exe"
Name: "{autoprograms}\Order Form App demo files"; Filename: "{app}\Demo files"
Name: "{autodesktop}\Order Form App"; Filename: "{app}\OrderFormApp.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\OrderFormApp.exe"; Description: "Open Order Form App now"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabel=Order Form App is installed. Open it any time from the Start menu or the desktop shortcut.%n%nYour customers and orders are stored separately, so installing a newer version keeps all your data.

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
