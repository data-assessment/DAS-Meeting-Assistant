# Build one distribution from the shared client source.
param(
    [string]$Profile = "packaging\profiles\community.json",
    [string]$PythonExe = ".venv\Scripts\python.exe",
    [string]$SigningConfig = "",
    [switch]$ValidateOnly,
    [switch]$AppOnly,
    [switch]$RequireSignature
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
# Validate before npm, staging or packaging. Legacy -BundleEnv is intentionally
# unsupported; no .env file can be passed to a release build.
$mode = & $PythonExe "packaging\profile_tool.py" --profile $Profile
if ($LASTEXITCODE -ne 0) { throw "Deployment profile validation failed." }
$mode = "$mode".Trim()
if ($mode -notin @("community", "managed")) { throw "Unexpected build mode." }
if ($ValidateOnly) { Write-Host "Valid public profile: $mode"; exit 0 }
$profileLabel = (Get-Culture).TextInfo.ToTitleCase($mode)
$appName = "MeetingTranscriber$profileLabel"
$appDirectory = Join-Path "dist\$mode" $appName
$verLine = Select-String -Path "config.py" -Pattern '^VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $verLine) { throw "Could not read VERSION from config.py." }
$version = $verLine.Matches[0].Groups[1].Value
$filenameVersion = $version -replace '\.', '-'
$installerStem = if ($mode -eq "managed") { "DAS-Meeting-Assistant" } else { "DAS-Meeting-Assistant-Community" }
$installerPath = "packaging\Output\$installerStem-Setup-$filenameVersion.exe"

function Import-SigningConfig($path) {
    if (-not $path -or -not (Test-Path -LiteralPath $path)) { return }
    $allowed = @(
        "TRUSTED_SIGNING_ENDPOINT",
        "TRUSTED_SIGNING_ACCOUNT",
        "TRUSTED_SIGNING_PROFILE",
        "SIGN_THUMBPRINT",
        "SIGN_TIMESTAMP_URL"
    )
    foreach ($rawLine in Get-Content -LiteralPath $path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
            throw "Invalid signing configuration; value omitted"
        }
        $name = $Matches[1]
        if ($name -notin $allowed) {
            throw "Unsupported signing setting '$name' in $path"
        }
        $value = $Matches[2].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
    Write-Host "Using signing settings: $((Resolve-Path -LiteralPath $path).Path)"
}


function Invoke-SigningWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [int]$MaxAttempts = 6
    )
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            & $Action
            return
        } catch {
            # A signing provider can report a late error after writing the signature.
            # Treat an already-valid signature as success rather than adding another.
            $signature = Get-AuthenticodeSignature -FilePath $File -ErrorAction SilentlyContinue
            if ($signature.Status -eq "Valid") {
                Write-Host "Signature verified despite a signing-provider error: $File"
                return
            }
            if ($attempt -ge $MaxAttempts) { throw }
            $delay = [Math]::Min($attempt * 2, 10)
            Write-Warning (
                "Signing attempt $attempt of $MaxAttempts failed for $File. " +
                "The file may still be held by Defender or another scanner; retrying in $delay seconds."
            )
            Start-Sleep -Seconds $delay
        }
    }
}

