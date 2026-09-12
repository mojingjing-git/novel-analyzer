@echo off
REM 一键打包 v0.1.0 Windows 单文件 exe
REM 前提：本机 .venv 装好 pyinstaller pyinstaller-hooks-contrib
REM 产物：dist\novel-analyzer.exe（约 26 MB）

setlocal

echo ===============================================
echo Novel Analyzer - build_exe.bat
echo ===============================================

REM 1. 清理上一次产物
if exist build (
    echo [1/4] 清理 build/ ...
    rmdir /s /q build
)
if exist dist (
    echo [1/4] 清理 dist/ ...
    rmdir /s /q dist
)

REM 2. 前端构建（dist/ 不入库但 PyInstaller 打包需要）
echo [2/4] 构建前端 ...
call npm --prefix frontend run build
if errorlevel 1 (
    echo [ERROR] 前端构建失败
    exit /b 1
)

REM 3. PyInstaller 打包
echo [3/4] PyInstaller 打包 ...
call .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean desktop.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller 失败
    exit /b 1
)

REM 4. 验证
echo [4/4] 验证产物 ...
if exist dist\novel-analyzer.exe (
    for %%A in (dist\novel-analyzer.exe) do echo   novel-analyzer.exe = %%~zA bytes
    echo.
    echo ===============================================
    echo [OK] 打包完成：dist\novel-analyzer.exe
    echo ===============================================
) else (
    echo [ERROR] 产物未生成
    exit /b 1
)

endlocal
