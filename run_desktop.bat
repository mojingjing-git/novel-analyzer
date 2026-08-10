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

rem ---- 稳健定位 python（优先 PATH，回退到已知安装位置）----
set "PYTHON="
for /f "delims=" %%P in ('where python 2^>nul') do ( set "PYTHON=%%P" & goto :have_python )
:have_python
if not defined PYTHON (
  if exist "C:\Users\MoJingJing\AppData\Local\Programs\Python\Python312\python.exe" set "PYTHON=C:\Users\MoJingJing\AppData\Local\Programs\Python\Python312\python.exe"
)
if not defined PYTHON (
  echo [%date% %time%] ERROR: 未找到 python >> "%RUN_LOG%"
  echo [ERROR] 未找到 python，请安装 Python 3.12 并加入 PATH，或检查环境变量。
  popd
  pause
  exit /b 1
)
echo [%date% %time%] Using python: %PYTHON% >> "%RUN_LOG%"

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
