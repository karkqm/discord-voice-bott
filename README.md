# Discord Voice Bot — Живой собеседник

Модульный Discord-бот, который сидит в голосовом канале, слушает речь, смотрит демонстрацию экрана и общается как живой человек.

## Стек

- **Discord**: py-cord (voice receive + playback)
- **STT**: RealtimeSTT (faster-whisper, локальный)
- **LLM**: OpenAI GPT (gpt-4o-mini, заменяемо)
- **Vision**: OpenAI Vision API (анализ экрана)
- **TTS**: RealtimeTTS (Edge/System/Coqui)

## Установка

### 1. Зависимости

```bash
# FFmpeg (обязателен)
# Windows: скачай с https://ffmpeg.org/download.html и добавь в PATH
# Linux: sudo apt install ffmpeg

# Python зависимости
pip install -r requirements.txt
```

### 2. Настройка

```bash
# Скопируй .env.example в .env
copy .env.example .env    # Windows
cp .env.example .env      # Linux/Mac

# Заполни DISCORD_BOT_TOKEN и OPENAI_API_KEY в .env
```

### 3. Discord Bot Token

1. Зайди на https://discord.com/developers/applications
2. Создай новое приложение → Bot
3. Включи **Message Content Intent**, **Server Members Intent**, **Voice** permissions
4. Скопируй токен в `.env`
5. Пригласи бота на сервер с правами: Send Messages, Connect, Speak, Use Voice Activity

### 4. Запуск

```bash
python bot.py
```

## Команды

| Команда | Описание |
|---------|----------|
| `!join` / `!j` | Подключиться к голосовому каналу |
| `!leave` / `!l` | Отключиться |
| `!screen` / `!s` | Вкл/выкл анализ демонстрации экрана |
| `!clear` / `!c` | Очистить историю диалога |
| `!status` | Показать статус бота |

## Архитектура

```
modules/
├── voice_receiver.py   # Приём аудио из Discord
├── stt_engine.py       # Speech-to-Text (RealtimeSTT)
├── llm_engine.py       # Генерация ответов (OpenAI)
├── tts_engine.py       # Text-to-Speech (RealtimeTTS)
├── voice_player.py     # Воспроизведение в Discord
├── screen_capture.py   # Захват кадров экрана
└── conversation.py     # Управление диалогом
```

Каждый модуль независим и может быть заменён без изменения остальных.

## Настройка .env

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `LLM_MODEL` | Модель OpenAI | `gpt-4o-mini` |
| `STT_MODEL` | Модель Whisper | `large-v3` |
| `STT_LANGUAGE` | Язык распознавания | `ru` |
| `TTS_ENGINE` | Движок TTS | `edge` |
| `TTS_VOICE` | Голос TTS | `ru-RU-DmitryNeural` |
| `SCREEN_CAPTURE_INTERVAL` | Интервал захвата экрана (сек) | `5` |
| `BOT_NAME` | Имя бота | `Алекс` |
