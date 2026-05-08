@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Launching container batch creator...
echo.

cd /d "%~dp0bin"

call :choose_interpreter
if not defined PYTHON_CMD (
  echo No usable Python interpreter was found.
  goto :end
)

call :ensure_dependencies
if errorlevel 1 goto :end

call %PYTHON_CMD% create_container_batch.py

echo.
if errorlevel 1 (
  echo Failed. Check the error message above.
) else (
  echo Finished. Batch output is under "%~dp0bin\output".
)
:end
pause
exit /b

:choose_interpreter
set "PYTHON_CMD="

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import PIL, fitz, win32print, win32ui" >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :eof
  )
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import PIL, fitz, win32print, win32ui" >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto :eof
  )
)

where python >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_CMD=python"
  goto :eof
)

where py >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_CMD=py -3"
  goto :eof
)

goto :eof

:ensure_dependencies
call %PYTHON_CMD% -c "import PIL, fitz, win32print, win32ui" >nul 2>nul
if not errorlevel 1 goto :eof

echo Required Python dependencies are missing for [%PYTHON_CMD%]. Installing dependencies...
call %PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies for [%PYTHON_CMD%].
  exit /b 1
)

call %PYTHON_CMD% -c "import PIL, fitz, win32print, win32ui" >nul 2>nul
if errorlevel 1 (
  echo Required dependencies are still unavailable after installation.
  exit /b 1
)

goto :eof
