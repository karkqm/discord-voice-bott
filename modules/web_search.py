"""Веб-поиск через DuckDuckGo. Бесплатно, без API ключа."""

import asyncio
import re
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


# Маппинг русских названий героев Dota 2 на английские
_DOTA_HEROES = {
    "антимаг": "Anti-Mage", "антимага": "Anti-Mage", "антимагу": "Anti-Mage",
    "инвокер": "Invoker", "инвокера": "Invoker",
    "пудж": "Pudge", "пуджа": "Pudge",
    "фантом": "Phantom Assassin", "фантомка": "Phantom Assassin",
    "спектра": "Spectre", "спектру": "Spectre",
    "медуза": "Medusa", "медузу": "Medusa",
    "магнус": "Magnus", "магнуса": "Magnus",
    "джаггернаут": "Juggernaut", "джагер": "Juggernaut",
    "урса": "Ursa", "урсу": "Ursa",
    "слардар": "Slardar", "слардара": "Slardar",
    "тинкер": "Tinker", "тинкера": "Tinker",
    "сларк": "Slark", "сларка": "Slark",
    "морф": "Morphling", "морфа": "Morphling", "морфлинг": "Morphling",
    "террорблейд": "Terrorblade", "тб": "Terrorblade",
    "фейслесс": "Faceless Void", "войд": "Faceless Void",
    "рубик": "Rubick", "рубика": "Rubick",
    "лион": "Lion", "лиона": "Lion",
    "шейкер": "Earthshaker", "шейкера": "Earthshaker",
    "тайд": "Tidehunter", "тайда": "Tidehunter",
    "энигма": "Enigma", "энигму": "Enigma",
    "виндранер": "Windranger", "винда": "Windranger",
    "кристалка": "Crystal Maiden", "цм": "Crystal Maiden",
    "лина": "Lina", "лину": "Lina",
    "снайпер": "Sniper", "снайпера": "Sniper",
    "хускар": "Huskar", "хускара": "Huskar",
    "дровка": "Drow Ranger", "дров": "Drow Ranger",
    "акс": "Axe", "акса": "Axe",
    "легион": "Legion Commander", "легионка": "Legion Commander",
    "алхимик": "Alchemist", "алхимика": "Alchemist",
    "мипо": "Meepo",
    "бристл": "Bristleback", "бристла": "Bristleback",
    "тимбер": "Timbersaw", "тимбера": "Timbersaw",
}


