# Register the Assisted Apply scheduled task.
# Re-runnable: an existing task with the same name is replaced.
#
# Keep this file pure ASCII (see the note in run_auto.ps1).
#
#   .\install_schedule.ps1                          # 07:20 / 12:50 / 17:20
#   .\install_schedule.ps1 -Times "07:20"           # once a day
#   .\install_schedule.ps1 -MinScore 90 -Max 5
#
# The times are 20 minutes behind the radar's 07:00 / 12:30 / 17:00 sweeps on
# purpose: `auto` reads data/radar/candidates.json, which is only written
# at the end of a run. A full sweep is ~7,700 postings and takes a few minutes,
# so 20 is comfortable margin rather than a race.

param(
    [string[]]$Times = @("07:20", "12:50", "17:20"),
    [int]$MinScore = 80,
    [int]$Max = 10,
    [string]$TaskName = "JobRadarApply"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runner = Join-Path $PSScriptRoot "run_auto.ps1"

if (-not (Test-Path $runner)) { throw "Missing runner: $runner" }
if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) {
    throw "Could not find py.exe on PATH."
}

Write-Host "Task name : $TaskName"
Write-Host "Runner    : $runner"
Write-Host "Working   : $root"
Write-Host "Times     : $($Times -join ', ')"
Write-Host "Floor     : score >= $MinScore, at most $Max packet(s) per run"

# -WindowStyle Hidden so no console flashes up three times a day. The runner
# uses py.exe rather than pythonw.exe deliberately - see the note in it.
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -MinScore $MinScore -Max $Max"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $root

$triggers = @()
foreach ($t in $Times) {
    $triggers += New-ScheduledTaskTrigger -Daily -At $t
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Description "Assisted Apply: build draft application packets for postings Job Radar scored at or above the floor. Never submits anything." -Force | Out-Null

Write-Host ""
Write-Host "Registered. Run it once now with:"
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
Write-Host "Pause it any time by putting OFF in SWITCH.txt (no need to touch the task)."
Write-Host "Log: $root\logs\apply\auto.log"
