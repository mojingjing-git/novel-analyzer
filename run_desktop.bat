@echo off
setlocal EnableExtensions
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
pushd "%LOCAL_APP%"
%PYTHON% desktop.py >> "%RUN_LOG%" 2>&1
set RUN_ERR=%ERRORLEVEL%
popd
echo [%date% %time%] === python desktop.py exited with code %RUN_ERR% === >> "%RUN_LOG%"

popd
pause
