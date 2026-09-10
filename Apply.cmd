@echo off
REM Assisted Apply used to open a numbered menu here. It is a JobDesk tab now,
REM so this launches the app and gets out of the way.
REM
REM Kept because a shortcut, a pinned taskbar icon or a habit may still point
REM at this file, and a launcher that silently stops working is worse than one
REM that forwards.
REM
REM The CLI underneath is still there and still supported:
REM     py -m jobdesk.apply.main --help

cd /d "%~dp0"
call "%~dp0JobDesk.cmd"
