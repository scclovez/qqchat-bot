@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo  bot控制面板 - 一键打包 exe
echo ============================================
echo.
echo [1/3] 安装依赖（requirements + PyInstaller）...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo [2/3] 开始打包（onefile + 内置 Node，约 3-10 分钟）...
python -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 goto :fail

echo [3/3] 打包完成！
echo   产物: dist\bot控制面板.exe
echo.
echo 提示：
echo   - 首次运行 exe 会稍慢（onefile 解压）
echo   - exe 放在源码项目 dist\ 下运行时，自动共用源码目录的个人数据
echo   - 单独分发时，需在 exe 旁准备 晚晚\配置\.env 等配置
pause
exit /b 0

:fail
echo.
echo 打包失败，请检查上方错误信息（常见：node.exe 路径、杀软拦截）。
pause
exit /b 1
