# Register the Job Radar scheduled task.
# Re-runnable: an existing task with the same name is replaced.
#
# Keep this file pure ASCII (see the note in run_radar.ps1).
#
#   .\install_schedule.ps1                      # default 7:00 AM and 1:00 PM
#   .\install_schedule.ps1 -Times "07:00"       # once a day
#   .\install_schedule.ps1 -Times "07:00","13:00","17:30"
#
# Twice a day is the default on purpose: postings reviewed in the first 24-72
# hours do materially better, so a second pass catches the morning's new reqs
# while they are still fresh.

param(
    [string[]]$Times = @("07:00", "13:00"),
    [string]$TaskName = "JobRadar"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# pythonw.exe so no console window flashes up on each run.
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $pyw = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
}
if (-not $pyw) {
    throw "Could not find pythonw.exe or py.exe on PATH."
}

Write-Host "Task name : $TaskName"
Write-Host "Runner    : $pyw"
Write-Host "Working   : $root"
Write-Host "Times     : $($Times -join ', ')"

$action = New-ScheduledTaskAction -Execute $pyw -Argument "-m jobdesk.radar.main" -WorkingDirectory $root

$triggers = @()
foreach ($t in $Times) {
    $triggers += New-ScheduledTaskTrigger -Daily -At $t
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Description "Job Radar: daily job discovery, scoring, and Notion tracking." -Force | Out-Null

Write-Host ""
Write-Host "Registered. Run it once now with:"
Write-Host "    Start-ScheduledTask -TaskName $TaskName"
Write-Host "Pause it any time by putting OFF in SWITCH.txt (no need to touch the task)."
