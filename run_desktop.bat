@echo off
chcp 65001 >nul
setlocal

echo === Novel Analyzer Desktop Mode ===
echo.

rem 切换到批处理文件所在目录（pushd 处理中文路径与盘符）
pushd "%~dp0"

echo Building frontend...
pushd "%~dp0frontend"
if errorlevel 1 (
    echo Failed to enter frontend directory!
    popd
    pause
    exit /b 1
)
call npm run build
set BUILD_ERR=%ERRORLEVEL%
popd
if not "%BUILD_ERR%"=="0" (
    echo Frontend build failed!
    pause
    exit /b 1
)

echo.
echo Launching desktop window...
pushd "%~dp0"
python "%~dp0desktop.py"
set RUN_ERR=%ERRORLEVEL%
popd
popd

pause