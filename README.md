# 🎙️ Discord Voice Bot — Живой собеседник

Автономный Discord-бот, который сидит в голосовом канале, слушает речь, комментирует стримы и общается как живой человек. Никаких команд для базовой работы — бот сам заходит и выходит.

---

## ✨ Возможности

- **Автономный режим** — бот сам заходит когда кто-то в канале, сам уходит когда все вышли
- **Голосовое общение** — распознаёт речь (STT) → генерирует ответ (LLM) → озвучивает (TTS)
- **Стриминг / Демо** — автоматически замечает когда кто-то начинает Go Live и комментирует экран
- **Веб-поиск** — знает что происходит в мире, не говорит «не знаю»
- **Музыкальный плеер** — играет музыку по запросу голосом
- **Minecraft** — может играть в Minecraft вместе с вами (опционально)
- **Reconnect** — если выкинули — сам переподключается

---

## 🧩 Стек

| Компонент | Технология |
|-----------|-----------|
| Discord | py-cord 2.6 (voice receive + playback) |
| STT | faster-whisper `large-v3-turbo` (локальный) |
| LLM | OpenAI API (gpt-4o-mini, заменяемо) |
| TTS | Silero v4 / Kokoro ONNX (локальный, без задержки) |
| Vision | OpenAI Vision API (анализ экрана) |
| VAD | Silero VAD (per-user, точное определение говорящего) |
| Поиск | DuckDuckGo (без API ключа) |

---

## 🚀 Установка

### 1. Системные зависимости

```bash
# FFmpeg (обязателен для аудио)
# Windows: скачай с https://ffmpeg.org/download.html и добавь в PATH
# Linux:
sudo apt install ffmpeg
```

### 2. Python зависимости

```bash
pip install -r requirements.txt
```

### 3. Настройка `.env`

```bash
cp .env.example .env
# Заполни DISCORD_BOT_TOKEN и OPENAI_API_KEY
```

### 4. Discord Bot Token

