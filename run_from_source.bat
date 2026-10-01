@echo off
rem Runs the app straight from the Python code (no exe needed)
cd /d "%~dp0"
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel -q
pip install -r requirements.txt -q
python run.py
