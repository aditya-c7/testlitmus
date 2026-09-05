@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel% neq 0 (
  echo No Python found. Install Python 3.10+ and retry.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtualenv (.venv)...
  python -m venv .venv
)
call ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if "%PORT%"=="" set PORT=8000
echo Serving Precedent on http://127.0.0.1:%PORT%/ (Ctrl+C to stop)
".venv\Scripts\python.exe" server.py
