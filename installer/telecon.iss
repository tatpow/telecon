; Windows installer for an already validated standalone distribution.
#ifndef PayloadDir
  #error PayloadDir must point to the checked application folder
#endif
#ifndef ReleaseVersion
  #error ReleaseVersion is required
#endif
#ifndef NumericVersion
  #error NumericVersion is required
#endif
#ifndef InstallerDir
  #error InstallerDir is required
#endif

[Setup]
AppId={{A7F7E82A-1D7B-4F55-8A14-A63F066F746E}
AppName=Телекон
AppVersion={#ReleaseVersion}
AppPublisher=tatpow
AppPublisherURL=https://github.com/tatpow/telecon
AppSupportURL=https://github.com/tatpow/telecon/issues
AppUpdatesURL=https://github.com/tatpow/telecon/releases
DefaultDirName={localappdata}\Programs\Telecon
DefaultGroupName=Телекон
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
UsePreviousAppDir=yes
UsePreviousTasks=yes
LicenseFile={#PayloadDir}\LICENSE
SetupIconFile={#PayloadDir}\ui\tray.ico
UninstallDisplayIcon={app}\ui\tray.ico
VersionInfoVersion={#NumericVersion}
VersionInfoProductName=Telecon
OutputDir={#InstallerDir}
OutputBaseFilename=Telecon-Windows-x64-{#ReleaseVersion}-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
SetupMutex=TeleconSetupMutex
; Never forcibly interrupt an active lecture/recording, including silent mode.
CloseApplications=no
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startmenuicon"; Description: "Создать ярлык в меню «Пуск»"; GroupDescription: "Ярлыки:"
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Телекон"; Filename: "{app}\Telecon.exe"; WorkingDir: "{app}"; IconFilename: "{app}\ui\tray.ico"; Tasks: startmenuicon
Name: "{autodesktop}\Телекон"; Filename: "{app}\Telecon.exe"; WorkingDir: "{app}"; IconFilename: "{app}\ui\tray.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\Telecon.exe"; WorkingDir: "{app}"; Description: "Запустить Телекон"; Flags: nowait postinstall skipifsilent
