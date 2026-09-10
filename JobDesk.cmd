@echo off
REM JobDesk, with a console attached.
REM
REM The desktop shortcut runs pythonw.exe and shows no console at all, which
REM is right for an app and wrong for working out why one will not start. This
REM opens the same program in a browser tab with the console visible, and the
REM window stays open on a crash so the error is readable.
REM
REM Run Install.cmd first. Run `JobDesk.cmd --shortcut` once to put the icon
REM on the desktop.

title JobDesk
cd /d "%~dp0"

REM UTF-8 console, so a posting with a smart quote in it doesn't kill a print.
chcp 65001 >nul 2>&1

REM The environment Install.cmd builds, if it is there. Falling back to the
REM system `py` matters: this file predates the installer, it is what the
REM README told people to run for a year, and a developer working on the
REM package may well have the dependencies installed globally and no .venv at
REM all. Either way the app is the same app.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=py"

"%PY%" -m jobdesk.app --browser %*
set EXITCODE=%ERRORLEVEL%

REM Exit code 9009 is cmd for "there is no such program", which here means no
REM Python at all. Every other non-zero code came out of Python itself, and
REM the traceback above it says more than this file could.
if "%EXITCODE%"=="9009" (
    echo.
    echo Python is not installed, or not on the PATH.
    echo.
    echo Run Install.cmd in this folder. It checks for Python, tells you
    echo where to get it if it is missing, and sets up everything else.
    echo.
    pause
    exit /b 9009
)

if not "%EXITCODE%"=="0" (
    echo.
    echo Exited with code %EXITCODE%.
    echo.
    echo If that is a ModuleNotFoundError above, the dependencies are not
    echo installed. Run Install.cmd in this folder.
    echo.
    pause
)
