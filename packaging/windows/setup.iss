#define Product "AOV_UABE_2022"
#define Version "2.4"
[Setup]
AppId={code:SelectAppId}
AppName={#Product}
AppVersion={#Version}
AppVerName={#Product} {#Version}
AppPublisher=ShownAlan
VersionInfoVersion=2.4.0.0
VersionInfoTextVersion=2.4
VersionInfoProductVersion=2.4.0.0
VersionInfoProductTextVersion=2.4
DefaultDirName={localappdata}\Programs\{#Product}
DefaultGroupName={code:SelectDefaultGroup}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\artifacts
OutputBaseFilename={#Product}-{#Version}-Setup-x64
SetupIconFile=..\..\icon.ico
UninstallDisplayIcon={app}\AOV_UABE_2022.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UsePreviousAppDir=HasPreviousInstall
UsePreviousGroup=HasPreviousInstall
UsePreviousTasks=HasPreviousInstall
; Inno cannot resolve a scripted AppId early enough for language selection.
UsePreviousLanguage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\AOV_UABE_2022\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
; Same task name as the 2.3.6 installer, so its user's selection is restored.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Icons]
Name: "{autoprograms}\AOV_UABE_2022"; Filename: "{app}\AOV_UABE_2022.exe"; WorkingDir: "{app}"; Check: not IsLegacyUpgrade
Name: "{group}\AOV UABE 2022"; Filename: "{app}\AOV_UABE_2022.exe"; WorkingDir: "{app}"; Check: IsLegacyUpgrade
Name: "{autodesktop}\AOV UABE 2022"; Filename: "{app}\AOV_UABE_2022.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AOV_UABE_2022.exe"; Description: "Launch AOV_UABE_2022 2.4"; Flags: nowait postinstall skipifsilent

[Code]
const
  CurrentAppId = 'AOV_UABE_2022';
  LegacyAppId = '{349E126A-338F-480D-B360-B76A97BB88CB}';
  UninstallPrefix = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';

var
  ChosenAppId: String;

function HasRegisteredApp(const AppId: String): Boolean;
var
  InstallPath: String;
begin
  { Both released installers are x64-mode and explicitly per-user. Do not
    try to upgrade another account's or an unrelated application's files. }
  Result :=
    RegQueryStringValue(HKCU64, UninstallPrefix + AppId + '_is1',
      'Inno Setup: App Path', InstallPath) and
    (InstallPath <> '') and
    FileExists(AddBackslash(InstallPath) + 'AOV_UABE_2022.exe');
end;

function SelectAppId(Param: String): String;
begin
  if ChosenAppId = '' then begin
    { Inno restores the path, program group and task choices and appends to
      the matching uninstall log when the original ID is reused. }
    if HasRegisteredApp(CurrentAppId) then
      ChosenAppId := CurrentAppId
    else if HasRegisteredApp(LegacyAppId) then
      ChosenAppId := LegacyAppId
    else
      ChosenAppId := CurrentAppId;
  end;
  Result := ChosenAppId;
end;

function HasPreviousInstall: Boolean;
begin
  Result := HasRegisteredApp(SelectAppId(''));
end;

function IsLegacyUpgrade: Boolean;
begin
  Result := SelectAppId('') = LegacyAppId;
end;

function SelectDefaultGroup(Param: String): String;
begin
  if IsLegacyUpgrade then
    Result := 'AOV UABE 2022'
  else
    Result := CurrentAppId;
end;
