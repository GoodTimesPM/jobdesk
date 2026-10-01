# Register the Assisted Apply scheduled task.
# Re-runnable: an existing task with the same name is replaced.
#
# Keep this file pure ASCII (see the note in run_auto.ps1).
#
#   .\install_schedule.ps1                          # 07:20 / 12:50 / 17:20
#   .\install_schedule.ps1 -Times "07:20"           # once a day
#   .\install_schedule.ps1 -Max 5
#
# The times are 20 minutes behind the radar's 07:00 / 12:30 / 17:00 sweeps on
# purpose: `auto` reads data/radar/candidates.json, which is only written
# at the end of a run. A full sweep is ~7,700 postings and takes a few minutes,
# so 20 is comfortable margin rather than a race.

param(
    [string[]]$Times = @("07:20", "12:50", "17:20"),
    [int]$Max = 10,
    [string]$TaskName = "JobRadarApply"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# pythonw.exe, never powershell.exe. PowerShell draws its console before it
# reads -WindowStyle Hidden, so the old task flashed a window three times a
# day. jobdesk.apply.auto_task writes the output to the log itself.
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) {
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
}
if (-not $pyw) { throw "Could not find pythonw.exe." }

Write-Host "Task name : $TaskName"
Write-Host "Runner    : $pyw"
Write-Host "Working   : $root"
Write-Host "Times     : $($Times -join ', ')"
Write-Host "Limit     : at most $Max packet(s) per run"

$action = New-ScheduledTaskAction -Execute $pyw -Argument "-m jobdesk.apply.auto_task --max $Max" -WorkingDirectory $root

$triggers = @()
foreach ($t in $Times) {
    $triggers += New-ScheduledTaskTrigger -Daily -At $t
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Description "Assisted Apply: build draft packets for postings Notion shows as newly applied. Never submits anything." -Force | Out-Null

Write-Host ""
Write-Host "Registered. Run it once now with:"
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
Write-Host "Pause it any time by putting OFF in SWITCH.txt (no need to touch the task)."
Write-Host "Log: $root\logs\apply\auto.log"
