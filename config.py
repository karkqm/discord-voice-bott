import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Discord
    DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")

    # LLM
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "150"))
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.9"))
    
    # Custom / Local LLM
    IS_LOCAL_LLM: bool = os.getenv("IS_LOCAL_LLM", "false").lower() == "true"

    # STT
    # STT_BACKEND: "onnx" (onnx-asr + DirectML/AMD) или "realtime" (RealtimeSTT + CUDA/NVIDIA)
    STT_BACKEND: str = os.getenv("STT_BACKEND", "realtime")
    STT_MODEL: str = os.getenv("STT_MODEL", "onnx-community/whisper-base")
    STT_LANGUAGE: str = os.getenv("STT_LANGUAGE", "ru")

    # TTS (Silero v4)
    TTS_ENGINE: str = os.getenv("TTS_ENGINE", "silero")
    TTS_VOICE: str = os.getenv("TTS_VOICE", "xenia")  # xenia, aidar, baya, kseniya, eugene

    # Screen Capture
    SCREEN_CAPTURE_INTERVAL: int = int(os.getenv("SCREEN_CAPTURE_INTERVAL", "5"))
    SCREEN_CAPTURE_ENABLED: bool = os.getenv("SCREEN_CAPTURE_ENABLED", "true").lower() == "true"
    
    # Interaction
    BOT_ALIASES: list[str] = os.getenv("BOT_ALIASES", "бот,алекс,андрей,слышь").split(",")
    BARGE_IN_SENSITIVITY: float = float(os.getenv("BARGE_IN_SENSITIVITY", "0.5"))

    # Bot Personality
    BOT_NAME: str = os.getenv("BOT_NAME", "Андрей")

    SYSTEM_PROMPT: str = (
        f"Ты — {BOT_NAME}, парень 19 лет в голосовом чате Discord.\n"
        "\n"
        "КОНТЕКСТ:\n"
        "- Речь друзей распознаётся автоматически и может содержать ошибки.\n"
        "- Формат: [Имя]: текст. Твой ответ озвучивается вслух.\n"
        "\n"
        "ПРАВИЛА:\n"
        "- Отвечай КОРОТКО: 1-2 предложения максимум.\n"
        "- Разговорный русский, сленг, мат если уместно.\n"
        "- Непонятный текст — переспроси коротко.\n"
        "- НЕ используй: смайлики, звёздочки, скобки, markdown, своё имя в [].\n"
        "- Пиши только то, что можно произнести вслух.\n"
        "- Ты живой человек, не бот."
    )
