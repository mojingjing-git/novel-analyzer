@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
echo === Build Novel Analyzer Frontend ===
echo === 本地构建（避开 esbuild 网络盘限制），再把 dist 同步回共享盘 ===
echo.

pushd "%~dp0"
set "SHARE_ROOT=%CD%"

set "RUN_LOG=%LOCALAPPDATA%\NovelAnalyzer\build.log"
if not exist "%LOCALAPPDATA%\NovelAnalyzer" mkdir "%LOCALAPPDATA%\NovelAnalyzer"
echo [%date% %time%] === build_frontend start === >> "%RUN_LOG%"

rem ---- 定位 npm ----
set "NPM_CMD=npm"
where npm >nul 2>nul
if errorlevel 1 (
  for /f "delims=" %%F in ('dir /b /s /a-d "%LOCALAPPDATA%\Microsoft\WinGet\Packages\npm.cmd" 2^>nul') do (
    if not defined FOUND_NPM ( set "NPM_CMD=%%F" & set "FOUND_NPM=1" )
  )
)
echo [%date% %time%] Using npm: %NPM_CMD% >> "%RUN_LOG%"

rem 1) 源码同步到本地（排除 node_modules/dist/.git）
set "LOCAL_BUILD=%LOCALAPPDATA%\NovelAnalyzer\build"
if not exist "%LOCAL_BUILD%" mkdir "%LOCAL_BUILD%"
robocopy frontend "%LOCAL_BUILD%" /E /XD node_modules dist .git .bak_* .tmp_verify /R:2 /W:2 /NFL /NDL >> "%RUN_LOG%"
if errorlevel 8 (
  echo [%date% %time%] 同步前端源码失败 >> "%RUN_LOG%"
  echo 同步前端源码失败！
  popd
  pause
  exit /b 1
)

rem 2) 本地安装依赖（仅首次；后续复用 LOCAL_BUILD\node_modules）
if not exist "%LOCAL_BUILD%\node_modules" (
  echo 正在安装前端依赖（首次较慢，需联网）...
  pushd "%LOCAL_BUILD%"
  call %NPM_CMD% install >> "%RUN_LOG%" 2>&1
  set NPM_ERR=!ERRORLEVEL!
  popd
  if not "!NPM_ERR!"=="0" (
    echo [%date% %time%] npm install 失败 >> "%RUN_LOG%"
    echo npm install 失败，详见 build.log（通常是无外网/代理拦截 npm registry）。
    popd
    pause
    exit /b 1
  )
)

rem 3) 本地构建
echo 正在构建前端（本地磁盘，避开 esbuild 网络盘问题）...
pushd "%LOCAL_BUILD%"
call %NPM_CMD% run build >> "%RUN_LOG%" 2>&1
set BUILD_ERR=%ERRORLEVEL%
popd
if not "%BUILD_ERR%"=="0" (
  echo [%date% %time%] 前端构建失败 >> "%RUN_LOG%"
  echo 前端构建失败，详见 build.log。
  popd
  pause
  exit /b 1
)

rem 4) 把 dist 同步回共享盘
robocopy "%LOCAL_BUILD%\dist" "frontend\dist" /E /R:2 /W:2 /NFL /NDL >> "%RUN_LOG%"
echo [%date% %time%] === dist 已同步回共享盘 frontend/dist === >> "%RUN_LOG%"

echo.
echo 前端构建完成，dist 已同步回共享盘。可直接双击 run_desktop.bat 启动。
popd
pause
