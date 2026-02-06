@echo off
chcp 65001 >nul
title Discord Voice Bot - Setup

echo ============================================
echo   Discord Voice Bot - Auto Setup
echo ============================================
echo.

:: Проверяем Python
where py >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python не найден. Устанавливаю Python 3.11...
    winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [X] Не удалось установить Python. Установи вручную: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo [OK] Python 3.11 установлен. Перезапусти терминал и запусти setup.bat снова.
    pause
    exit /b 0
)

echo [OK] Python найден
py --version
echo.

:: Проверяем ffmpeg
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] ffmpeg не найден. Устанавливаю...
    winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [!] Не удалось установить ffmpeg через winget.
        echo     Скачай вручную: https://ffmpeg.org/download.html
    ) else (
        echo [OK] ffmpeg установлен
    )
) else (
    echo [OK] ffmpeg найден
)
echo.

:: Создаём venv
if not exist "venv" (
    echo [*] Создаю виртуальное окружение...
    py -3.11 -m venv venv 2>nul || py -m venv venv
    echo [OK] venv создан
) else (
    echo [OK] venv уже существует
)
echo.

:: Активируем venv
call venv\Scripts\activate.bat

:: Обновляем pip
echo [*] Обновляю pip...
python -m pip install --upgrade pip --quiet
echo.

:: Определяем GPU
echo [*] Определяю видеокарту...
set HAS_NVIDIA=0
nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    set HAS_NVIDIA=1
    echo [OK] NVIDIA GPU найден
    nvidia-smi --query-gpu=name --format=csv,noheader
) else (
    echo [i] NVIDIA GPU не найден, используем CPU/DirectML
)
echo.

:: Устанавливаем PyTorch
if %HAS_NVIDIA%==1 (
    echo [*] Устанавливаю PyTorch с CUDA...
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet
    pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 --quiet
    echo [OK] PyTorch CUDA установлен
) else (
    echo [*] Устанавливаю PyTorch CPU...
    pip install torch torchvision torchaudio --quiet
    echo [OK] PyTorch CPU установлен
)
echo.

:: Устанавливаем основные зависимости
echo [*] Устанавливаю зависимости...
pip install -r requirements.txt --quiet
echo [OK] Основные зависимости установлены
echo.

:: Устанавливаем edge-tts
echo [*] Устанавливаю edge-tts...
pip install edge-tts --quiet
echo [OK] edge-tts установлен
echo.

:: Для AMD GPU — onnx-asr + DirectML
if %HAS_NVIDIA%==0 (
    echo [*] Устанавливаю onnx-asr + DirectML (для AMD GPU)...
    pip install onnx-asr[hub] onnxruntime-directml --quiet
    echo [OK] onnx-asr + DirectML установлены
    echo.
)

:: Создаём .env если нет
if not exist ".env" (
    echo [*] Создаю .env из .env.example...
    copy .env.example .env >nul
    echo [!] Отредактируй .env - впиши свой DISCORD_BOT_TOKEN и OPENAI_API_KEY
    echo.
    
    :: Автоматически выбираем STT backend
    if %HAS_NVIDIA%==1 (
        echo [i] NVIDIA GPU найден - рекомендуется STT_BACKEND=realtime
        echo     Для лучшего качества: STT_MODEL=large-v3
        echo     Для быстрого старта: STT_MODEL=base
    ) else (
        echo [i] Нет NVIDIA - рекомендуется STT_BACKEND=onnx
        echo     STT_MODEL=onnx-community/whisper-base
    )
) else (
    echo [OK] .env уже существует
)
echo.

echo ============================================
echo   Установка завершена!
echo ============================================
echo.
echo   Следующие шаги:
echo   1. Отредактируй .env (токены)
echo   2. Запусти: run.bat
echo.
pause
