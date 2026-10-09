@echo off
rem Shared launcher used by the other shortcuts: runs the exhibit CLI with the given args.
setlocal
chcp 65001 >nul
set "ROOT=%~dp0.."
if not exist "%ROOT%\node_modules" (
  echo node_modules missing - run "npm install" in %ROOT% first.
  pause
  exit /b 1
)
set "NODE=node"
where node >nul 2>nul || set "NODE=C:\dev\tools\node\node.exe"
"%NODE%" "%ROOT%\bin\exhibit.js" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo exhibit exited with code %RC% - see %ROOT%\logs
  pause
)
exit /b %RC%
