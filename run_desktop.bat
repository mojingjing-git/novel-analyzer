@echo off
setlocal EnableExtensions
chcp 65001 >nul
echo === Novel Analyzer Desktop Mode ===
echo.

rem Enter project root (supports Chinese paths and network drives)
pushd "%~dp0"
set "SHARE_ROOT=%CD%"

rem ---- Diagnostic log: all step outputs go to disk for post-mortem ----
set "RUN_LOG=%LOCALAPPDATA%\NovelAnalyzer\run.log"
if not exist "%LOCALAPPDATA%\NovelAnalyzer" mkdir "%LOCALAPPDATA%\NovelAnalyzer"
echo [%date% %time%] === run_desktop start (root=%SHARE_ROOT%) === >> "%RUN_LOG%"

rem ---- Locate Python: prefer project .venv, fall back to PATH (2026-08-22) ----
rem Reason: previously `where python` returned the WindowsApps Python (bare install)
rem which is missing json_repair/json5/websockets, breaking the JSON parse chain.
rem The project .venv\Scripts\python.exe has all deps; prefer it for full robustness.
rem Fallback to PATH only when .venv is missing.
rem Note (2026-08-22): cmd's `rem` parser is buggy under chcp 65001 (UTF-8 codepage) when
rem the comment line contains Chinese + halfwidth parens -- it tries to execute the
rem comment as a command. Keep `rem` lines in ASCII to avoid spurious errors.
set "PYTHON="
if exist "%SHARE_ROOT%\.venv\Scripts\python.exe" (
  set "PYTHON=%SHARE_ROOT%\.venv\Scripts\python.exe"
) else (
  for /f "delims=" %%P in ('where python 2^>nul') do set "PYTHON=%%P"
  if not defined PYTHON (
    for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
  )
)
echo [%date% %time%] Using python: %PYTHON% >> "%RUN_LOG%"
if not defined PYTHON (
  echo [%date% %time%] ERROR: 未找到 python >> "%RUN_LOG%"
  echo [ERROR] 未找到 python，请安装 Python 3.11+ 并加入 PATH（或安装 py 启动器）。
  popd
  pause
  exit /b 1
)

rem ---- Frontend dist must exist on the share (built by build_frontend.bat) ----
if not exist "frontend\dist\index.html" (
  echo [%date% %time%] ERROR: frontend/dist 缺失 >> "%RUN_LOG%"
  echo [ERROR] 前端构建产物 frontend/dist 不存在，请先运行 build_frontend.bat 生成。
  popd
  pause
  exit /b 1
)
echo [%date% %time%] frontend/dist 存在，跳过前端构建（构建已解耦到 build_frontend.bat） >> "%RUN_LOG%"

rem ---- Assemble "local runtime copy" (WebView2 needs process path on local drive) ----
set "LOCAL_APP=%LOCALAPPDATA%\NovelAnalyzer\app"
if not exist "%LOCAL_APP%" mkdir "%LOCAL_APP%"
echo [%date% %time%] Assembling local runtime at %LOCAL_APP% >> "%RUN_LOG%"
robocopy backend "%LOCAL_APP%\backend" /E /XD __pycache__ /R:2 /W:2 /NFL /NDL >> "%RUN_LOG%"
copy /Y desktop.py "%LOCAL_APP%\desktop.py" >> "%RUN_LOG%"
copy /Y config.json "%LOCAL_APP%\config.json" >> "%RUN_LOG%"

rem ---- Launch from local copy; data/logs via NOVEL_ROOT point back to share ----
echo [%date% %time%] === launching: %PYTHON% "%LOCAL_APP%\desktop.py"  (NOVEL_ROOT=%SHARE_ROOT%) === >> "%RUN_LOG%"
set "NOVEL_ROOT=%SHARE_ROOT%"

rem ---- Open a separate window showing the complete log in real-time ----
rem Reason: without this, all python output is redirected to %RUN_LOG% (which can
rem grow to 30+ MB during long analyses) and the original cmd window sits idle
rem showing only echo + pause. User can't see what's happening without manually
rem opening the log file. This PowerShell tail window streams run.log live with
rem the last 50 lines pre-loaded, so startup and analysis are both visible.
rem Note: do this BEFORE launching desktop.py so the tail window catches the
rem launching echo and any early python errors.
rem Closing the GUI: when desktop.py exits we taskkill this window by its title
rem (set via `start "NovelAnalyzer 运行日志" ...` above) so the user only has to
rem dismiss one remaining cmd window. PowerShell -NoExit does not change the
rem host title, so the `start` title persists for the lifetime of the tail
rem process and the filter is an exact match.
if not exist "%RUN_LOG%" type nul > "%RUN_LOG%"
start "NovelAnalyzer 运行日志" powershell -NoExit -ExecutionPolicy Bypass -Command "Get-Content -Path '%RUN_LOG%' -Wait -Encoding UTF8 -Tail 50"

pushd "%LOCAL_APP%"
%PYTHON% desktop.py >> "%RUN_LOG%" 2>&1
set RUN_ERR=%ERRORLEVEL%
popd
echo [%date% %time%] === python desktop.py exited with code %RUN_ERR% === >> "%RUN_LOG%"

rem ---- Close the tail window we opened above ----
rem /T also kills any child powershell job; /F forces immediate termination.
rem The cmd window itself stays open so the user can read the exit code at pause.
taskkill /FI "WINDOWTITLE eq NovelAnalyzer 运行日志" /T /F 2>nul

popd
pause
