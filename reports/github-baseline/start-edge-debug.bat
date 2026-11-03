@echo off
chcp 65001 >nul
rem 以「你桌面上可见的普通窗口」打开 Edge，并开启调试端口 9334。
rem 关键：必须先关掉所有 Edge 窗口，否则新开的只会并入旧实例（端口不生效）。

tasklist /fi "imagename eq msedge.exe" | find /i "msedge.exe" >nul
if not errorlevel 1 (
  echo [!] 检测到 Edge 仍在运行，请先关闭所有 Edge 窗口后重试。
  echo     关闭后按任意键继续，或直接关掉本窗口。
  pause >nul
)

set "UDD=%LOCALAPPDATA%\Microsoft\Edge\User Data"
set "EXE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EXE%" set "EXE=C:\Program Files\Microsoft\Edge\Application\msedge.exe"

echo [i] 启动：%EXE%
echo [i] 配置：%UDD%
start "" "%EXE%" --remote-debugging-port=9334 --user-data-dir="%UDD%" --no-first-run --no-default-browser-check --start-maximized "https://github.com/login?return_to=%%2Fnotepad-plus-plus%%2Fnotepad-plus-plus"

echo [i] 已启动。请在新窗口里登录 GitHub，登录后回来告诉我「登录好了」。
timeout /t 5 >nul
