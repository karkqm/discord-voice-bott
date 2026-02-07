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


def needs_search(text: str) -> Optional[str]:
    """Определяет, нужен ли веб-поиск для ответа на вопрос.
    
    Возвращает поисковый запрос или None.
    Триггеры: вопросы про актуальную информацию, билды, патчи, новости и т.д.
    """
    text_lower = text.lower()
    
    # Ключевые слова, указывающие на необходимость поиска
    search_triggers = [
        # Прямые запросы
        "загугли", "найди", "поищи", "погугли", "посмотри в интернете",
        "что в интернете", "в гугле",
        # Вопросы про актуальную информацию
        "какой билд", "какая сборка", "какой патч", "какая мета",
        "метовый", "метовая", "метовое",
        "какой курс", "сколько стоит",
        "когда выйдет", "когда релиз", "дата выхода",
        "последние новости", "что нового",
        # Игровые вопросы
        "билд на", "сборка на", "гайд на", "гайд по",
        "как играть за", "как собирать",
        "патч ", "патче ", "патчи ",
        "контр пик", "контрпик",
        "винрейт",
        # Общие знания, которые могут быть неактуальны
        "кто выиграл", "кто победил", "счёт матча", "счет матча",
        "результат ", "турнир",
    ]
    
    for trigger in search_triggers:
        if trigger in text_lower:
            # Формируем поисковый запрос из текста пользователя
            # Убираем обращения к боту
            query = text
            for name in ["андрей", "алекс", "бот"]:
                query = query.lower().replace(name, "").strip()
            # Убираем лишние знаки
            query = query.strip(".,!?;: ")
            if len(query) > 5:
                return query
            return text
    
    # Вопросительные предложения с "какой/какая/сколько/когда" — тоже могут требовать поиска
    question_words = ["какой ", "какая ", "какое ", "какие ", "сколько ", "когда "]
    if any(text_lower.startswith(w) or f" {w}" in text_lower for w in question_words):
        # Но только если есть конкретные слова про игры/цены/даты
        context_words = [
            "дота", "dota", "cs", "кс", "valorant", "валорант",
            "лол", "lol", "league", "патч", "билд", "сборк",
            "стоит", "курс", "цена", "выйдет", "релиз",
            "погода", "температур",
        ]
        if any(w in text_lower for w in context_words):
            query = text
            for name in ["андрей", "алекс", "бот"]:
                query = query.lower().replace(name, "").strip()
            return query.strip(".,!?;: ")
    
    return None
