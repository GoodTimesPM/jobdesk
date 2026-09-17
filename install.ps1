<#
.SYNOPSIS
    Set up JobDesk on this machine.

.DESCRIPTION
    Everything between a fresh download and a working app: Python itself if
    the machine has none, a private environment, the dependencies, the folders
    the app writes into, and a Desktop shortcut.

    Nothing here touches your system Python. The dependencies go into a
    `.venv` folder inside this checkout, so uninstalling is deleting a folder
    and a version pinned here can never collide with a version some other
    project needs.

    Safe to run twice. Every step checks for its own result first and says so
    instead of redoing it, which makes this a repair as well as an install.

    It does not ask you anything about yourself. Your name, your target
    titles, your salary floor and the rest are the app's setup tab, which
    opens on its own the first time and writes a profile you can keep editing
    afterwards.

.PARAMETER NoShortcut
    Skip the Desktop shortcut. Everything else still happens.

.PARAMETER NoLaunch
    Do not open JobDesk when the install finishes.

.PARAMETER Uninstall
    Remove the environment and the shortcut. Your profile, your data and your
    packets are deliberately left where they are; the script prints the paths
    so you can delete them yourself if that is what you meant. Asks first,
    unless -Force.

.PARAMETER NoPythonInstall
    Do not offer to install Python. If there is no suitable one already on the
    machine, stop and print the instructions for installing it by hand.

.PARAMETER Force
    Skip the confirmation prompts: the one on -Uninstall, and the one before
    installing Python.

.EXAMPLE
    .\Install.cmd

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoLaunch
#>

