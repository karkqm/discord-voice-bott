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

:: Install PyTorch — выбираем версию по Compute Capability GPU
set PYTORCH_INSTALLED=0
if "!HAS_NVIDIA!"=="1" (
    :: Получаем compute capability из verbose вывода nvidia-smi (надёжнее чем --query-gpu=compute_cap)
    :: Формат строки: "    CUDA Compute Capability  : 6.1"
    set CC_MAJOR=0
    set CC_MINOR=0
    for /f "tokens=4" %%v in ('nvidia-smi -q 2^>nul ^| findstr /i "Compute Capability"') do (
        for /f "tokens=1,2 delims=." %%m in ("%%v") do (
            set CC_MAJOR=%%m
            set CC_MINOR=%%n
        )
    )
    :: Запасной вариант если verbose не помог — через python
    if "!CC_MAJOR!"=="0" (
        for /f "tokens=*" %%g in ('python -c "import subprocess,re; o=subprocess.run([\"nvidia-smi\",\"--query-gpu=compute_cap\",\"--format=csv,noheader\"],capture_output=True,text=True).stdout.strip(); p=re.match(r\"(\d+)\.(\d+)\",o); print(p.group(1)+\" \"+p.group(2)) if p else print(\"0 0\")" 2^>nul') do (
            for /f "tokens=1,2" %%a in ("%%g") do (
                set CC_MAJOR=%%a
                set CC_MINOR=%%b
            )
        )
    )
    echo [i] GPU Compute Capability: !CC_MAJOR!.!CC_MINOR!

    if !CC_MAJOR! GEQ 12 (
        :: Blackwell RTX 50xx — нужен PyTorch nightly CUDA 12.8
        echo [*] Blackwell GPU ^(CC !CC_MAJOR!.!CC_MINOR!^) — PyTorch nightly CUDA 12.8...
        pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128 --quiet
        set PYTORCH_INSTALLED=1
        echo [OK] PyTorch nightly CUDA 12.8
    ) else if !CC_MAJOR! GEQ 8 (
        :: RTX 30xx / RTX 40xx — CUDA 12.1
        echo [*] Modern GPU ^(CC !CC_MAJOR!.!CC_MINOR!^) — PyTorch CUDA 12.1...
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet
        set PYTORCH_INSTALLED=1
        echo [OK] PyTorch CUDA 12.1
    ) else if !CC_MAJOR! GEQ 7 (
        :: RTX 20xx / GTX 16xx — CUDA 11.8 (PyTorch 2.1.2 поддерживает CC 7.x)
        echo [*] Turing GPU ^(CC !CC_MAJOR!.!CC_MINOR!^) — PyTorch 2.1.2 CUDA 11.8...
        pip install torch==2.1.2 torchvision==0.16.2 torchaudio==2.1.2 --index-url https://download.pytorch.org/whl/cu118 --quiet
        set PYTORCH_INSTALLED=1
        echo [OK] PyTorch 2.1.2 CUDA 11.8
    ) else if !CC_MAJOR! GEQ 6 (
        :: GTX 10xx / GTX 16xx Pascal — CUDA 11.8 (последний поддерживающий CC 6.x)
        echo [*] Pascal GPU ^(CC !CC_MAJOR!.!CC_MINOR!^) — PyTorch 2.1.2 CUDA 11.8...
        echo [i] GTX 10xx поддерживается PyTorch до версии 2.1.x ^(CUDA 11.8^)
        pip install torch==2.1.2 torchvision==0.16.2 torchaudio==2.1.2 --index-url https://download.pytorch.org/whl/cu118 --quiet
        set PYTORCH_INSTALLED=1
        echo [OK] PyTorch 2.1.2 CUDA 11.8
    ) else (
        :: Очень старый GPU — CC < 6.0, только CPU
        echo [!] GPU слишком старый ^(CC !CC_MAJOR!.!CC_MINOR!^), CUDA не поддерживается
        echo [*] Устанавливаю PyTorch CPU...
        pip install torch torchvision torchaudio --quiet
        set PYTORCH_INSTALLED=1
        echo [OK] PyTorch CPU
    )
)
if "!PYTORCH_INSTALLED!"=="0" (
    echo [*] Installing PyTorch CPU...
    pip install torch torchvision torchaudio --quiet
    echo [OK] PyTorch CPU installed
)
echo.

:: Install main dependencies (torch может подтянуться через RealtimeSTT и др. — перепинуем ниже)
echo [*] Installing dependencies...
pip install -r requirements.txt --quiet
echo [OK] Dependencies installed
echo.

:: Переустанавливаем PyTorch поверх того что могли затянуть зависимости
echo [*] Pinning correct PyTorch version for this GPU...
if "!HAS_NVIDIA!"=="1" (
    if !CC_MAJOR! GEQ 12 (
        pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128 --quiet
    ) else if !CC_MAJOR! GEQ 8 (
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet
    ) else if !CC_MAJOR! GEQ 6 (
        pip install torch==2.1.2 torchvision==0.16.2 torchaudio==2.1.2 --index-url https://download.pytorch.org/whl/cu118 --quiet
    ) else (
        pip install torch torchvision torchaudio --quiet
    )
) else (
    pip install torch torchvision torchaudio --quiet
)
echo [OK] PyTorch pinned
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
    if "!HAS_NVIDIA!"=="1" (
        if !CC_MAJOR! GEQ 6 (
            echo [i] NVIDIA GPU ^(CC !CC_MAJOR!.!CC_MINOR!^) — рекомендуется:
            echo     STT_BACKEND=realtime
            echo     STT_MODEL=large-v3-turbo
            echo     GPU_BACKEND=cuda
        ) else (
            echo [i] Старый NVIDIA GPU — рекомендуется CPU режим:
            echo     STT_BACKEND=realtime
            echo     STT_MODEL=base
            echo     GPU_BACKEND=cpu
        )
    ) else (
        echo [i] Нет NVIDIA — рекомендуется AMD/CPU режим:
        echo     STT_BACKEND=onnx
        echo     STT_MODEL=onnx-community/whisper-base
        echo     GPU_BACKEND=rocm
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
