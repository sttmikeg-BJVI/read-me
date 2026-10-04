@echo off
setlocal
cd /d "%~dp0\.."

echo ==========================================
echo AI Music Production Studio - Local Launcher
echo ==========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py
) else (
  where python >nul 2>nul
  if %errorlevel% neq 0 (
    echo Python is not installed or is not on PATH.
    echo Install Python 3.11+ from python.org, then run this file again.
    pause
    exit /b 1
  )
  set PY=python
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  %PY% -m venv .venv
  if %errorlevel% neq 0 goto :fail
)

call ".venv\Scripts\activate.bat"
echo Installing/updating required packages...
python -m pip install --upgrade pip >nul
pip install -r ai_music_studio\requirements.txt
if %errorlevel% neq 0 goto :fail

echo.
echo Starting studio at http://127.0.0.1:8000
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"
python -m uvicorn ai_music_studio.app:app --host 127.0.0.1 --port 8000
goto :eof

:fail
echo.
echo The studio could not start. Review the error above.
pause
exit /b 1
