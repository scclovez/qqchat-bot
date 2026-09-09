@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

rem 双击 .bat 时由资源管理器创建的窗口可能在错误发生后立即关闭。
rem 首次启动改由独立 cmd /k 窗口承接，保证输出始终可见。
if /i not "%~1"=="--in-console" (
    start "小晚桌面版打包" cmd /k call "%~f0" --in-console
    exit /b 0
)

set "LOG_FILE=%~dp0打包日志.txt"
call :main > "%LOG_FILE%" 2>&1
set "RESULT=%ERRORLEVEL%"
type "%LOG_FILE%"
echo.
if not "%RESULT%"=="0" echo 详细日志已保存到：%LOG_FILE%
pause
exit /b %RESULT%

:main

echo.
echo ============================================
echo   小晚桌面版 - 一键打包
echo ============================================
echo.

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [失败] 未找到 Python。请安装 Python 3.10 及以上版本后重试。
        goto :fail
    )
    set "PYTHON=python"
)

echo [1/3] 安装或更新打包依赖...
%PYTHON% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo.
echo [2/3] 正在生成桌面应用（首次通常需要数分钟）...
%PYTHON% -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 goto :fail

echo.
echo [3/3] 打包完成。
echo 产物位置：%CD%\dist\bot控制面板.exe
echo.
echo 提示：首次运行可执行文件会稍慢。
echo       产物放在项目的 dist 目录时，会使用项目根目录已有的配置和数据；
echo       如需单独分发，请连同对应的数据目录一起准备，避免丢失配置与记忆。
start "" "%CD%\dist"
exit /b 0

:fail
echo.
echo 打包未完成。请查看上方错误信息后重试。
exit /b 1