1. Зайди на [discord.com/developers/applications](https://discord.com/developers/applications)
2. Создай приложение → Bot
3. Включи: **Message Content Intent**, **Server Members Intent**, **Voice States**
4. Права при инвайте: `Send Messages`, `Connect`, `Speak`, `Use Voice Activity`

### 5. Запуск

```bash
python bot.py
# или
run.bat
```

---

## ⚙️ Конфигурация (`.env`)

### Основное

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Токен Discord бота | **обязательно** |
| `OPENAI_API_KEY` | Ключ OpenAI (или совместимого API) | **обязательно** |
| `OPENAI_BASE_URL` | URL для кастомного LLM (LM Studio, Ollama и т.д.) | `https://api.openai.com/v1` |
| `BOT_NAME` | Имя бота | `Андрей` |
| `BOT_ALIASES` | Слова на которые реагирует бот | `бот,алекс,андрей,слышь` |

### STT (Распознавание речи)

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `STT_BACKEND` | Движок STT: `realtime` (faster-whisper) или `onnx` (onnx-asr + DirectML) | `realtime` |
| `STT_MODEL` | Модель Whisper для `realtime`: `large-v3-turbo`, `large-v3`, `medium`, `base` | `large-v3-turbo` |
| `STT_LANGUAGE` | Язык распознавания | `ru` |
| `GPU_BACKEND` | Ускорение: `cuda` (NVIDIA), `rocm` (AMD), `cpu` | `cpu` |

### TTS (Синтез речи)

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `TTS_ENGINE` | Движок: `silero` (русский, локальный) или `kokoro` (ONNX, низкая задержка) | `silero` |
| `TTS_VOICE` | Голос Silero: `xenia`, `aidar`, `baya`, `kseniya`, `eugene` | `xenia` |
| `KOKORO_VOICE` | Голос Kokoro (при `TTS_ENGINE=kokoro`) | `af_heart` |

### LLM

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `LLM_MODEL` | Модель | `gemini-2.5-flash-lite` |
| `LLM_MAX_TOKENS` | Макс. токенов в ответе | `150` |
| `LLM_TEMPERATURE` | Температура генерации | `0.9` |
| `IS_LOCAL_LLM` | Включить режим для локального LLM | `false` |

### Прочее

| Переменная | Описание | По умолчанию |
|-----------|----------|-------------|
| `SCREEN_CAPTURE_INTERVAL` | Интервал захвата кадра в режиме демо (сек) | `5` |
| `AUTO_JOIN_CHANNEL_ID` | ID канала для автоподключения при старте | `0` (выкл) |

---

## 📋 Команды

### Голосовой канал

| Команда | Алиас | Описание |
|---------|-------|----------|
| `!join` | `!j` | Подключиться к голосовому каналу вручную |
| `!leave` | `!l` | Отключиться от голосового канала |
| `!status` | — | Показать статус бота (STT/TTS/LLM/демо/игра) |
| `!clear` | `!c` | Очистить историю диалога |

### Демонстрация экрана

| Команда | Описание |
|---------|----------|
| `!demo` | Включить / выключить режим наблюдения за экраном |
| `!demo <url>` | Разово проанализировать скриншот по ссылке |
| `!demo` + 📎 | Разово проанализировать прикреплённый скриншот |
| `!s`, `!screen`, `!d` | Алиасы для `!demo` |

> **Автоматика:** когда кто-то начинает **Go Live** в Discord — бот сам включает режим демо и начинает комментировать. Когда стрим заканчивается — выключает. Если кто-то скидывает скрин в чат — бот реагирует голосом автоматически.

### Игровой контекст

| Команда | Описание |
|---------|----------|
| `!game <название>` | Сообщить боту во что играете (например: `!game Valorant`) |
| `!game` | Сбросить игровой контекст |
| `!g` | Алиас |

> Бот будет знать контекст игры и реагировать как геймер который в ней разбирается.

### Музыка

> Управляется голосом — просто скажи что хочешь услышать:
> *«Андрей, включи что-нибудь спокойное»*, *«стоп музыка»*, *«следующий трек»*

### Minecraft (опционально)

| Команда | Описание |
|---------|----------|
| `!mc_join <host> <port>` | Подключить бота к серверу Minecraft |
| `!mc_leave` | Отключить от Minecraft |
| `!mc_status` | Статус Minecraft бота |

> Требует `javascript` pip пакет и Node.js.

---

## 🤖 Автономный режим

Бот **не требует команды `!join`** для работы:

1. Кто-то заходит в голосовой канал → бот автоматически подключается
2. Канал опустел → бот автоматически уходит и очищает историю диалога
3. Бота выкинули (сетевой сбой, рестарт) → ищет канал с живыми пользователями и переподключается

Чтобы отключить автоматику — используй `!leave`.

---

## 🖥️ Демонстрация экрана (детали)

Discord API не даёт прямого доступа к видеопотоку чужого стрима, поэтому реализованы три режима:

1. **Go Live авто-детект** — бот замечает что кто-то начал стримить (`self_stream`) и включает `screen_capture` (периодический захват через FFmpeg если задан `stream_url`)
2. **Ручной скриншот** — `!demo <url>` или `!demo` + прикреплённый файл → Vision LLM анализирует и бот комментирует голосом
3. **Авто-скрин в чате** — кто-то скинул картинку в текстовый канал → бот автоматически её видит и комментирует без команды

---

## 🔴 AMD GPU (ROCm)

```env
GPU_BACKEND=rocm
```

> **Примечание:** `faster-whisper` не поддерживает ROCm напрямую. При `GPU_BACKEND=rocm` используется CPU-путь с `int8` квантизацией — это быстрее чем `float32` CPU, но медленнее NVIDIA CUDA. Kokoro ONNX работает через ONNX Runtime который может использовать DirectML на AMD.

Для полноценного AMD ускорения рекомендуется `STT_BACKEND=onnx` (onnx-asr + DirectML):

```env
STT_BACKEND=onnx
STT_MODEL=onnx-community/whisper-base
GPU_BACKEND=rocm
```

---

## 🗣️ Локальный TTS

### Silero v4 (по умолчанию)

- Синтез в памяти через `apply_tts()` — без файлового I/O
- Поддержка CUDA (`GPU_BACKEND=cuda`)
- Русские голоса: `xenia`, `aidar`, `baya`, `kseniya`, `eugene`

### Kokoro ONNX (рекомендуется для низкой задержки)

```bash
pip install kokoro-onnx
```

```env
TTS_ENGINE=kokoro
KOKORO_VOICE=af_heart
```

- ONNX-based, работает на CPU и GPU
- Задержка < 500мс от текста до первого аудио-чанка
- Если `kokoro-onnx` не установлен — автоматически падает на Silero

---

## 🏗️ Архитектура

```
modules/
├── voice_receiver.py   # Приём аудио из Discord (48kHz stereo → 16kHz mono)
├── stt_engine.py       # STT: onnx-asr + DirectML (AMD backend)
├── stt_engine_v3.py    # STT: per-user Silero VAD + shared faster-whisper (NVIDIA/CPU)
├── llm_engine.py       # Генерация ответов (OpenAI / совместимые API)
├── tts_engine.py       # TTS: Silero v4 / Kokoro ONNX
├── voice_player.py     # Воспроизведение PCM потока в Discord (thread-safe)
├── screen_capture.py   # Захват и обработка кадров экрана
├── conversation.py     # История диалога, VAD логика, игровой контекст
├── web_search.py       # DuckDuckGo поиск + LLM переформулировка запросов
├── music_player.py     # Музыкальный плеер (yt-dlp)
└── minecraft_bot.py    # Minecraft интеграция (опционально)

utils/
├── audio_utils.py      # Ресемплинг аудио (48k→16k, 16k→48k stereo)
└── logger.py           # Цветной логгер
```

**Пайплайн:**
```
Discord Audio → RealtimeSink → STTEngine (VAD + Whisper)
                                    ↓
                              LLMEngine (стриминг по предложениям)
                                    ↓
                              TTSEngine (Silero/Kokoro)
                                    ↓
                              VoicePlayer (PCM поток в Discord)
```

---

## 🐛 Известные ограничения

- Discord API не даёт прямого доступа к видеопотоку чужого стрима — используйте ручной скриншот через `!demo`
- `faster-whisper` не поддерживает ROCm напрямую (AMD GPU) — используйте `STT_BACKEND=onnx` для AMD
- Minecraft интеграция требует Node.js и `javascript` pip пакет

---

## 📄 Лицензия

MIT
