@echo off
chcp 65001 >nul
echo ========================================
echo   NAS Manager - PyInstaller 打包脚本
echo ========================================
echo.

pip install pyinstaller >nul 2>&1

pyinstaller --noconfirm --onefile --windowed ^
  --name "NasManager" ^
  --icon "app_multi.ico" ^
  --add-data "services/presets.json;services" ^
  --add-data "assets/splash;assets/splash" ^
  --add-data "assets/app.ico;assets" ^
  --hidden-import keyring.backends.Windows ^
  main.py

rem 注意：打包后再对 exe 做资源更新（rcedit/CopyIcons）会破坏 onefile 的 PKG 数据，
rem 图标必须用 --icon 在打包时写入 bootloader。

if %errorlevel% equ 0 (
    echo.
    echo [成功] 打包完成: dist\NasManager.exe
) else (
    echo.
    echo [失败] 打包出错，请检查错误信息
)
pause
