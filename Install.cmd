@echo off
REM Set up JobDesk. Double-click this.
REM
REM All it does is run install.ps1, which is where the actual work and the
REM explanations are. It exists because double-clicking a .ps1 does not run
REM it -- Windows opens it in Notepad -- and telling someone to open a
REM terminal and type an -ExecutionPolicy flag is the point most people stop.
REM
REM -ExecutionPolicy Bypass applies to this one run of this one file. It
REM changes no setting on the machine.

title Installing JobDesk
cd /d "%~dp0"

REM UTF-8 console, so a path with an accent in it doesn't kill a message.
chcp 65001 >nul 2>&1

REM Everything that comes out of a downloaded .zip carries a mark saying it
REM came from the internet, and Windows blocks scripts that carry it. The
REM symptom is not an error anyone can act on: a window opens, something says
REM the file is not digitally signed, and nothing installs. Clearing the mark
REM on the way in is the whole difference between "double-click this" and a
REM page of instructions about right-click, Properties, Unblock.
echo Clearing the downloaded-file mark...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0.' -Recurse -Force -Include *.ps1,*.cmd,*.bat,*.py,*.pyw,*.ico,*.txt,*.md -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
    echo.
    echo The install did not finish. The message above says why.
    echo.
    pause
    exit /b %EXITCODE%
)

REM A successful install ends by opening the app, so this window has nothing
REM left to say. The pause is still here because a window that vanishes the
REM instant it finishes leaves you unsure whether it worked.
echo.
pause
