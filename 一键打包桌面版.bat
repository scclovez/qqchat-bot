@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

rem Every failure returns here so a double-clicked window always reaches pause.
call :main
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Build failed. Please review the error above.
if /i not "%~1"=="--no-pause" pause
exit /b %RESULT%

:main

echo.
echo ============================================
echo   SnowLuma Desktop - One-click Build
echo ============================================
echo.

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found. Install Python 3.10 or newer and try again.
        exit /b 1
    )
    set "PYTHON=python"
)

echo [1/3] Installing or updating build dependencies...
%PYTHON% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

echo.
echo [2/3] Building the desktop app. The first build may take several minutes...
%PYTHON% -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 exit /b 1

echo.
echo [3/3] Build completed.
echo Output folder: %CD%\dist
echo.
echo Note: The first launch may be slower.
echo       When the executable stays in dist, it uses the project configuration and data.
echo       Prepare the required data folders before distributing it separately.
start "" "%CD%\dist"
exit /b 0