def _enrich_query(query: str) -> tuple[str, str]:
    """Обогащает поисковый запрос контекстом.
    
    Для игровых запросов переводит на английский и добавляет ключевые слова.
    Для остальных запросов тоже переводит на английский (DuckDuckGo плохо ищет по-русски).
    Возвращает (enriched_query, region).
    """
    q_lower = query.lower()
    
    # === Определяем контекст Dota 2 ===
    # Проверяем наличие героев
    found_hero = ""
    for ru, en in _DOTA_HEROES.items():
        if ru in q_lower:
            found_hero = en
            break
    
    # Широкий набор триггеров для Dota
    dota_keywords = [
        "дота", "dota", "доту", "доте", "доты",
        "патч", "патче", "patch",
        "билд", "сборк", "собирать", "собрать",
        "айтем", "шмотк", "item",
        "dotabuff", "дотабаф", "дотабафф",
        "винрейт", "winrate", "пикрейт",
        "метовый", "метовая", "метавый", "метавая", "метровый", "в мете", "мета ",
        "контрпик", "контр пик",
        "имба", "нерф", "бафф",
        "популярный герой", "популярная шмотк", "популярный айтем",
        "крутой в доте", "сильный герой", "лучший герой",
        "shadow fiend", "anti-mage", "sf ",
    ]
    is_dota = found_hero != "" or any(w in q_lower for w in dota_keywords)
    
    if is_dota:
        # Извлекаем номер патча если есть
        patch_match = re.search(r'(\d+\.\d+\w*)', q_lower)
        patch = patch_match.group(1) if patch_match else ""
        
        # Определяем тип запроса
        if any(w in q_lower for w in ["собирать", "собрать", "билд", "сборк", "айтем", "шмотк", "item"]):
            query_type = "best items build"
        elif any(w in q_lower for w in ["контрпик", "контр пик", "counter"]):
            query_type = "counter pick"
        elif any(w in q_lower for w in ["гайд", "как играть", "guide"]):
            query_type = "guide"
        elif any(w in q_lower for w in ["винрейт", "winrate", "процент"]):
            query_type = "winrate"
        elif any(w in q_lower for w in ["метовый", "метавый", "метровый", "в мете", "мета ", "популярн", "крутой", "сильн", "лучший", "самый"]):
            query_type = "best meta heroes highest winrate"
        else:
            query_type = "build guide"
        
        if found_hero:
            final = f"dota 2 {found_hero} {query_type}"
            if patch:
                final += f" patch {patch}"
            final += " dotabuff 2025"
        else:
            final = f"dota 2 {query_type}"
            if patch:
                final += f" patch {patch}"
            final += " dotabuff 2025"
        
        log.info(f"[SEARCH] Dota query enriched: '{query}' -> '{final}'")
        return final, "wt-wt"
    
    # === Не-Dota запросы: переводим ключевые слова на английский ===
    # DuckDuckGo плохо ищет по-русски, возвращает мусор про грамматику
    
    # Погода
    weather_match = re.search(r'погод[аеу]\s+(?:в\s+)?(.+)', q_lower)
    if weather_match or 'погод' in q_lower:
        city = weather_match.group(1).strip(' ?!.') if weather_match else ""
        if city:
            final = f"weather {city} today"
        else:
            final = "weather today"
        log.info(f"[SEARCH] Weather query: '{query}' -> '{final}'")
        return final, "wt-wt"
    
    # Новости
    news_match = re.search(r'новост[иья]\s+(?:в\s+)?(.+)', q_lower)
    if news_match or 'новост' in q_lower:
        topic = news_match.group(1).strip(' ?!.') if news_match else ""
        if topic:
            final = f"{topic} news today 2025"
        else:
            final = "news today 2025"
        log.info(f"[SEARCH] News query: '{query}' -> '{final}'")
        return final, "wt-wt"
    
    # Курсы валют
    if any(w in q_lower for w in ["курс", "доллар", "евро", "биткоин"]):
        final = query + " exchange rate today"
        log.info(f"[SEARCH] Finance query: '{query}' -> '{final}'")
        return final, "wt-wt"
    
    # Для всего остального — ищем на русском но без вопросительных слов
    # (DuckDuckGo триггерится на "какой" и возвращает грамматику)
    cleaned = re.sub(r'\b(какой|какая|какое|какие|каким|какую|сколько|когда|где|кто|что)\b', '', q_lower)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' ?!.,;:')
    if len(cleaned) > 5:
        if not any(str(y) in cleaned for y in range(2020, 2027)):
            cleaned += " 2025"
        log.info(f"[SEARCH] Cleaned query: '{query}' -> '{cleaned}'")
        return cleaned, "ru-ru"
    
    # Фоллбэк
    if not any(str(y) in query for y in range(2020, 2027)):
        query += " 2025"
    return query, "ru-ru"


async def search(query: str, max_results: int = 5, timeout: float = 5.0) -> Optional[str]:
    """Ищет в интернете и возвращает краткую сводку результатов."""
    if not SEARCH_AVAILABLE:
        return None

    # Обогащаем запрос контекстом
    enriched_query, region = _enrich_query(query)
    
    t0 = time.time()

    try:
        results = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, _do_search, enriched_query, max_results, region
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning(f"[SEARCH] Timeout ({timeout}s) for: {enriched_query}")
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
        href = r.get("href", "")
        lines.append(f"{i}. {title}: {body}")
        log.debug(f"[SEARCH] #{i}: {title} | {href}")

    text = "\n".join(lines)
    log.info(f"[SEARCH] {len(results)} results ({elapsed:.0f}ms) for: {enriched_query}")
    return text


def _do_search(query: str, max_results: int, region: str = "ru-ru") -> list[dict]:
    """Синхронный поиск (запускается в executor)."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region=region))


def _clean_query(text: str) -> str:
    """Убирает имя бота и мусор из текста, формируя поисковый запрос."""
    query = text
    # Убираем "[Имя]:" формат из начала (от STT)
    query = re.sub(r'^\[.*?\]:\s*', '', query)
    # Убираем обращения к боту
    for name in ["андрей", "алекс", "бот", "слышь", "посмотри", "загугли", "найди", "поищи"]:
        query = re.sub(rf'\b{name}\b', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\s+', ' ', query).strip(".,!?;: \t\n")
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
