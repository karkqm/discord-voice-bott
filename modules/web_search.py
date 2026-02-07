"""Веб-поиск через DuckDuckGo. Бесплатно, без API ключа."""

import asyncio
import time
from typing import Optional

from utils.logger import setup_logger

log = setup_logger("web_search")

try:
    from duckduckgo_search import DDGS
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False
    log.warning("duckduckgo-search not installed. Web search disabled. pip install duckduckgo-search")


async def search(query: str, max_results: int = 3, timeout: float = 5.0) -> Optional[str]:
    """Ищет в интернете и возвращает краткую сводку результатов.
    
    Args:
        query: Поисковый запрос
        max_results: Максимум результатов
        timeout: Таймаут в секундах
        
    Returns:
        Строка с результатами поиска или None если ничего не найдено
    """
    if not SEARCH_AVAILABLE:
        return None

    t0 = time.time()
    log.debug(f"[SEARCH] Query: {query}")

    try:
        results = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, _do_search, query, max_results
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning(f"[SEARCH] Timeout ({timeout}s) for: {query}")
        return None
    except Exception as e:
        log.warning(f"[SEARCH] Error: {e}")
        return None

    elapsed = (time.time() - t0) * 1000
    
    if not results:
        log.debug(f"[SEARCH] No results ({elapsed:.0f}ms)")
        return None

    # Форматируем результаты в текст для LLM
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        body = r.get("body", "")
        lines.append(f"{i}. {title}: {body}")

    text = "\n".join(lines)
    log.info(f"[SEARCH] {len(results)} results ({elapsed:.0f}ms) for: {query}")
    return text


def _do_search(query: str, max_results: int) -> list[dict]:
    """Синхронный поиск (запускается в executor)."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region="ru-ru"))


def _clean_query(text: str) -> str:
    """Убирает имя бота и мусор из текста, формируя поисковый запрос."""
    import re
    query = text
    # Убираем "[Имя]:" формат из начала (от STT)
    query = re.sub(r'^\[.*?\]:\s*', '', query)
    # Убираем обращения к боту
    for name in ["андрей", "алекс", "бот", "слышь"]:
        query = re.sub(rf'\b{name}\b', '', query, flags=re.IGNORECASE)
    query = query.strip(".,!?;: \t\n")
    return query if len(query) > 3 else text


def needs_search(text: str) -> Optional[str]:
    """Определяет, нужен ли веб-поиск для ответа на вопрос.
    
    Возвращает поисковый запрос или None.
    Широкая детекция: любой вопрос, на который нужна актуальная информация.
    """
    text_lower = text.lower()
    
    # === 1. Прямые команды поиска — всегда ищем ===
    direct_triggers = [
        "загугли", "найди", "поищи", "погугли", "посмотри в интернете",
        "что в интернете", "в гугле", "в интернете",
    ]
    for trigger in direct_triggers:
        if trigger in text_lower:
            return _clean_query(text)
    
    # === 2. Темы, которые ВСЕГДА требуют поиска (актуальная инфа) ===
    always_search_topics = [
        # Игры — билды, герои, патчи
        "билд", "сборка", "сборку", "собирать", "собрать", "закупк",
        "гайд", "патч", "мета ", "метовый", "метовая",
        "контрпик", "контр пик", "винрейт", "пикрейт",
        "имба", "нерф", "бафф",
        # Конкретные игры
        "антимаг", "инвокер", "пудж", "фантом", "спектра", "медуза",
        "dota", "дота", "доту", "доте",
        "cs2", "кс2", "counter-strike", "контра",
        "valorant", "валорант",
        "lol", "league of legends",
        "fortnite", "фортнайт",
        # Цены, курсы, финансы
        "курс ", "стоит ", "стоимость", "цена ", "ценник",
        "доллар", "евро", "биткоин", "крипт",
        # Новости, события
        "новости", "что нового", "что случилось", "что произошло",
        "турнир", "чемпионат", "матч ",
        "кто выиграл", "кто победил", "результат",
        # Даты, релизы
        "когда выйдет", "когда релиз", "дата выхода", "дата релиза",
        # Погода
        "погода", "температур", "градус",
        # Рецепты, инструкции
        "рецепт ", "как приготовить", "как сделать",
        "как настроить", "как установить",
    ]
    for topic in always_search_topics:
        if topic in text_lower:
            return _clean_query(text)
    
    # === 3. Вопросительные слова + любой контекст ===
    question_patterns = [
        "что собирать", "что купить", "что брать", "что качать",
        "что лучше", "что сильнее", "что мощнее",
        "как играть", "как пройти", "как победить", "как собрать",
        "какой лучш", "какая лучш", "какое лучш",
        "какой сейчас", "какая сейчас",
        "где найти", "где купить", "где взять",
        "почему нерф", "зачем нерф",
    ]
    for pattern in question_patterns:
        if pattern in text_lower:
            return _clean_query(text)
    
    # === 4. Вопросы с "какой/сколько/когда" + существительное ===
    import re
    question_re = re.search(
        r'\b(какой|какая|какое|какие|каким|какую|сколько|когда|где|кто)\b',
        text_lower
    )
    if question_re:
        # Есть вопросительное слово — ищем если текст достаточно конкретный (>15 символов)
        clean = _clean_query(text)
        if len(clean) > 15:
            return clean
    
    return None
