import logging
import sys
import os

# ANSI цвета
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
WHITE = "\033[97m"
GRAY = "\033[90m"

# Иконки модулей
MODULE_STYLE = {
    "bot":           (CYAN,    "BOT"),
    "llm_engine":    (MAGENTA, "LLM"),
    "tts_engine":    (GREEN,   "TTS"),
    "stt_engine_v2": (YELLOW,  "STT"),
    "stt_engine_v3": (YELLOW,  "STT"),
    "voice_player":  (BLUE,    "AUD"),
    "voice_receiver":(GRAY,    "MIC"),
    "conversation":  (WHITE,   "CTX"),
}


class CleanConsoleFormatter(logging.Formatter):
    """Чистый формат для консоли с цветами."""

    LEVEL_STYLE = {
        logging.DEBUG:    (GRAY,   "dbg"),
        logging.INFO:     (WHITE,  "   "),
        logging.WARNING:  (YELLOW, "wrn"),
        logging.ERROR:    (RED,    "ERR"),
        logging.CRITICAL: (RED,    "!!!")
    }

    def format(self, record):
        # Время
        time_str = self.formatTime(record, "%H:%M:%S")
        
        # Уровень
        lvl_color, lvl_tag = self.LEVEL_STYLE.get(record.levelno, (WHITE, "???"))
        
        # Модуль
        mod_color, mod_tag = MODULE_STYLE.get(record.name, (GRAY, record.name[:3].upper()))
        
        # Сообщение
        msg = record.getMessage()
        
        # Форматирование с цветами
        if record.levelno >= logging.ERROR:
            line = f"{GRAY}{time_str}{RESET} {RED}{BOLD}{lvl_tag}{RESET} {mod_color}{mod_tag}{RESET} {RED}{msg}{RESET}"
        elif record.levelno >= logging.WARNING:
            line = f"{GRAY}{time_str}{RESET} {YELLOW}{lvl_tag}{RESET} {mod_color}{mod_tag}{RESET} {YELLOW}{msg}{RESET}"
        else:
            line = f"{GRAY}{time_str}{RESET} {lvl_color}{lvl_tag}{RESET} {mod_color}{mod_tag}{RESET} {msg}"
        
        # Traceback
        if record.exc_info and record.exc_info[0]:
            line += "\n" + self.formatException(record.exc_info)
        
        return line


def setup_logger(name: str = "discord-bot", level: int = logging.DEBUG) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        # Включаем ANSI на Windows
        if sys.platform == "win32":
            os.system("")

        # Консоль — показываем debug для наших модулей
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.DEBUG)
        console.setFormatter(CleanConsoleFormatter())
        logger.addHandler(console)

        # Файл — полный формат без цветов
        file_formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)-20s | %(message)s",
            datefmt="%H:%M:%S",
        )
        file_handler = logging.FileHandler("bot.log", encoding="utf-8", mode="w")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


def suppress_noisy_loggers():
    """Подавляет спам от сторонних библиотек."""
    # Silero TTS loguru спам
    try:
        from loguru import logger as loguru_logger
        loguru_logger.disable("silero_tts")
    except ImportError:
        pass
    
    # huggingface_hub warnings
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)


log = setup_logger()
suppress_noisy_loggers()
