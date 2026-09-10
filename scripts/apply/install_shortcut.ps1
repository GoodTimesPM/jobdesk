# Put "Job Search.lnk" on the Desktop, pointing at Apply.cmd.
#
# Keep this file pure ASCII - Windows PowerShell 5.1 reads a BOM-less script
# as ANSI, so a UTF-8 dash decodes into bytes that break string parsing. Same
# lesson Job Radar's scripts learned.

param(
    [string]$Name = "Job Search",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$target = Join-Path $root "Apply.cmd"
$desktop = [Environment]::GetFolderPath("Desktop")
$link = Join-Path $desktop "$Name.lnk"

if ($Remove) {
    if (Test-Path $link) {
        Remove-Item $link -Force
        Write-Host "Removed $link"
    } else {
        Write-Host "Nothing to remove at $link"
    }
    return
}

if (-not (Test-Path $target)) {
    throw "Apply.cmd not found at $target"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $root
$shortcut.Description = "Assisted Apply - job candidates, tailored packets, application log"
# apply.ico is the radar scope, drawn by scripts/make_icon.ps1. Fall back to a
# shell32 icon if it is missing - anything beats the generic .cmd icon, which
# is what makes the shortcut findable on a busy desktop.
$icon = Join-Path $root "apply.ico"
if (Test-Path $icon) {
    $shortcut.IconLocation = "$icon,0"
} else {
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,43"
}
$shortcut.Save()

Write-Host "Shortcut created: $link"
Write-Host "  -> $target"
Write-Host ""
Write-Host "Double-click it to open the menu."
