param(
    [Parameter(Mandatory=$true)][string]$Iscc,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$testRoot = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
& $Iscc '/Qp' "/O$testRoot" (Join-Path $PSScriptRoot 'installer-consent.iss')
if ($LASTEXITCODE -ne 0) { throw 'Consent fixture compilation failed.' }
$fixture = Join-Path $testRoot 'consent-fixture.exe'
$packet = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '..\third_party\microsoft-consent\packet.json') | ConvertFrom-Json
$revision = $packet.revision
$cases = @(
    @{Name='missing'; Extra=@(); Allowed=$false},
    @{Name='wrong'; Extra=@('/ACCEPTMICROSOFTTERMS=1'); Allowed=$false},
    @{Name='old-revision'; Extra=@('/ACCEPTMICROSOFTTERMS=2020-01-01'); Allowed=$false},
    @{Name='duplicate'; Extra=@("/ACCEPTMICROSOFTTERMS=$revision", "/ACCEPTMICROSOFTTERMS=$revision"); Allowed=$false},
    @{Name='conflicting'; Extra=@("/ACCEPTMICROSOFTTERMS=$revision", '/ACCEPTMICROSOFTTERMS=no'); Allowed=$false},
    @{Name='valid-english'; Extra=@("/ACCEPTMICROSOFTTERMS=$revision", '/LANG=english'); Allowed=$true; Language='en'},
    @{Name='valid-german'; Extra=@("/acceptmicrosoftterms=$revision", '/LANG=german'); Allowed=$true; Language='de'}
)
$results = foreach ($case in $cases) {
    $caseRoot = Join-Path $testRoot $case.Name
    $log = Join-Path $testRoot ($case.Name + '.log')
    $arguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',
                   ('/DIR="' + $caseRoot + '"'), ('/LOG="' + $log + '"')) + $case.Extra
    $proc = Start-Process -FilePath $fixture -ArgumentList $arguments -WindowStyle Hidden -PassThru
    if (-not $proc.WaitForExit(30000)) { throw "Consent test timed out: $($case.Name)" }
    $proc.Refresh()
    $installed = Test-Path -LiteralPath (Join-Path $caseRoot 'installed-marker.txt')
    $receipt = Join-Path $caseRoot 'microsoft-terms-acceptance.txt'
    if ($case.Allowed) {
        if ($proc.ExitCode -ne 0 -or -not $installed -or -not (Test-Path -LiteralPath $receipt)) {
            throw "Authorized install failed: $($case.Name), exit=$($proc.ExitCode); $log"
        }
        $content = Get-Content -Raw -LiteralPath $receipt
        $hash = $packet.rtf_sha256.($case.Language)
        if (-not $content.Contains("terms_sha256=$hash") -or -not $content.Contains('method=explicit-command-line')) {
            throw "Receipt does not identify the accepted terms: $($case.Name)"
        }
    } elseif ($proc.ExitCode -eq 0 -or $installed -or (Test-Path -LiteralPath $receipt)) {
        throw "Unauthorized installation reached file writes: $($case.Name)"
    }
    [pscustomobject]@{Case=$case.Name;ExitCode=$proc.ExitCode;Installed=$installed;Passed=$true}
}
$results | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $testRoot 'results.json') -Encoding utf8
$results | Format-Table -AutoSize
