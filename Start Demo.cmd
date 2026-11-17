@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup MyMoney Hub.cmd once before launching.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py --demo --port 8766
if errorlevel 1 pause
