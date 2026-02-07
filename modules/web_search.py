"""Веб-поиск: LLM переформулирует запрос → DuckDuckGo ищет."""

import asyncio
import re
import time
from typing import Optional

import aiohttp

from utils.logger import setup_logger

log = setup_logger("web_search")

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

# LLM для переформулировки запросов (инициализируется из bot.py)
_llm_api_key: str = ""
_llm_base_url: str = ""
_llm_model: str = ""


def init(api_key: str, base_url: str, model: str) -> None:
    """Инициализация модуля — передаём LLM credentials из config."""
    global _llm_api_key, _llm_base_url, _llm_model
    _llm_api_key = api_key
    _llm_base_url = base_url.rstrip("/")
    _llm_model = model
    
    log.info(f"[SEARCH] Engine: {'DuckDuckGo' if DDG_AVAILABLE else 'NONE'}. LLM rewrite: {'yes' if _llm_api_key else 'no'}")


# ─── LLM Query Rewriter ───────────────────────────────────────────────

_REWRITE_PROMPT = """You are a search query optimizer. Convert the user's question into an optimal English web search query.

Rules:
- Output ONLY the search query, nothing else
- Translate from Russian to English
- For gaming questions (Dota 2, CS2, etc): use proper English game terms, hero names, item names
- For Dota 2 heroes: use official English names (e.g. Антимаг -> Anti-Mage, Магнус -> Magnus, Пудж -> Pudge)
- Add relevant keywords: "dotabuff", "build", "winrate", "meta", "2025" etc.
- For weather: "weather [city] today"
- For news: "[topic] news today 2025"
- For prices/rates: "[currency] exchange rate today"
- Keep it concise: 3-8 words max
- NEVER output Russian text, only English"""


async def _rewrite_query_llm(query: str) -> Optional[str]:
    """Переформулирует запрос через быстрый LLM вызов (~200-500ms)."""
    if not _llm_api_key:
        return None
    
    t0 = time.time()
    
    try:
        connector = aiohttp.TCPConnector(force_close=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            resp = await asyncio.wait_for(
                session.post(
                    f"{_llm_base_url}/chat/completions",
                    json={
                        "model": _llm_model,
                        "messages": [
                            {"role": "system", "content": _REWRITE_PROMPT},
                            {"role": "user", "content": query},
                        ],
                        "max_tokens": 50,
                        "temperature": 0.1,
                        "stream": False,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {_llm_api_key}",
                    },
                ).__aenter__(),
                timeout=3.0,
            )
            
            if resp.status not in (200, 201):
                log.warning(f"[REWRITE] LLM error {resp.status}")
                return None
            
            data = await resp.json()
            result = data["choices"][0]["message"]["content"].strip().strip('"\'')
            elapsed = (time.time() - t0) * 1000
            log.info(f"[REWRITE] '{query}' -> '{result}' ({elapsed:.0f}ms)")
            return result
            
    except asyncio.TimeoutError:
        log.warning(f"[REWRITE] Timeout (3s)")
        return None
    except Exception as e:
        log.warning(f"[REWRITE] Error: {e}")
        return None


# ─── Search Engine ─────────────────────────────────────────────────────

async def _ddg_search(query: str, max_results: int = 5) -> Optional[list[dict]]:
    """Поиск через DuckDuckGo (фоллбэк)."""
    if not DDG_AVAILABLE:
        return None
    
    try:
        results = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, _ddg_sync, query, max_results
            ),
            timeout=5.0,
        )
        return results if results else None
    except asyncio.TimeoutError:
        log.warning(f"[DDG] Timeout (5s)")
        return None
    except Exception as e:
        log.warning(f"[DDG] Error: {e}")
        return None


