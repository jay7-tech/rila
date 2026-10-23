@echo off
REM Run Rilla on this laptop without Docker (uses a local SQLite database).
cd /d "%~dp0.."
if not exist .env (
  copy .env.example .env >nul
  echo Created .env. Open it, fill in BOT_TOKEN and GROQ_API_KEY, then run this again.
  notepad .env
  exit /b 1
)
if not exist .venv (
  py -3.11 -m venv .venv || py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
python -m app.main
