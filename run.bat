@echo off
rem Run hsr-local with its own venv.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m hsr %*
if errorlevel 1 (
  echo.
  echo [hsr-local] server exited with an error.
  echo If it says port 21000/23301 is already in use, close the old server first.
  pause
)
