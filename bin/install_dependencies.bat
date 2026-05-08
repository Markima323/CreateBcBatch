@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if not errorlevel 1 (
  python -m pip install -r requirements.txt
  goto :end
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m pip install -r requirements.txt
  goto :end
)

echo No usable Python interpreter was found.
:end
pause
