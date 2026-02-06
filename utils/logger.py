import logging
import sys


def setup_logger(name: str = "discord-bot", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)-20s | %(message)s",
            datefmt="%H:%M:%S",
        )

        # Консоль
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(formatter)
        logger.addHandler(console)

        # Файл — ловит всё включая трейсбеки
        file_handler = logging.FileHandler("bot.log", encoding="utf-8", mode="w")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


log = setup_logger()
