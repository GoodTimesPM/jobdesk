# Remove the Job Radar scheduled task.
# Keep this file pure ASCII (see the note in run_radar.ps1).
#
# To pause temporarily you do NOT need this - put OFF in SWITCH.txt instead.

param(
    [string]$TaskName = "JobRadar"
)

$ErrorActionPreference = "Stop"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
} else {
    Write-Host "No scheduled task named '$TaskName' found - nothing to do."
}
