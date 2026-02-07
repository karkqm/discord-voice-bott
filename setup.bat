@echo off
setlocal EnableDelayedExpansion
title Discord Voice Bot - Setup

echo ============================================
echo   Discord Voice Bot - Auto Setup
echo ============================================
echo.

:: Check Python
where py >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python not found. Installing Python 3.11...
    winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [X] Failed to install Python. Install manually: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo [OK] Python 3.11 installed. Restart terminal and run setup.bat again.
    pause
    exit /b 0
)

echo [OK] Python found
py --version
echo.

:: Check ffmpeg
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] ffmpeg not found. Installing...
    winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [!] Failed to install ffmpeg via winget.
        echo     Download manually: https://ffmpeg.org/download.html
    ) else (
        echo [OK] ffmpeg installed
    )
) else (
    echo [OK] ffmpeg found
)
echo.

:: mpv no longer needed (Silero TTS is local)
echo.

:: Create venv
if not exist "venv" (
    echo [*] Creating virtual environment...
    py -3.11 -m venv venv 2>nul
    if %errorlevel% neq 0 (
        py -m venv venv
    )
    echo [OK] venv created
) else (
    echo [OK] venv already exists
)
echo.

:: Activate venv
call venv\Scripts\activate.bat

:: Upgrade pip
echo [*] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo.

:: Detect GPU
echo [*] Detecting GPU...
set HAS_NVIDIA=0
nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    set HAS_NVIDIA=1
    echo [OK] NVIDIA GPU detected
    nvidia-smi --query-gpu=name --format=csv,noheader
) else (
    echo [i] No NVIDIA GPU found, using CPU/DirectML
)
echo.

:: Install PyTorch
if "%HAS_NVIDIA%"=="1" (
    :: Check if RTX 50xx series (needs nightly PyTorch with cu128)
    set IS_RTX50=0
    for /f "tokens=*" %%g in ('nvidia-smi --query-gpu=name --format=csv,noheader 2^>nul') do (
        echo %%g | findstr /i "50" >nul && set IS_RTX50=1
    )
    if "!IS_RTX50!"=="1" (
        echo [*] RTX 50xx detected - installing PyTorch nightly with CUDA 12.8...
        pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128 --quiet
        echo [OK] PyTorch nightly CUDA 12.8 installed
    ) else (
        echo [*] Installing PyTorch with CUDA 12.1...
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet
        echo [OK] PyTorch CUDA 12.1 installed
    )
) else (
    echo [*] Installing PyTorch CPU...
    pip install torch torchvision torchaudio --quiet
    echo [OK] PyTorch CPU installed
)
echo.

:: Install main dependencies
echo [*] Installing dependencies...
pip install -r requirements.txt --quiet
echo [OK] Dependencies installed
echo.

:: Silero TTS downloads model automatically on first run
echo [i] Silero TTS will download model on first run (~100MB)
echo.

:: For AMD GPU - onnx-asr + DirectML
if "%HAS_NVIDIA%"=="0" (
    echo [*] Installing onnx-asr + DirectML for AMD GPU...
    pip install onnx-asr[hub] onnxruntime-directml --quiet
    echo [OK] onnx-asr + DirectML installed
    echo.
)

:: Create .env if missing
if not exist ".env" (
    echo [*] Creating .env from .env.example...
    copy .env.example .env >nul
    echo [!] Edit .env - set your DISCORD_BOT_TOKEN and OPENAI_API_KEY
    echo.
    if "%HAS_NVIDIA%"=="1" (
        echo [i] NVIDIA GPU detected - recommended: STT_BACKEND=realtime
        echo     Best quality: STT_MODEL=large-v3
        echo     Fast start:   STT_MODEL=base
    ) else (
        echo [i] No NVIDIA - recommended: STT_BACKEND=onnx
        echo     STT_MODEL=onnx-community/whisper-base
    )
) else (
    echo [OK] .env already exists
)
echo.

echo ============================================
echo   Setup complete!
echo ============================================
echo.
echo   Next steps:
echo   1. Edit .env (add your tokens)
echo   2. Run: run.bat
echo.
pause
