#define AppName "EnigmaLex"
#ifndef AppVersion
  #define AppVersion "2026.11.28.1"
#endif

[Setup]
AppId={{B7B17F9B-82F3-4E8C-9B98-9A2E65C31A60}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\EnigmaLex
DefaultGroupName=EnigmaLex
PrivilegesRequired=lowest
OutputDir=..\instalador-dist
OutputBaseFilename=EnigmaLex-Setup-{#AppVersion}
SetupIconFile=..\assets\anonimizador.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\EnigmaLex\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\EnigmaLex"; Filename: "{app}\EnigmaLex.exe"; IconFilename: "{app}\_internal\assets\anonimizador.ico"
Name: "{autodesktop}\EnigmaLex"; Filename: "{app}\EnigmaLex.exe"; IconFilename: "{app}\_internal\assets\anonimizador.ico"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: checkedonce

[Run]
Filename: "{app}\EnigmaLex.exe"; Description: "Abrir EnigmaLex"; Flags: nowait postinstall skipifsilent
