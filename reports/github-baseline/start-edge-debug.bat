@echo off
chcp 65001 >nul
setlocal

tasklist /fi "imagename eq msedge.exe" | find /i "msedge.exe" >nul
if not errorlevel 1 (
  echo [!] Edge is still running. Please close ALL Edge windows, then press any key.
  pause >nul
)

set "UDD=%LOCALAPPDATA%\Microsoft\Edge\User Data"
set "EXE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EXE%" set "EXE=C:\Program Files\Microsoft\Edge\Application\msedge.exe"

echo [i] Starting Edge with debug port 9334
start "" "%EXE%" --remote-debugging-port=9334 --user-data-dir="%UDD%" --no-first-run --no-default-browser-check --start-maximized "https://github.com/login"

echo [i] Sign in inside the new window, then tell me login is done.
timeout /t 6 >nul
