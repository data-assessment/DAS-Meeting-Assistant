# Isolated first-run test of the installed DAS client. No existing user data is changed.
param([switch]$Resume, [switch]$Status)
$ErrorActionPreference = 'Stop'
if ($Resume -and $Status) { throw 'Bitte nur -Resume oder -Status angeben.' }
$testBase = Join-Path $PSScriptRoot 'DAS-Anmeldetests'
$lastRunFile = Join-Path $testBase 'last-run.json'
$installedExe = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\MeetingTranscriberManaged\MeetingTranscriberManaged.exe'

if ($Resume -or $Status) {
    if (-not (Test-Path -LiteralPath $lastRunFile)) { throw 'Noch kein Anmeldetest gestartet. Skript zuerst ohne Parameter ausführen.' }
    $lastRun = Get-Content -Raw -LiteralPath $lastRunFile | ConvertFrom-Json
    $profileRoot = [IO.Path]::GetFullPath([string]$lastRun.profileRoot)
    $basePrefix = [IO.Path]::GetFullPath($testBase).TrimEnd('\') + '\'
    if (-not $profileRoot.StartsWith($basePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Ungültiger Testprofil-Pfad.'
    }
    if (-not (Test-Path -LiteralPath $profileRoot -PathType Container)) { throw 'Testprofil fehlt.' }
}

if ($Status) {
    $dataFolder = Join-Path $profileRoot 'MeetingTranscriber\Managed'
    $settingsFile = Join-Path $dataFolder 'settings.json'
    $complete = $false
    $signedIn = $false
    if (Test-Path -LiteralPath $settingsFile) {
        $settings = Get-Content -Raw -LiteralPath $settingsFile | ConvertFrom-Json
        $signedIn = $settings.meeting_notes_setup_complete -eq $true -and $settings.meeting_notes_access_mode -eq 'entra'
        $complete = $signedIn -and ($null -eq $settings.meeting_notes_onboarding_complete -or $settings.meeting_notes_onboarding_complete -eq $true)
    }
    [pscustomobject]@{
        AnmeldungErfolgreich = $signedIn
        EinrichtungErfolgreichGespeichert = $complete
        AnmeldecacheVorhanden = Test-Path -LiteralPath (Join-Path $dataFolder 'token_cache.bin')
        Testprofil = $profileRoot
        Protokoll = Join-Path $dataFolder 'transcriber.log'
    } | Format-List
    return
}

if (-not (Test-Path -LiteralPath $installedExe -PathType Leaf)) { throw 'Der DAS-Client ist für diesen Windows-Benutzer nicht installiert.' }
$manifestFile = Join-Path (Split-Path -Parent $installedExe) 'build-manifest.json'
if (-not (Test-Path -LiteralPath $manifestFile)) { throw 'Bitte zuerst DAS-Version 0.40.1 oder neuer installieren.' }
$manifest = Get-Content -Raw -LiteralPath $manifestFile | ConvertFrom-Json
if ('isolated-app-data-root' -notin $manifest.features) {
    throw 'Bitte zuerst DAS-Version 0.40.1 oder neuer installieren. Ältere Versionen unterstützen diesen sicheren Anmeldetest nicht.'
}
if (Get-Process -Name 'MeetingTranscriberManaged' -ErrorAction SilentlyContinue) {
    throw 'Zuerst DAS Meeting Assistant über das Symbol neben der Windows-Uhr mit Quit beenden. Das Fenster-X blendet ihn nur aus.'
}

if (-not $Resume) {
    $runName = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $profileRoot = Join-Path $testBase $runName
    $dataFolder = Join-Path $profileRoot 'MeetingTranscriber\Managed'
    New-Item -ItemType Directory -Path $dataFolder -Force | Out-Null
    # Prevent automatic recording while the user is only testing sign-in.
    [IO.File]::WriteAllText((Join-Path $dataFolder 'settings.json'), '{"auto_start":false}', [Text.UTF8Encoding]::new($false))
}

$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $installedExe
$startInfo.WorkingDirectory = Split-Path -Parent $installedExe
$startInfo.UseShellExecute = $false
# Keep Windows/browser profile discovery intact. Only Transcriber reads this variable.
$startInfo.EnvironmentVariables['VOICE_TRANSCRIBER_DATA_ROOT'] = $profileRoot
$clientProcess = [System.Diagnostics.Process]::Start($startInfo)
@{ profileRoot = $profileRoot; processId = $clientProcess.Id } | ConvertTo-Json |
    Set-Content -LiteralPath $lastRunFile -Encoding UTF8

Write-Output "DAS-Anmeldetest gestartet. Testprofil: $profileRoot"
Write-Output 'Bitte die Anmeldung jetzt selbst im Client anklicken. Automatischer Meetingstart ist im neuen Testprofil ausgeschaltet.'
Write-Output 'Nach dem Test über das Tray-Symbol mit Quit beenden. Normaler Start über das Startmenü verwendet wieder Ihre bisherigen Daten.'
Write-Output 'Mit -Status prüfen Sie das gespeicherte Ergebnis; mit -Resume starten Sie dasselbe Testprofil erneut.'
