; An isolated fixture: same production consent code, no app/registry/shortcuts.
#define AppVersion "consent-test"
#include "..\packaging\licenses\microsoft-terms.iss"
[Setup]
AppId=DAS.MicrosoftConsent.IsolatedTest
AppName=Microsoft consent verification
AppVersion=1
DefaultDirName={tmp}\das-consent-verification
UsePreviousAppDir=no
AppendDefaultDirName=no
Uninstallable=no
CreateUninstallRegKey=no
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
ShowLanguageDialog=no
OutputBaseFilename=consent-fixture
LicenseFile=..\third_party\microsoft-consent\microsoft-components.en.rtf
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"; LicenseFile: "..\third_party\microsoft-consent\microsoft-components.de.rtf"
[CustomMessages]
english.MicrosoftConsentRequired=Microsoft consent missing or invalid.
german.MicrosoftConsentRequired=Microsoft-Zustimmung fehlt oder ist ungültig.
[Files]
Source: "..\packaging\licenses\microsoft-components.en.txt"; DestDir: "{app}"; DestName: "installed-marker.txt"
[Code]
#include "..\packaging\microsoft-consent.iss"
