@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
  "%LocalAppData%\Programs\Python\Python314\python.exe" -m venv .venv
) else (
  py -3 -m venv .venv
)
if errorlevel 1 goto failed
:install
if exist "wheelhouse\" (
  ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheelhouse -r requirements.txt
) else (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m unittest discover -s tests_stdlib -v
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto failed
echo Setup and tests complete. Double-click Start MyMoney Hub.cmd.
pause
exit /b 0
:failed
echo Setup did not complete. See the error above and README.md.
pause
exit /b 1