function Invoke-Sign($file) {
    if ($env:TRUSTED_SIGNING_ENDPOINT -and $env:TRUSTED_SIGNING_ACCOUNT -and $env:TRUSTED_SIGNING_PROFILE) {
        if (-not (Get-Module -ListAvailable -Name TrustedSigning)) {
            throw "TrustedSigning module not found. Install-Module -Name TrustedSigning -Repository PSGallery"
        }
        Import-Module TrustedSigning -ErrorAction Stop
        $full = (Resolve-Path -LiteralPath $file).ProviderPath
        Write-Host "Signing (Azure Trusted Signing): $full"
        Invoke-SigningWithRetry -File $full -Action {
            Invoke-TrustedSigning `
                -Endpoint $env:TRUSTED_SIGNING_ENDPOINT `
                -CodeSigningAccountName $env:TRUSTED_SIGNING_ACCOUNT `
                -CertificateProfileName $env:TRUSTED_SIGNING_PROFILE `
                -Files $full `
                -FileDigest SHA256 `
                -TimestampRfc3161 "http://timestamp.acs.microsoft.com" `
                -TimestampDigest SHA256
        }
        return
    }
    if ($env:SIGN_THUMBPRINT) {
        $signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
        if (-not $signtool) { Write-Warning "signtool.exe not found (install Windows SDK); skipped signing"; return }
        $ts = if ($env:SIGN_TIMESTAMP_URL) { $env:SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
        $full = (Resolve-Path -LiteralPath $file).ProviderPath
        Invoke-SigningWithRetry -File $full -Action {
            & $signtool sign /sha1 $env:SIGN_THUMBPRINT /fd SHA256 /tr $ts /td SHA256 $full
            if ($LASTEXITCODE -ne 0) { throw "signing failed for $full" }
        }
        return
    }
    # No signing backend configured -> unsigned build.
}


function Resolve-Iscc {
    # 1) on PATH; 2) common install dirs; 3) registry InstallLocation.
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe"
    )
    foreach ($p in $candidates) { if ($p -and (Test-Path $p)) { return $p } }
    foreach ($hive in 'HKLM:', 'HKCU:') {
        $key = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
        try {
            $loc = (Get-ItemProperty $key -ErrorAction Stop).InstallLocation
            if ($loc -and (Test-Path (Join-Path $loc 'ISCC.exe'))) { return (Join-Path $loc 'ISCC.exe') }
        } catch {}
    }
    return $null
}



Import-SigningConfig $SigningConfig
$hasAzureSigning = $env:TRUSTED_SIGNING_ENDPOINT -and $env:TRUSTED_SIGNING_ACCOUNT -and $env:TRUSTED_SIGNING_PROFILE
if ($RequireSignature -and -not ($hasAzureSigning -or $env:SIGN_THUMBPRINT)) {
    throw "A release signature was required but no signing backend is configured."
}
$iscc = Resolve-Iscc
if (-not $AppOnly -and -not $iscc) {
    throw "Inno Setup (ISCC.exe) is required for an installer. Use -AppOnly for an unpacked test build."
}

Write-Host "Building frontend from the committed lockfile"
Push-Location frontend
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
} finally { Pop-Location }

& $PythonExe "packaging\prepare_microsoft_terms.py"
if ($LASTEXITCODE -ne 0) { throw "Microsoft recipient agreement is missing or stale." }
& $PythonExe "packaging\third_party.py"
if ($LASTEXITCODE -ne 0) { throw "Dependency documents are missing or stale." }
$stagedProfile = Join-Path $PSScriptRoot "deployment-profile.json"
try {
    & $PythonExe "packaging\profile_tool.py" --profile $Profile --stage $stagedProfile
    if ($LASTEXITCODE -ne 0) { throw "Profile staging failed." }
    # Restrict Windows DLL discovery to this Python and the operating system.
    $originalBuildPath = $env:PATH
    $pythonBase = (& $PythonExe -c "import sys; print(sys.base_prefix)").Trim()
    $env:PATH = @((Split-Path $PythonExe), $pythonBase, (Join-Path $pythonBase "DLLs"),
                  "$env:SystemRoot\System32", "$env:SystemRoot") -join ";"
    try {
        & $PythonExe -m PyInstaller --clean --noconfirm --distpath "dist\$mode" --workpath "build\$mode" "packaging\MeetingTranscriber.spec"
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    } finally { $env:PATH = $originalBuildPath }
} finally {
    if (Test-Path -LiteralPath $stagedProfile) { Remove-Item -LiteralPath $stagedProfile }
}
$executable = Join-Path $appDirectory "$appName.exe"
& $PythonExe "packaging\third_party.py" --bundle $appDirectory --analysis "build\$mode\MeetingTranscriber\Analysis-00.toc"
if ($LASTEXITCODE -ne 0) { throw "Dependency artifact audit failed." }
Invoke-Sign $executable
if ($RequireSignature -and (Get-AuthenticodeSignature -FilePath $executable).Status -ne "Valid") {
    throw "Application signature verification failed."
}
& $PythonExe "packaging\profile_tool.py" --profile $Profile --audit $appDirectory
if ($LASTEXITCODE -ne 0) { throw "Application artifact audit failed." }
if (-not $AppOnly) {
    & $iscc "/DAppVersion=$version" "/DInstallerVersion=$filenameVersion" "/DAppProfile=$profileLabel" "/DBuildMode=$mode" "packaging\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
    Invoke-Sign $installerPath
    if ($RequireSignature -and (Get-AuthenticodeSignature -FilePath $installerPath).Status -ne "Valid") {
        throw "Installer signature verification failed."
    }
    Get-FileHash -LiteralPath $installerPath -Algorithm SHA256
    Write-Host "Built installer: $installerPath"
} else {
    Write-Host "Built unpacked test application: $appDirectory"
}
