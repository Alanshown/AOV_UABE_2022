#define Product "AOV_UABE_2022"
#define Version "2.4"
[Setup]
AppId=AOV_UABE_2022
AppName={#Product}
AppVersion={#Version}
AppVerName={#Product} {#Version}
AppPublisher=ShownAlan
VersionInfoVersion=2.4.0.0
VersionInfoTextVersion=2.4
VersionInfoProductVersion=2.4.0.0
VersionInfoProductTextVersion=2.4
DefaultDirName={localappdata}\Programs\{#Product}
DefaultGroupName={#Product}
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\AOV_UABE_2022\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\AOV_UABE_2022"; Filename: "{app}\AOV_UABE_2022.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\AOV_UABE_2022.exe"; Description: "Launch AOV_UABE_2022 2.4"; Flags: nowait postinstall skipifsilent
