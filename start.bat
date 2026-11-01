@echo off
title BlockRadar
cd /d "%~dp0"

rem ---- find a working Python 3.10+ (PATH, the py launcher, or Anaconda)
set "PY="
for %%P in (python py) do (
  if not defined PY (
    %%P -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set "PY=%%P"
  )
)
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\anaconda3\python.exe" set "PY=%LOCALAPPDATA%\anaconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"

if not defined PY (
  echo.
  echo  Python 3.10 or newer was not found.
  echo  Install it from https://www.python.org/downloads/
  echo  and tick "Add python.exe to PATH" during setup. Then run this file again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo  First run: setting up BlockRadar. This takes a minute...
  "%PY%" -m venv .venv
  if errorlevel 1 goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :fail
)

echo.
echo  BlockRadar is running at http://127.0.0.1:8000
echo  Keep this window open while you use it. Close it to stop the app.
echo.
start "" cmd /c "timeout /t 5 >nul & start http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0

:fail
echo.
echo  Setup failed. Check your internet connection and try again.
echo  If it keeps failing, delete the .venv folder and run start.bat again.
pause
exit /b 1
