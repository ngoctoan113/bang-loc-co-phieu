@echo off
chcp 65001 >nul
cd /d "%~dp0"
python loc_co_phieu.py %*
echo.
pause
