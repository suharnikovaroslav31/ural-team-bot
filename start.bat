@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .env (
  copy .env.example .env >nul
  echo Создан файл .env — вставьте BOT_TOKEN и свой Telegram ID в ADMIN_IDS
  notepad .env
)

if not exist venv (
  python -m venv venv
)

call venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python main.py
pause
