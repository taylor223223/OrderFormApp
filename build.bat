@echo off
rem Builds dist\OrderFormApp.exe on this PC
cd /d "%~dp0"
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements-dev.txt
pyinstaller --noconfirm OrderFormApp.spec
echo.
echo Done. Your app is dist\OrderFormApp.exe
pause
