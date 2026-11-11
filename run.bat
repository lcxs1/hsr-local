@echo off
rem Run hsr-local with its own venv.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m hsr %*
