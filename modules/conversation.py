import random
import time
from typing import Optional
from collections import deque

from config import Config
from utils.logger import setup_logger

log = setup_logger("conversation")


class Conversation:
    """Управление контекстом диалога и историей сообщений.
    
    Хранит историю разговора, управляет контекстным окном,
    и определяет когда бот должен говорить.
    """

    def __init__(self, config: Config, max_history: int = 10):
        self.config = config
        self.max_history = max_history
        self._history: deque[dict] = deque(maxlen=max_history)
        self._last_user_speech_time: float = 0
        self._last_bot_speech_time: float = 0
        self._last_screen_comment_time: float = 0
        self._screen_comment_cooldown: float = 15.0  # секунд между комментариями экрана
        self._msgs_since_bot_spoke: int = 0  # сколько сообщений прошло без ответа бота
        self._interject_cooldown: float = 10.0  # минимум секунд между авто-вмешательствами
        self.current_game: Optional[str] = None  # название текущей игры

    def get_messages(self, include_screen: bool = False, minecraft_context: Optional[str] = None) -> list[dict]:
        """Возвращает историю сообщений в формате OpenAI API.
        
        Args:
            include_screen: Добавить контекст о просмотре экрана
            minecraft_context: Добавить контекст о состоянии Minecraft
            
        Returns:
            Список сообщений для OpenAI Chat API
        """
        messages = [{"role": "system", "content": self.config.SYSTEM_PROMPT}]

        if self.current_game:
            messages[0]["content"] += (
                f"\nСейчас все играют в игру: {self.current_game}. "
                "Ты в курсе этой игры, комментируй и реагируй как геймер который знает её."
            )

        if include_screen:
            game_hint = f" в {self.current_game}" if self.current_game else ""
            messages[0]["content"] += (
                f"\nСейчас ты смотришь демонстрацию экрана друга{game_hint} и комментируешь. "
                "Будь как зритель стрима — реагируй на интересные моменты."
            )
        
        if minecraft_context:
            messages[0]["content"] += (
                f"\nТы сейчас находишься в игре Minecraft. Твое состояние:\n{minecraft_context}\n"
                "Ты полноценный ИИ игрок. Ты можешь всё!\n"
                "Команды управления:\n"
                "[MC: chat \"Сообщение\"] - писать в чат\n"
                "[MC: goto x y z] - идти в координаты\n"
                "[MC: follow \"Player\"] - следовать\n"
                "[MC: stop] - стоп\n"
                "[MC: mine \"block_name\" count] - добыть блоки (например: [MC: mine \"oak_log\" 5])\n"
                "[MC: attack \"entity_name\"] - атаковать моба (например: [MC: attack \"zombie\"])\n"
                "[MC: equip \"item_name\"] - взять предмет в руку\n"
                "[MC: inventory] - проверить инвентарь (хотя он есть в контексте)\n"
                "Используй английские названия блоков и мобов (oak_log, zombie, iron_sword)."
            )

        messages.extend(list(self._history))
        return messages

    def add_user_message(self, text: str, user_name: str = "User") -> None:
        """Добавляет сообщение пользователя в историю."""
        self._history.append({
            "role": "user",
            "content": f"[{user_name}]: {text}",
        })
        self._last_user_speech_time = time.time()
        self._msgs_since_bot_spoke += 1
        log.debug(f"User message added: [{user_name}]: {text} (msgs_since_bot={self._msgs_since_bot_spoke})")

    def add_bot_message(self, text: str) -> None:
        """Добавляет ответ бота в историю."""
        self._history.append({
            "role": "assistant",
            "content": text,
        })
        self._last_bot_speech_time = time.time()
        self._msgs_since_bot_spoke = 0
        log.debug(f"Bot message added: {text[:50]}...")

    def add_screen_context(self, description: str) -> None:
        """Добавляет описание того, что на экране, как системное сообщение."""
        self._history.append({
            "role": "system",
            "content": f"[На экране]: {description}",
        })
        self._last_screen_comment_time = time.time()

    def should_comment_screen(self) -> bool:
        """Определяет, стоит ли боту прокомментировать экран.
        
        Комментирует если:
        - Прошло достаточно времени с последнего комментария
        - Пользователь не говорит прямо сейчас
        """
        now = time.time()
        time_since_comment = now - self._last_screen_comment_time
        time_since_user = now - self._last_user_speech_time

        return (
            time_since_comment >= self._screen_comment_cooldown
            and time_since_user >= 3.0  # пользователь молчит 3+ секунды
        )

    def should_respond(self) -> bool:
        """Определяет, должен ли бот ответить на речь пользователя."""
        return self._last_user_speech_time > self._last_bot_speech_time

    def clear(self) -> None:
        """Очищает историю диалога."""
        self._history.clear()
        log.info("Conversation history cleared")

    def is_addressed_to_bot(self, text: str) -> bool:
        """Определяет, обращено ли сообщение к боту."""
        text_lower = text.lower()
        
        for alias in self.config.BOT_ALIASES:
            if alias.lower() in text_lower:
                return True
                    
        return False

    def should_auto_interject(self) -> bool:
        """Определяет, стоит ли боту самому встрять в разговор.
        
        Вероятность растёт с количеством сообщений без ответа:
        - 3-4 сообщения: 15%
        - 5-6 сообщений: 30%
        - 7+ сообщений: 50%
        Плюс кулдаун — не чаще раз в 10 секунд.
        """
        now = time.time()
        time_since_bot = now - self._last_bot_speech_time
        
        # Кулдаун: не встреваем слишком часто
        if time_since_bot < self._interject_cooldown:
            return False
        
        msgs = self._msgs_since_bot_spoke
        
        if msgs < 3:
            return False
        elif msgs <= 4:
            prob = 0.15
        elif msgs <= 6:
            prob = 0.30
        else:
            prob = 0.50
        
        roll = random.random()
        if roll < prob:
            log.debug(f"Auto-interject triggered (msgs={msgs}, prob={prob:.0%}, roll={roll:.2f})")
            return True
        
        return False

    @property
    def last_user_speech_time(self) -> float:
        return self._last_user_speech_time

    @property
    def history_length(self) -> int:
        return len(self._history)
