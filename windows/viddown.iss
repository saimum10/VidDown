; Inno Setup script — VidDown (SAIMUM HABIB) Windows installer.
; Built by build_windows.ps1, which passes:
;   /DAppVersion=<x.y.z.w>  /DVariant=<with-js|no-js>
;   /DSourceDir=<PyInstaller output folder>  /DOutputDir=<where to put Setup.exe>
; The output file name carries the variant — the in-app updater relies on it:
;   VidDown_Setup_<version>_x64_<variant>.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Variant
  #define Variant "with-js"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\pyinstaller_out\VidDown"
#endif
#ifndef OutputDir
  #define OutputDir "..\"
#endif

[Setup]
AppId={{CA4926DC-6A1A-4E3C-B2CB-E8FF891FFBCD}
AppName=VidDown
AppVersion={#AppVersion}
AppVerName=VidDown {#AppVersion}
AppPublisher=SAIMUM HABIB
AppPublisherURL=https://github.com/saimum10/VidDown
AppSupportURL=https://github.com/saimum10/VidDown/issues
DefaultDirName={autopf}\VidDown
DefaultGroupName=VidDown
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=VidDown_Setup_{#AppVersion}_x64_{#Variant}
SetupIconFile=viddown.ico
UninstallDisplayIcon={app}\VidDown.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; Per-user install by default (no admin prompt); the person can still choose "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\VidDown"; Filename: "{app}\VidDown.exe"
Name: "{autodesktop}\VidDown"; Filename: "{app}\VidDown.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VidDown.exe"; Description: "{cm:LaunchProgram,VidDown}"; Flags: nowait postinstall skipifsilent
; Silent run = started by the in-app updater, which quit VidDown first: start the new version again.
Filename: "{app}\VidDown.exe"; Flags: nowait runasoriginaluser; Check: WizardSilent
