r"""JobDesk, already running when you pick up your phone.

Reaching JobDesk from a phone works the moment the server can bind a network
address, and then does not work in practice, for a boring reason: it only runs
while a window is open on the desktop. The phone is the device you use
*because* you are not at the desk, so "first go to the desk and start it" is
the whole feature cancelling itself out.

So this registers a scheduled task, beside the three that run the radar, that
starts the server at logon and leaves it up. Same shape as the radar's task --
pythonw so there is no console, hidden so it does not flicker -- and it differs
in three ways, each of which is a bug if you get it wrong:

  * **No execution time limit.** The default is three days, after which Task
    Scheduler kills the task. A server that stops on the third Tuesday and
    comes back at the next logon is worse than one that never started, because
    you will not notice until you are away from the machine.
  * **`--host auto`, not a literal address.** The task is written once and runs
    for months; the address is a fact about the network at boot. See `net.py`.
  * **Restart on failure**, three times, a minute apart. The one failure this
    actually covers is losing the race with the network at logon.

The task holds no secret. It names the project directory and a flag; the access
token stays in `.env`, read at startup by the process the task launches.
"""

from __future__ import annotations

import getpass
import subprocess

from .. import noconsole, paths
from . import access, net, shortcut

TASK_NAME = "JobDesk Server"

# Long enough for Tailscale or wifi to have come up, short enough that JobDesk
# is there before you are. `--host auto` resolves after this delay, which is
# the entire reason the delay exists.
START_DELAY = "PT45S"


def _ps(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(script: str) -> str:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=90, **noconsole.flags())
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"powershell could not be run: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "").strip() or "powershell failed")
    return result.stdout or ""


def preflight(host: str = "auto", port: int = 8765) -> tuple[str, str]:
    """Refuse now, on screen, rather than at 7am in a log nobody reads.

    Resolves what `--host auto` will resolve to and asks `access.check` the
    same question the server will ask. Raises `net.NoAddress` or
    `access.Unconfigured`, both of which carry the fix in the message.
    """
    address, kind = net.resolve(host)
    access.check(address)
    return address, kind


def install(*, host: str = "auto", port: int = 8765) -> str:
    """Create or replace the logon task. Returns what the scheduler reports."""
    preflight(host, port)
    args = f"-m jobdesk.app --serve --host {host} --port {port}"
    user = getpass.getuser()
    script = f"""
$ErrorActionPreference = 'Stop'
$act = New-ScheduledTaskAction -Execute {_ps(shortcut.pythonw())} -Argument {_ps(args)} -WorkingDirectory {_ps(paths.ROOT)}
$trg = New-ScheduledTaskTrigger -AtLogOn -User {_ps(user)}
$trg.Delay = '{START_DELAY}'
$set = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries `
       -DontStopIfGoingOnBatteries -StartWhenAvailable `
       -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
       -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$prn = New-ScheduledTaskPrincipal -UserId {_ps(user)} -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName {_ps(TASK_NAME)} -Action $act -Trigger $trg `
    -Settings $set -Principal $prn -Force `
    -Description 'JobDesk, up from logon so the phone has something to reach. No window, no time limit.' | Out-Null
(Get-ScheduledTask -TaskName {_ps(TASK_NAME)}).Actions[0].Execute
"""
    return _run(script).strip()


def start_now() -> None:
    """Run the task immediately, so installing it is also starting it."""
    _run(f"Start-ScheduledTask -TaskName {_ps(TASK_NAME)}")


def remove() -> None:
    _run(f"Unregister-ScheduledTask -TaskName {_ps(TASK_NAME)} "
         f"-Confirm:$false -ErrorAction SilentlyContinue")


def describe() -> dict:
    """What the scheduler currently holds, or `{}` when the task is absent."""
    script = f"""
$t = Get-ScheduledTask -TaskName {_ps(TASK_NAME)} -ErrorAction SilentlyContinue
if (-not $t) {{ 'missing'; exit 0 }}
$i = Get-ScheduledTaskInfo -TaskName {_ps(TASK_NAME)}
$t.Actions[0].Execute
$t.Actions[0].Arguments
[string]$t.State
[string]$t.Settings.ExecutionTimeLimit
[string]$i.LastRunTime
[string]$i.LastTaskResult
"""
    try:
        lines = [ln.strip() for ln in _run(script).splitlines() if ln.strip()]
    except RuntimeError:
        return {}
    if not lines or lines[0] == "missing":
        return {}
    keys = ["execute", "arguments", "state", "time_limit", "last_run",
            "last_result"]
    return dict(zip(keys, lines + [""] * len(keys)))


def unlimited(task: dict) -> bool:
    """True when the task will not be killed after three days.

    Task Scheduler spells 'no limit' as an empty limit or `PT0S`, and spells
    the dangerous default as `P3D`.
    """
    return task.get("time_limit", "") in ("", "PT0S")
