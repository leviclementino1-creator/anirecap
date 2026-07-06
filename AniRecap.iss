; Instalador do AniRecap (Inno Setup 6).
;
; Compilar: ISCC.exe /DAppVersion=2.0.0 AniRecap.iss
;   (ou build-installer.bat, que lê a versão do config.py sozinho)
;
; Decisões:
; - Instala em {localappdata}\AniRecap SEM pedir admin (PrivilegesRequired=
;   lowest). Essencial: o auto-update precisa escrever na pasta do app —
;   em Program Files isso exigiria elevação a cada update.
; - config.json e music/ nunca são sobrescritos em updates
;   (onlyifdoesntexist) nem removidos na desinstalação (uninsneveruninstall)
;   — preserva as API keys e as trilhas do usuário.
; - Update silencioso: o updater.py roda o Setup com /VERYSILENT /RELAUNCH=1;
;   o instalador fecha o app (CloseApplications), troca os arquivos e reabre.

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

#define MyAppName "AniRecap"
#define MyAppExeName "AniRecap.exe"
#define MyAppPublisher "Levi"
#define MyAppURL "https://github.com/leviclementino1-creator/anirecap"

[Setup]
AppId={{8E1F4A9C-7B23-4D5E-9A61-AC20B3F0D144}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppVerName={#MyAppName} {#AppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={localappdata}\{#MyAppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename={#MyAppName}-Setup-{#AppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#AppVersion}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; App inteiro, MENOS config.json e music/ (tratados abaixo com regra própria)
Source: "dist\AniRecap\*"; DestDir: "{app}"; Excludes: "config.json,music\*"; Flags: recursesubdirs ignoreversion
; config.json sanitizado: só entra se não existe (update preserva as keys)
Source: "dist\AniRecap\config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
; music/: placeholder só na primeira instalação; nunca desinstala as trilhas
Source: "dist\AniRecap\music\*"; DestDir: "{app}\music"; Flags: onlyifdoesntexist recursesubdirs uninsneveruninstall

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Instalação manual: checkbox "Abrir o AniRecap" no fim do wizard
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
; Update silencioso (/VERYSILENT /RELAUNCH=1): reabre o app sozinho
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: ShouldRelaunch

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
