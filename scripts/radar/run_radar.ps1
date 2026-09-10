# Run one Job Radar cycle now (manual / testing).
# Keep this file pure ASCII - Windows PowerShell 5.1 reads a BOM-less script
# as ANSI, so a UTF-8 em-dash decodes to a byte sequence containing a quote
# character and breaks string parsing.

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

Write-Host "Job Radar - running one cycle from $root"

if ($DryRun) {
    py -m jobdesk.radar.main --dry-run
} else {
    py -m jobdesk.radar.main
}

Write-Host ""
Write-Host "Log:      $root\logs\radar\run.log"
Write-Host "Digests:  $root\data\radar\digests"
