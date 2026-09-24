; Inno Setup script for DAS Meeting Assistant.
; Compile after PyInstaller has produced dist\MeetingTranscriber\:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
; Output: packaging\Output\DAS-Meeting-Assistant-Setup-<version>.exe (DAS)
;
; Download Inno Setup: https://jrsoftware.org/isdl.php

#ifndef AppProfile
  #error Build through packaging/build.ps1 with a validated profile
#endif
#ifndef BuildMode
  #error BuildMode is required
#endif
#define LegacyAppName "Voice Transcriber " + AppProfile
#if AppProfile == "Managed"
  #define AppName "DAS Meeting Assistant"
  #define InstallerStem "DAS-Meeting-Assistant"
#else
  #define AppName "DAS Meeting Assistant Community"
  #define InstallerStem "DAS-Meeting-Assistant-Community"
#endif
#define AppExe "MeetingTranscriber" + AppProfile + ".exe"
; build.ps1 passes /DAppVersion=<config.py VERSION>; this is only the fallback
; when ISCC is run directly. config.py is the single source of truth.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
; build.ps1 derives this filename-safe form from AppVersion by replacing dots.
#ifndef InstallerVersion
  #define InstallerVersion "0-1-0"
#endif

#include "licenses\microsoft-terms.iss"

[Setup]
; Keep upgrade identity, install path and executable stable across the rename.
AppId=DAS.VoiceTranscriber.{#AppProfile}
AppName={#AppName}
AppPublisher=Data Assessment Solutions
AppVersion={#AppVersion}
DefaultDirName={autopf}\MeetingTranscriber{#AppProfile}
DefaultGroupName={#AppName}
UsePreviousTasks=yes
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=Output
OutputBaseFilename={#InstallerStem}-Setup-{#InstallerVersion}
Compression=lzma2
SolidCompression=yes
; Per-user install needs no admin; use lowest so testers don't hit UAC.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Stop an existing tray process before replacing files or launching the new build.
CloseApplications=yes
CloseApplicationsFilter={#AppExe}
RestartApplications=no
; Use the Windows UI language without an extra language-selection step.
ShowLanguageDialog=no
LicenseFile=..\third_party\microsoft-consent\microsoft-components.en.rtf

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "..\third_party\microsoft-consent\microsoft-components.en.rtf"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"; LicenseFile: "..\third_party\microsoft-consent\microsoft-components.de.rtf"

[Messages]
english.WizardLicense=License terms for Microsoft components
english.LicenseLabel=Microsoft Speech SDK and Visual C++ Runtime
english.LicenseLabel3=Read the complete terms below. Your open-source rights for the other components remain unchanged.
english.LicenseAccepted=I &accept the Microsoft component terms
english.LicenseNotAccepted=I &do not accept the terms
german.WizardLicense=Lizenzbedingungen für Microsoft-Komponenten
german.LicenseLabel=Microsoft Speech SDK und Visual C++ Runtime
german.LicenseLabel3=Lesen Sie die vollständigen Bedingungen. Ihre Open-Source-Rechte für die übrigen Komponenten bleiben bestehen.
german.LicenseAccepted=Ich &stimme den Microsoft-Komponentenbedingungen zu
german.LicenseNotAccepted=Ich stimme &nicht zu

[CustomMessages]
english.MicrosoftConsentRequired=Installation requires agreement to the Microsoft component terms. Review the agreement and pass /ACCEPTMICROSOFTTERMS={#MicrosoftTermsRevision} for an authorized silent deployment.
german.MicrosoftConsentRequired=Die Installation erfordert die Zustimmung zu den Microsoft-Komponentenbedingungen. Prüfen Sie die Vereinbarung und übergeben Sie für eine autorisierte stille Installation /ACCEPTMICROSOFTTERMS={#MicrosoftTermsRevision}.
english.DesktopShortcut=Create a desktop shortcut
german.DesktopShortcut=Desktop-Verknüpfung erstellen
english.Shortcuts=Additional shortcuts:
german.Shortcuts=Zusätzliche Verknüpfungen:
english.StartupShortcut=Start automatically when I sign in
german.StartupShortcut=Bei der Windows-Anmeldung automatisch starten
english.StartupGroup=Startup:
german.StartupGroup=Autostart:
english.LaunchApp=Launch %1
german.LaunchApp=%1 starten

[Files]
; The entire PyInstaller one-folder output (built into ..\dist\MeetingTranscriber).
Source: "..\dist\{#BuildMode}\MeetingTranscriber{#AppProfile}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[InstallDelete]
; Only obsolete program components inside this product's installation directory.
; Without these entries an upgrade would leave the old FFmpeg/native DLLs behind.
; User settings, recordings and LGPL replacement sources are not cleanup targets.
Type: filesandordirs; Name: "{app}\_internal\av"
Type: filesandordirs; Name: "{app}\_internal\av.libs"
Type: filesandordirs; Name: "{app}\_internal\google_crc32c"
Type: filesandordirs; Name: "{app}\_internal\pylibsrtp"
Type: filesandordirs; Name: "{app}\_internal\setuptools"
Type: filesandordirs; Name: "{app}\_internal\webview\lib"
Type: files; Name: "{app}\_internal\api-ms-win-*.dll"
Type: files; Name: "{app}\_internal\dbghelp.dll"
Type: files; Name: "{app}\_internal\dbgcore.dll"
Type: files; Name: "{app}\_internal\ucrtbase.dll"
Type: files; Name: "{app}\_internal\charset.dll"
Type: files; Name: "{app}\_internal\ffi-8.dll"
Type: files; Name: "{app}\_internal\glib-2.0-0.dll"
Type: files; Name: "{app}\_internal\gobject-2.0-0.dll"
Type: files; Name: "{app}\_internal\iconv.dll"
Type: files; Name: "{app}\_internal\intl-8.dll"
Type: files; Name: "{app}\_internal\pcre2-8.dll"
Type: files; Name: "{app}\_internal\azure\cognitiveservices\speech\Microsoft.CognitiveServices.Speech.extension.*.dll"
Type: files; Name: "{app}\_internal\PIL\_avif*.pyd"
Type: files; Name: "{app}\_internal\PIL\_webp*.pyd"
Type: files; Name: "{app}\_internal\PIL\_imagingcms*.pyd"
Type: files; Name: "{app}\_internal\PIL\_imagingft*.pyd"
Type: files; Name: "{app}\_internal\PIL\_imagingtk*.pyd"
; Remove only our old links, including a previously customized Start Menu group.
Type: files; Name: "{group}\{#LegacyAppName}.lnk"
Type: files; Name: "{userprograms}\{#LegacyAppName}\{#LegacyAppName}.lnk"
Type: dirifempty; Name: "{userprograms}\{#LegacyAppName}"
Type: files; Name: "{autodesktop}\{#LegacyAppName}.lnk"
Type: files; Name: "{userstartup}\{#LegacyAppName}.lnk"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
; Optional: start automatically at login.
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startupicon

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:Shortcuts}"
Name: "startupicon"; Description: "{cm:StartupShortcut}"; GroupDescription: "{cm:StartupGroup}"

[UninstallDelete]
Type: files; Name: "{app}\microsoft-terms-acceptance.txt"

[Code]
#include "microsoft-consent.iss"

procedure InitializeWizard();
begin
  { Inno restores the old group on upgrade. Rename its default only, preserving
    a folder that the user explicitly customized. }
  if CompareText(WizardForm.GroupEdit.Text, '{#LegacyAppName}') = 0 then
    WizardForm.GroupEdit.Text := '{#AppName}';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { CloseApplications asks politely; taskkill is the final guard for a tray
    process that has no visible window or did not respond to shutdown. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExe}', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
  Result := '';
end;

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp,{#AppName}}"; Flags: nowait postinstall skipifsilent
