@echo off
chcp 65001 >nul
REM ============================================================
REM 启动带调试端口的 Edge（用于采集 GitHub 登录态）
REM 为什么需要先杀进程：Chromium 在同类 profile 已有实例时，
REM 第二次启动会附着到旧进程并**忽略** --remote-debugging-port，
REM 表现为"命令没反应、端口不通"。
REM ============================================================
setlocal
set PROFILE=%~dp0reports\_tmp\edgeprofile
set PORT=9334
set EDGE="%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist %EDGE% set EDGE="%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist %EDGE% (
  echo [ERROR] 找不到 msedge.exe
  pause & exit /b 1
)

echo [1/3] 结束占用该 profile 的旧实例...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like '*edgeprofile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 >nul

echo [2/3] 启动 Edge（port %PORT%）...
start "" %EDGE% --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check --new-window https://github.com/notepad-plus-plus/notepad-plus-plus

echo [3/3] 等待端口就绪（最多 30 秒）...
powershell -NoProfile -Command "for($i=0;$i -lt 15;$i++){ try{ $r=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/json/version' -UseBasicParsing -TimeoutSec 2; Write-Host ('[OK] ' + ($r.Content | ConvertFrom-Json).Browser); exit 0 }catch{ Start-Sleep -Seconds 2 } }; Write-Host '[FAIL] 端口未就绪'; exit 1"

echo.
echo 如果窗口里 GitHub 是未登录状态，请在该窗口登录一次（登录态保存在 %PROFILE%）。
echo.
pause
