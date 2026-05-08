@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

call :choose_interpreter
if not defined PYTHON_CMD (
  echo No usable Python interpreter was found.
  goto :end
)

call :ensure_dependencies
if errorlevel 1 goto :end

call %PYTHON_CMD% create_container_batch.py --check-config

:end
pause
exit /b

:choose_interpreter
set "PYTHON_CMD="
where python >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"
if defined PYTHON_CMD goto :eof
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
goto :eof

:ensure_dependencies
call %PYTHON_CMD% -c "import PIL, fitz, win32print, win32ui" >nul 2>nul
if not errorlevel 1 goto :eof
echo Installing dependencies...
call %PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
goto :eof
