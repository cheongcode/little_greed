@echo off
call .venv\Scripts\activate
start "" http://localhost:8000
python run.py