[CmdletBinding()]
param(
    [switch]$NoShortcut,
    [switch]$NoLaunch,
    [switch]$Uninstall,
    [switch]$NoPythonInstall,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Root    = $PSScriptRoot
$Venv    = Join-Path $Root ".venv"
$VenvPy  = Join-Path $Venv "Scripts\python.exe"
$VenvPyw = Join-Path $Venv "Scripts\pythonw.exe"
$MinPython = [Version]"3.11"

# What gets installed when the machine has nothing. 3.12 rather than the
# newest: PyMuPDF and pywebview ship prebuilt wheels for it, and the first
# weeks of a new Python release are spent watching packages compile from
# source or refuse to.
$PyVersion = "3.12.10"
$PyWinget  = "Python.Python.3.12"

# `py -m jobdesk.app` finds the package on the working directory, not on the
# path to this script. Without this the installer would build whichever
# checkout the shell happened to be sitting in.
Push-Location $Root

function Done([int]$Code) {
    Pop-Location
    exit $Code
}

# --- output ------------------------------------------------------------------
# A first-run installer gets read, not skimmed. Every step says what it is
# about to do before it does it, so a failure lands under a heading that names
# the thing that failed.

$script:StepNumber = 0

function Step([string]$Text) {
    $script:StepNumber++
    Write-Host ""
    Write-Host "[$script:StepNumber] $Text" -ForegroundColor Cyan
}

function Ok([string]$Text)   { Write-Host "    $Text" -ForegroundColor Green }
function Note([string]$Text) { Write-Host "    $Text" -ForegroundColor DarkGray }
function Warn([string]$Text) { Write-Host "    $Text" -ForegroundColor Yellow }

function Fail([string]$Text, [string]$Fix) {
    Write-Host ""
    Write-Host "  $Text" -ForegroundColor Red
    if ($Fix) { Write-Host ""; Write-Host $Fix -ForegroundColor Yellow }
    Write-Host ""
    Done 1
}

Write-Host ""
Write-Host "JobDesk" -ForegroundColor Cyan
Note $Root

# --- 0. are we actually extracted? -------------------------------------------
# Double-clicking a .cmd inside a .zip does not fail. Explorer quietly copies
# it, alone, to a scratch folder and runs it there, so the installer starts up
# in a directory that has none of the files it is about to install. The error
# that follows is about a missing requirements.txt, which sends people looking
# for the wrong problem. Say the real one instead.

if ($Root -match '\.zip\\' -or $Root -match '\\Temp\d+_') {
    Fail "This is still inside the .zip file." @"
Windows ran this out of a temporary copy, so nothing here is really on your
disk yet and nothing would survive the install.

Close this window. Right-click the .zip, choose "Extract All", pick a folder
you will keep it in, and run Install.cmd from there.
"@
}

if (-not (Test-Path (Join-Path $Root "requirements.txt"))) {
    Fail "This does not look like the JobDesk folder." @"
Install.cmd has to sit next to requirements.txt and the jobdesk folder. If you
copied it out on its own, put it back beside them and run it there.
"@
}

# --- uninstall ---------------------------------------------------------------

if ($Uninstall) {
    Step "Removing JobDesk from this machine"

    Write-Host ""
    Write-Host "    This deletes:" -ForegroundColor DarkGray
    Write-Host "      $Venv"
    Write-Host "      the JobDesk shortcut on your Desktop"
    Write-Host ""
    Write-Host "    This does NOT delete, and you may want to:" -ForegroundColor DarkGray
    foreach ($keep in @("profile", "data", "packets", "out", "logs")) {
        $path = Join-Path $Root $keep
        if (Test-Path $path) { Write-Host "      $path" }
    }
    Write-Host ""

    if (-not $Force) {
        $answer = Read-Host "    Go ahead? [y/N]"
        if ($answer -notmatch '^[Yy]') {
            Warn "Nothing was removed."
            Done 0
        }
    }

    if (Test-Path $Venv) {
        Remove-Item -Recurse -Force $Venv
        Ok "removed the environment"
    } else {
        Note "no environment to remove"
    }

    # The same OneDrive redirect `app/shortcut.py` handles: the real Desktop
    # is usually not the one under the home directory.
    $desktops = @()
    foreach ($var in @($env:OneDrive, $env:OneDriveConsumer)) {
        if ($var) { $desktops += (Join-Path $var "Desktop") }
    }
    $desktops += [Environment]::GetFolderPath("Desktop")
    $removed = $false
    foreach ($dir in $desktops) {
        $link = Join-Path $dir "JobDesk.lnk"
        if (Test-Path $link) { Remove-Item -Force $link; $removed = $true }
    }
    if ($removed) { Ok "removed the shortcut" } else { Note "no shortcut to remove" }

    Write-Host ""
    Write-Host "Uninstalled." -ForegroundColor Green
    Write-Host ""
    Done 0
}

# --- 1. python ---------------------------------------------------------------
# The single most common way a first run fails, and the one worth the most
# words: "python is not recognized" tells a person nothing about what to do.
# Better than telling them well is not making them do it, so this installs
# Python when the machine has none.

$InstallHelp = @"
Install Python from https://www.python.org/downloads/ and tick
"Add python.exe to PATH" on the first screen of the installer. Or, if you
have winget:

    winget install --id $PyWinget -e

Close this window, open a new one, and run Install.cmd again. A new window
matters: a program that was not on the PATH when this one started is not on
the PATH for this one either.
"@

function Get-PythonVersion([string]$Exe) {
    # The version that exe reports, or nothing if it is not a real Python.
    if (-not $Exe) { return $null }
    # try/catch around the call itself, not only around the parse: this now
    # runs against anything called python.exe anywhere on the machine, and a
    # broken one is a thing to walk past rather than an install failure.
    try {
        $reported = & $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $reported) { return $null }
    try { return [Version]($reported | Select-Object -First 1).Trim() }
    catch { return $null }
}

function Find-Python {
    # The best Python on this machine at or above $MinPython, or nothing.
    #
    # The PATH is checked first and then the two directories the official
    # installer actually writes to. That second sweep is not belt and braces:
    # a Python installed a minute ago by this very script is not on this
    # process's PATH and cannot be, because a process inherits its environment
    # at birth. Without the sweep, installing Python would be followed by
    # failing to find it.
    $best = $null
    $bestVersion = $null

    $candidates = @()
    foreach ($name in @("py", "python3", "python")) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found) { $candidates += $found.Source }
    }
    foreach ($base in @((Join-Path $env:LOCALAPPDATA "Programs\Python"),
                        $env:ProgramFiles, ${env:ProgramFiles(x86)}, "C:\")) {
        if (-not $base -or -not (Test-Path $base)) { continue }
        $dirs = Get-ChildItem -Path $base -Directory -Filter "Python3*" `
                              -ErrorAction SilentlyContinue
        foreach ($dir in $dirs) {
            $candidates += (Join-Path $dir.FullName "python.exe")
        }
    }

    foreach ($exe in $candidates) {
        if (-not (Test-Path $exe)) { continue }
        # The Microsoft Store ships a stub `python.exe` that exists, runs, and
        # does nothing but open the Store. It reports no version, so asking
        # for one is also the test for it.
        $version = Get-PythonVersion $exe
        if (-not $version -or $version -lt $MinPython) { continue }
        if (-not $bestVersion -or $version -gt $bestVersion) {
            $best = $exe
            $bestVersion = $version
        }
    }

    if (-not $best) { return $null }
    return [pscustomobject]@{ Path = $best; Version = $bestVersion }
}

function Update-PathFromRegistry {
    # Pick up a PATH that changed after this process started.
    #
    # An installer edits the stored PATH and broadcasts that it changed.
    # Running processes do not listen, so the only way to see a Python
    # installed thirty seconds ago is to go and read the setting.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ";"
}

function Install-Python {
    # Put Python on this machine. Did it work?
    #
    # winget first, because it is already on any current Windows and it knows
    # how to upgrade what it installs later. python.org's own installer is the
    # fallback, run unattended for the current user only so that nothing here
    # needs an administrator.
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Note "using winget"
        # Out-Host, not a bare call: a native command's output is pipeline
        # output, and pipeline output inside a PowerShell function is part of
        # what the function returns. Left alone, winget's chatter would come
        # back from Install-Python as a truthy array.
        & winget install --id $PyWinget -e --source winget --scope user `
            --accept-package-agreements --accept-source-agreements | Out-Host
        Update-PathFromRegistry
        if (Find-Python) { return $true }

        # Asking for user scope can fail outright when the package only
        # publishes a machine-scope installer. Worth one more try without the
        # preference, which may raise an administrator prompt -- a visible
        # thing a person can answer, rather than a silent dead end.
        Note "retrying without a scope preference"
        & winget install --id $PyWinget -e --source winget `
            --accept-package-agreements --accept-source-agreements | Out-Host
        Update-PathFromRegistry
        if (Find-Python) { return $true }
        Warn "winget did not leave a usable Python behind. Trying python.org."
    } else {
        Note "no winget on this machine, going to python.org"
    }

    $arch = switch ($env:PROCESSOR_ARCHITECTURE) {
        "AMD64" { "amd64" }
        "ARM64" { "arm64" }
        default { "" }
    }
    $suffix = if ($arch) { "-$arch" } else { "" }
    $url = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion$suffix.exe"
    $exe = Join-Path $env:TEMP "python-$PyVersion$suffix.exe"

    Note $url
    # A progress bar redrawn over a 25 MB download costs more time than the
    # download does. This is not a cosmetic preference.
    $previous = $ProgressPreference
    try {
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
    } catch {
        Warn "The download failed: $($_.Exception.Message)"
        return $false
    } finally {
        $ProgressPreference = $previous
    }

    Note "running the installer, this takes a minute"
    # /passive shows a progress window and asks nothing. InstallAllUsers=0
    # keeps it in this account, which is what lets it run without an
    # administrator prompt. PrependPath is for every window after this one.
    $proc = Start-Process -FilePath $exe -Wait -PassThru -ArgumentList @(
        "/passive", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1",
        "Include_test=0", "Include_doc=0", "AssociateFiles=0", "Shortcuts=0")
    Remove-Item $exe -Force -ErrorAction SilentlyContinue

    # 3010 is "installed, wants a reboot". Python does not need one to run.
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        Warn "The Python installer exited with $($proc.ExitCode)."
        return $false
    }
    Update-PathFromRegistry
    return [bool](Find-Python)
}

Step "Looking for Python $MinPython or newer"

$python = Find-Python
if ($python) {
    Ok "Python $($python.Version)"
    Note $python.Path
} else {
    Note "none found"

    if ($NoPythonInstall) {
        Fail "No Python $MinPython or newer on this machine." $InstallHelp
    }

    Write-Host ""
    Write-Host "    JobDesk runs on Python and this machine does not have it." `
        -ForegroundColor Yellow
    Write-Host "    It installs for your account only, needs no administrator," `
        -ForegroundColor DarkGray
    Write-Host "    and leaves anything else on the machine alone." `
        -ForegroundColor DarkGray

    $go = $Force
    if (-not $go) {
        Write-Host ""
        Write-Host "    Install Python $PyVersion now? [Y/n] " -NoNewline `
            -ForegroundColor Cyan
        try { $answer = Read-Host } catch { $answer = "" }
        # Anything but a clear no. Somebody who double-clicked an installer
        # and pressed Enter meant yes.
        $go = -not ($answer -and $answer.Trim() -match "^n")
    }
    if (-not $go) {
        Fail "Stopped: JobDesk cannot run without Python." $InstallHelp
    }

    Step "Installing Python $PyVersion"
    if (-not (Install-Python)) {
        Fail "Python could not be installed automatically." $InstallHelp
    }

    $python = Find-Python
    if (-not $python) {
        Fail "Python installed but cannot be found." $InstallHelp
    }
    Ok "Python $($python.Version)"
    Note $python.Path
}

$SystemPy = $python.Path

# --- 2. the environment ------------------------------------------------------

Step "Building a private environment in .venv"

if (Test-Path $VenvPy) {
    Ok "already there"
} else {
    & $SystemPy -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) {
        Fail "The environment could not be created." @"
The output above says why. The usual cause is that this folder is on a drive
or a share the account cannot write to. Try moving the JobDesk folder
somewhere under your own user directory and running Install.cmd again.
"@
    }
    Ok "created"
}
Note $VenvPy

# --- 3. the dependencies -----------------------------------------------------

Step "Installing the dependencies"
Note "requests, fpdf2, python-docx, PyMuPDF, pymysql, truststore, pywebview"

& $VenvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $VenvPy -m pip install -r (Join-Path $Root "requirements.txt") --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Fail "Something would not install." @"
Run it again without the quiet flag to see the whole error:

    "$VenvPy" -m pip install -r requirements.txt

If it is PyMuPDF that fails, your Python is probably a version it has no
prebuilt package for yet. Python 3.12 is the safe choice today.
"@
}

# Proof, rather than a clean exit code. pip can report success and still leave
# an import broken, and the first time anyone finds out is the app failing to
# open with a traceback nobody reads.
$check = & $VenvPy -c "import requests, fpdf, docx, fitz; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "The dependencies installed but will not import." ($check | Out-String)
}
Ok "installed, and they import"

# --- 4. the folders ----------------------------------------------------------

Step "Making the folders JobDesk writes into"

$made = 0
foreach ($dir in @("data\radar", "data\engine\jds", "data\apply", "logs",
                   "out", "packets")) {
    $path = Join-Path $Root $dir
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
        $made++
    }
}
if ($made -gt 0) { Ok "made $made" } else { Ok "all there already" }

# --- 5. the shortcut ---------------------------------------------------------

Step "Writing the Desktop shortcut"

if ($NoShortcut) {
    Note "skipped, you asked for -NoShortcut"
} else {
    # The app writes its own shortcut, so there is one implementation of the
    # COM call and one answer about where the Desktop actually is. It targets
    # the interpreter that runs it, which is why this uses the venv's.
    $out = & $VenvPy -m jobdesk.app --shortcut 2>&1
    if ($LASTEXITCODE -eq 0) {
        Ok ($out | Select-Object -Last 1)
    } else {
        # Not fatal. A missing icon on the Desktop is a worse outcome than a
        # failed install only if it stops you using the app, and it does not:
        # JobDesk.cmd opens the same program.
        Warn "could not be written, which is not fatal:"
        Note ($out | Out-String).Trim()
        Note "JobDesk.cmd in this folder opens the app either way."
    }
}

# --- 6. done -----------------------------------------------------------------

Write-Host ""
Write-Host "Installed." -ForegroundColor Green
Write-Host ""
Write-Host "    JobDesk opens on its Setup tab the first time and asks for" -ForegroundColor DarkGray
Write-Host "    your resume, your target titles and where you will work." -ForegroundColor DarkGray
Write-Host "    Nothing runs against a job board until that is filled in." -ForegroundColor DarkGray
Write-Host ""

if ($NoLaunch) {
    Note "Not opening it, you asked for -NoLaunch."
    Write-Host ""
    Done 0
}

# Start-Process rather than a plain call, so this window can close while the
# app stays open. pythonw, so the app does not carry a console around.
$launcher = if (Test-Path $VenvPyw) { $VenvPyw } else { $VenvPy }
Start-Process -FilePath $launcher -ArgumentList "-m", "jobdesk.app" -WorkingDirectory $Root
Ok "opening JobDesk"
Write-Host ""

Done 0
