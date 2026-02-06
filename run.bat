@echo off
chcp 65001 >nul
title Discord Voice Bot

if not exist "venv" (
    echo [X] venv не найден. Сначала запусти setup.bat
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
python bot.py
pause