def _ddg_sync(query: str, max_results: int) -> list[dict]:
    """Синхронный DuckDuckGo поиск."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results, region="wt-wt"))


# ─── Main Search Function ─────────────────────────────────────────────

async def search(query: str, max_results: int = 5) -> Optional[str]:
    """Ищет в интернете: LLM переформулирует → DuckDuckGo ищет."""
    t0 = time.time()
    
    # Шаг 1: LLM переформулирует запрос на английский
    rewritten = await _rewrite_query_llm(query)
    search_query = rewritten or query  # фоллбэк на оригинал
    
    # Шаг 2: Ищем через DuckDuckGo
    results = await _ddg_search(search_query, max_results)
    
    elapsed = (time.time() - t0) * 1000
    
    if not results:
        log.info(f"[SEARCH] No results ({elapsed:.0f}ms) for: {search_query}")
        return None
    
    # Форматируем
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        body = r.get("body", "")
        href = r.get("href", "")
        lines.append(f"{i}. {title}: {body}")
        log.debug(f"[SEARCH] #{i}: {title} | {href}")
    
    text = "\n".join(lines)
    log.info(f"[SEARCH] {engine}: {len(results)} results ({elapsed:.0f}ms) for: {search_query}")
    return text


# ─── Search Trigger Detection ─────────────────────────────────────────

def _clean_query(text: str) -> str:
    """Убирает имя бота и мусор из текста."""
    query = text
    query = re.sub(r'^\[.*?\]:\s*', '', query)
    for name in ["андрей", "алекс", "бот", "слышь", "посмотри", "загугли", "найди", "поищи"]:
        query = re.sub(rf'\b{name}\b', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\s+', ' ', query).strip(".,!?;: \t\n")
    return query if len(query) > 3 else text


def needs_search(text: str) -> Optional[str]:
    """Определяет, нужен ли веб-поиск. Возвращает запрос или None."""
    text_lower = text.lower()
    
    # === 1. Прямые команды поиска ===
    direct_triggers = [
        "загугли", "найди", "поищи", "погугли", "посмотри в интернете",
        "что в интернете", "в гугле", "в интернете",
    ]
    for trigger in direct_triggers:
        if trigger in text_lower:
            return _clean_query(text)
    
    # === 2. Темы, требующие поиска ===
    always_search_topics = [
        # Игры
        "билд", "сборка", "сборку", "собирать", "собрать", "закупк",
        "гайд", "патч", "мета ", "метовый", "метовая", "метавый", "метровый",
        "контрпик", "контр пик", "винрейт", "пикрейт",
        "имба", "нерф", "бафф", "в мете",
        "антимаг", "инвокер", "пудж", "фантом", "спектра", "медуза",
        "магнус", "джаггер", "урса", "сларк", "тинкер", "рубик",
        "dota", "дота", "доту", "доте",
        "cs2", "кс2", "counter-strike", "контра",
        "valorant", "валорант",
        "lol", "league of legends",
        "fortnite", "фортнайт",
        "айтем", "шмотк", "dotabuff", "дотабаф",
        # Финансы
        "курс ", "стоит ", "стоимость", "цена ", "ценник",
        "доллар", "евро", "биткоин", "крипт",
        # Новости
        "новости", "что нового", "что случилось", "что произошло",
        "турнир", "чемпионат", "матч ",
        "кто выиграл", "кто победил", "результат",
        # Даты
        "когда выйдет", "когда релиз", "дата выхода", "дата релиза",
        # Погода
        "погода", "температур", "градус",
        # Инструкции
        "рецепт ", "как приготовить", "как сделать",
        "как настроить", "как установить",
    ]
    for topic in always_search_topics:
        if topic in text_lower:
            return _clean_query(text)
    
    # === 3. Вопросительные паттерны ===
    question_patterns = [
        "что собирать", "что купить", "что брать", "что качать",
        "что лучше", "что сильнее", "что мощнее",
        "как играть", "как пройти", "как победить", "как собрать",
        "какой лучш", "какая лучш", "какое лучш",
        "какой сейчас", "какая сейчас",
        "где найти", "где купить", "где взять",
        "почему нерф", "зачем нерф",
        "популярный герой", "популярная шмотк", "популярный айтем",
        "крутой в доте", "сильный герой", "лучший герой",
        "самый популярный", "самая популярная", "самый сильный",
    ]
    for pattern in question_patterns:
        if pattern in text_lower:
            return _clean_query(text)
    
    # === 4. Вопросительные слова + контекст ===
    question_re = re.search(
        r'\b(какой|какая|какое|какие|каким|какую|сколько|когда|где|кто)\b',
        text_lower
    )
    if question_re:
        clean = _clean_query(text)
        if len(clean) > 15:
            return clean
    
    return None
