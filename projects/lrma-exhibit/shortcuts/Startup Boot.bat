@echo off
rem Run at Windows logon (a shortcut to this file lives in the user's Startup folder).
rem Waits for the desktop, audio devices and network to settle, then runs the boot macro.
set "DELAY=30"
title Exhibit startup
echo Exhibit boot starts in %DELAY% seconds (close this window to cancel)...
timeout /t %DELAY% /nobreak >nul
call "%~dp0_exhibit.cmd" run boot
