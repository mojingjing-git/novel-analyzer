@echo off
setlocal EnableExtensions
echo === Novel Analyzer Desktop Mode ===
echo.

rem 映射/进入项目根目录（支持中文与网络盘）
pushd "%~dp0"
set "SHARE_ROOT=%CD%"

rem ---- 诊断日志：所有步骤输出落盘，便于排查 ----
set "RUN_LOG=%LOCALAPPDATA%\NovelAnalyzer\run.log"
if not exist "%LOCALAPPDATA%\NovelAnalyzer" mkdir "%LOCALAPPDATA%\NovelAnalyzer"
echo [%date% %time%] === run_desktop start (root=%SHARE_ROOT%) === >> "%RUN_LOG%"

rem ---- 稳健定位 python（2026-08-22 调整：项目 .venv 优先，PATH 中的 python 次之）----
rem 原因：run_desktop.bat 之前通过 `where python` 找到的是 WindowsApps Python（裸装），
rem      缺 json_repair/json5/websockets 等核心依赖，导致 LLM JSON 解析链残缺、Phase 0a 硬失败。
rem      项目自带的 .venv\Scripts\python.exe 已含所有依赖（webview/json_repair/json5/...），
rem      优先使用它能让整个容错链恢复完整；找不到 .venv 才回退到 PATH。
rem 注意（2026-08-22 修订）：不能在 ( ... ) 块内放 :label，cmd 会把 :label 当命令执行，
rem      报错「'XXX' 不是内部或外部命令」（XXX 是按 cmd 当前代码页解码后的乱码）。
rem      同时 %PYTHON% 在 ( ... ) 内 echo 会被命令解析期的旧值替换；把日志移到块外即可。
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

rem ---- 前端构建产物必须在共享盘上（由 build_frontend.bat 生成）----
if not exist "frontend\dist\index.html" (
  echo [%date% %time%] ERROR: frontend/dist 缺失 >> "%RUN_LOG%"
  echo [ERROR] 前端构建产物 frontend/dist 不存在，请先运行 build_frontend.bat 生成。
  popd
  pause
  exit /b 1
)
echo [%date% %time%] frontend/dist 存在，跳过前端构建（构建已解耦到 build_frontend.bat） >> "%RUN_LOG%"

rem ---- 组装“本地运行时副本”（WebView2 要求进程路径在本地盘，不能用网络盘）----
set "LOCAL_APP=%LOCALAPPDATA%\NovelAnalyzer\app"
if not exist "%LOCAL_APP%" mkdir "%LOCAL_APP%"
echo [%date% %time%] Assembling local runtime at %LOCAL_APP% >> "%RUN_LOG%"
robocopy backend "%LOCAL_APP%\backend" /E /XD __pycache__ /R:2 /W:2 /NFL /NDL >> "%RUN_LOG%"
copy /Y desktop.py "%LOCAL_APP%\desktop.py" >> "%RUN_LOG%"
copy /Y config.json "%LOCAL_APP%\config.json" >> "%RUN_LOG%"

rem ---- 从本地副本启动进程；数据/日志通过 NOVEL_ROOT 指回共享盘 ----
echo [%date% %time%] === launching: %PYTHON% "%LOCAL_APP%\desktop.py"  (NOVEL_ROOT=%SHARE_ROOT%) === >> "%RUN_LOG%"
set "NOVEL_ROOT=%SHARE_ROOT%"
pushd "%LOCAL_APP%"
%PYTHON% desktop.py >> "%RUN_LOG%" 2>&1
set RUN_ERR=%ERRORLEVEL%
popd
echo [%date% %time%] === python desktop.py exited with code %RUN_ERR% === >> "%RUN_LOG%"

popd
pause
