# Build packets + sync the OG tracker for every posting Notion shows as
# newly "Date Applied", unattended. This is what the JobRadarApply scheduled
# task runs; also fine to run by hand.
#
# Keep this file pure ASCII - Windows PowerShell 5.1 reads a BOM-less script
# as ANSI, so a UTF-8 em-dash decodes to a byte sequence containing a quote
# character and breaks string parsing. (Learned in scripts/radar/run_radar.ps1.)
#
#   .\run_auto.ps1                 # at most 10 packets
#   .\run_auto.ps1 -DryRun         # list what it would sync/build
#   .\run_auto.ps1 -Max 5

param(
    [int]$Max = 10,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$logDir = Join-Path $root "logs\apply"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory $logDir | Out-Null }
$logFile = Join-Path $logDir "auto.log"

# Keep the log from growing without bound - last ~2000 lines is several weeks.
if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 1MB)) {
    $keep = Get-Content $logFile -Tail 2000
    Set-Content $logFile $keep -Encoding utf8
}

# Not $args - that is an automatic variable and assigning to it is asking for
# a confusing evening.
$pyArgs = @("-m", "jobdesk.apply.main", "auto", "--max", $Max)
if ($DryRun) { $pyArgs += "--dry-run" }

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $logFile "" -Encoding utf8
Add-Content $logFile "==== $stamp  run_auto (max $Max) ====" -Encoding utf8

# py.exe rather than pythonw.exe: pythonw leaves sys.stdout as None, so every
# echo() in the run vanishes and a failed build leaves no trace. The scheduled
# task hides the window instead.
$output = & py @pyArgs
$code = $LASTEXITCODE

$output | Add-Content $logFile -Encoding utf8
Add-Content $logFile "---- exit $code ----" -Encoding utf8

# Echo to the console too, so running this by hand still shows something.
$output | Write-Host
Write-Host ""
Write-Host "Log:      $logFile"
Write-Host "Packets:  $(Join-Path $root 'packets')"

exit $code
