r"""The rule that decides whether the phone's request ever reaches the server.

This module exists because of a failure with no error message anywhere. The
server binds a network address, says so in its log, answers on that address
from the machine itself -- and the phone sits on a blank tab spinning until it
gives up. Nothing is logged, because nothing arrived: Windows Firewall dropped
the packet. A dropped packet is not a refused connection. A refusal comes back
in a few milliseconds and the browser says so; a drop looks exactly like a
server that is thinking about it, forever.

What makes it easy to get wrong is that Python has two executables. Starting
JobDesk from `JobDesk.cmd` runs `python.exe`, and the first time it binds a
network address Windows shows the "allow this app" box and writes a rule for
`python.exe`. Everything works. The desktop shortcut and the logon task both
run `pythonw.exe` -- the windowless twin, a different file, therefore a
different rule, and one that will never be created by a prompt, because a
hidden background task has no window to prompt in front of. So the feature
works when you test it from a terminal and fails on the machine you actually
walk away from, which is the worst shape a bug can have.

Adding a rule needs administrator rights, and a web request is not going to
have them: a page that could elevate itself would be a much worse thing than
an unreachable phone. So this module does what it can unelevated -- it reads --
and hands back the exact command for the rest.

The rule is deliberately narrow: one port, TCP, inbound, private profiles
only. Not "allow pythonw.exe", which would open every port any Python script
on this machine ever binds, on any network it is on.
"""

from __future__ import annotations

import subprocess

from .. import noconsole
from . import shortcut

RULE_NAME = "JobDesk"


def _ps(value: str) -> str:
    """A PowerShell single-quoted literal. Doubling is the whole escape rule."""
    return "'" + str(value).replace("'", "''") + "'"


def _run(script: str, *, quiet: bool = True) -> str:
    """Run PowerShell and hand back stdout.

    Best-effort by default. Parts of the firewall API refuse to answer an
    unelevated caller, and a JobDesk that cannot report on a rule must still
    draw a page.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, **noconsole.flags())
    except (OSError, subprocess.SubprocessError) as exc:
        if quiet:
            return ""
        raise RuntimeError(str(exc)) from exc
    if result.returncode != 0 and not quiet:
        raise RuntimeError((result.stderr or "").strip() or "powershell failed")
    return result.stdout or ""


def rule_command(port: int) -> str:
    """The command that opens the port, to be run in an admin PowerShell."""
    return (
        f"New-NetFirewallRule -DisplayName '{RULE_NAME}' -Direction Inbound "
        f"-Action Allow -Protocol TCP -LocalPort {port} -Profile Private,Domain"
    )


def state(port: int) -> str:
    """One of `open`, `blocked`, or `unknown`.

    `unknown` is its own answer and not a synonym for `blocked`. The port
    filters are one of the parts an unelevated caller cannot read, so on a
    locked-down machine this cannot tell an open port from a closed one -- and
    telling someone their firewall is the problem when it is not sends them
    off to fight the wrong thing with an admin prompt open.
    """
    script = f"""
$ErrorActionPreference = 'Stop'
$r = Get-NetFirewallRule -DisplayName {_ps(RULE_NAME)} -ErrorAction SilentlyContinue |
     Where-Object {{ $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' -and $_.Enabled -eq 'True' }}
if (-not $r) {{ 'blocked'; exit 0 }}
foreach ($rule in $r) {{
  $p = ($rule | Get-NetFirewallPortFilter).LocalPort
  if ($p -contains '{port}' -or $p -eq 'Any') {{ 'open'; exit 0 }}
}}
'blocked'
"""
    answer = _run(script).strip().splitlines()
    if not answer:
        return "unknown"
    last = answer[-1].strip()
    return last if last in ("open", "blocked") else "unknown"


def allow(port: int) -> None:
    """Add the rule, elevating once. Raises with the manual command on refusal.

    `Start-Process -Verb RunAs` is the UAC prompt. `-Wait` matters: without it
    the outer PowerShell returns success the moment the prompt is *shown*, and
    the caller would report the port open while the box is still on screen.
    """
    inner = rule_command(port).replace("'", "''")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$p = Start-Process powershell -Verb RunAs -Wait -PassThru "
        f"-ArgumentList '-NoProfile','-Command','{inner}'\n"
        "if ($p.ExitCode -ne 0) { throw 'the elevated command failed' }"
    )
    try:
        _run(script, quiet=False)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"could not add the firewall rule ({exc}).\n\n"
            "Open PowerShell as administrator and run:\n\n"
            f"    {rule_command(port)}\n"
        ) from exc


def program() -> str:
    """The executable the phone's packets are actually addressed to.

    Only used in messages. It is the single most confusing fact in this file --
    the rule Windows already wrote is for the other one.
    """
    return str(shortcut.pythonw())
