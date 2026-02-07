"""
Стресс-тест API neuroapi.host
Запуск: python test_api_stress.py

Тестирует:
1. Серия из 20 запросов подряд (с паузой 1с) — процент успеха
2. Быстрая очередь — 5 запросов без паузы (rate limit?)
3. Параллельные запросы — 3 одновременно (конкурентность)
4. Время до первого байта vs полный ответ
5. DNS resolve время отдельно

Результат: таблица с таймингами + итоговая статистика
"""

import asyncio
import aiohttp
import json
import time
import os
import socket
import sys
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = (os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")
URL = f"{BASE_URL}/chat/completions"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
}

PAYLOAD = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": "Ты — тестовый бот. Отвечай коротко."},
        {"role": "user", "content": "Привет, как дела?"},
    ],
    "max_tokens": 50,
    "temperature": 0.5,
    "stream": True,
}

# Цвета
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"

if sys.platform == "win32":
    os.system("")  # включаем ANSI на Windows


def dns_resolve(hostname: str) -> tuple[str, float]:
    """Резолвим DNS и замеряем время."""
    t0 = time.time()
    try:
        ip = socket.gethostbyname(hostname)
        elapsed = (time.time() - t0) * 1000
        return ip, elapsed
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        return f"ERROR: {e}", elapsed


async def single_request(session: aiohttp.ClientSession, request_id: int, timeout_s: float = 10.0) -> dict:
    """Один запрос к API. Возвращает результат с таймингами."""
    result = {
        "id": request_id,
        "success": False,
        "status": 0,
        "t_connect": 0.0,      # время до HTTP ответа (connect + TLS + server processing)
        "t_first_byte": 0.0,   # время до первого SSE data чанка с контентом
        "t_total": 0.0,        # общее время
        "tokens": 0,
        "response": "",
        "error": "",
    }

    t0 = time.time()

    try:
        async with session.post(URL, json=PAYLOAD, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
            result["t_connect"] = (time.time() - t0) * 1000
            result["status"] = resp.status

            if resp.status != 200:
                body = await resp.text()
                result["error"] = f"HTTP {resp.status}: {body[:200]}"
                result["t_total"] = (time.time() - t0) * 1000
                return result

            text = ""
            first_content = True

            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or line.startswith(":"):
                    continue
                if line == "data: [DONE]":
                    break
                if not line.startswith("data: "):
                    continue

                try:
                    chunk = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    if first_content:
                        result["t_first_byte"] = (time.time() - t0) * 1000
                        first_content = False
                    text += content
                    result["tokens"] += 1

            result["response"] = text.strip()
            result["success"] = True
            result["t_total"] = (time.time() - t0) * 1000

    except asyncio.TimeoutError:
        result["error"] = f"TIMEOUT ({timeout_s}s)"
        result["t_total"] = (time.time() - t0) * 1000
    except aiohttp.ClientError as e:
        result["error"] = f"CONNECTION: {e}"
        result["t_total"] = (time.time() - t0) * 1000
    except Exception as e:
        result["error"] = f"ERROR: {e}"
        result["t_total"] = (time.time() - t0) * 1000

    return result


def print_result(r: dict):
    """Печатает результат одного запроса."""
    if r["success"]:
        status = f"{GREEN}OK{RESET}"
        timing = (
            f"connect={r['t_connect']:.0f}ms  "
            f"first_byte={r['t_first_byte']:.0f}ms  "
            f"total={r['t_total']:.0f}ms  "
            f"tokens={r['tokens']}"
        )
        resp = r["response"][:60].replace("\n", " ")
        print(f"  #{r['id']:>2} {status}  {timing}  {GRAY}\"{resp}\"{RESET}")
    else:
        status = f"{RED}FAIL{RESET}"
        print(f"  #{r['id']:>2} {status}  total={r['t_total']:.0f}ms  {RED}{r['error']}{RESET}")


def print_stats(results: list[dict], label: str):
    """Печатает статистику по серии запросов."""
    ok = [r for r in results if r["success"]]
    fail = [r for r in results if not r["success"]]

    total = len(results)
    ok_count = len(ok)
    fail_count = len(fail)
    pct = (ok_count / total * 100) if total else 0

    print(f"\n  {BOLD}Итого ({label}):{RESET}")
    
    if ok_count > 0:
        avg_connect = sum(r["t_connect"] for r in ok) / ok_count
        avg_first = sum(r["t_first_byte"] for r in ok) / ok_count
        avg_total = sum(r["t_total"] for r in ok) / ok_count
        min_total = min(r["t_total"] for r in ok)
        max_total = max(r["t_total"] for r in ok)
        
        color = GREEN if pct >= 90 else YELLOW if pct >= 70 else RED
        print(f"  Успех: {color}{ok_count}/{total} ({pct:.0f}%){RESET}")
        print(f"  Connect:    avg={avg_connect:.0f}ms")
        print(f"  First byte: avg={avg_first:.0f}ms")
        print(f"  Total:      avg={avg_total:.0f}ms  min={min_total:.0f}ms  max={max_total:.0f}ms")
    else:
        print(f"  {RED}Все запросы провалились!{RESET}")

    if fail_count > 0:
        errors = {}
        for r in fail:
            errors[r["error"]] = errors.get(r["error"], 0) + 1
        print(f"  Ошибки:")
        for err, count in errors.items():
            print(f"    {RED}{count}x{RESET} {err}")


async def test_sequential(count: int = 20, delay: float = 1.0, timeout: float = 10.0):
    """Тест 1: последовательные запросы с паузой."""
    print(f"\n{BOLD}{CYAN}═══ ТЕСТ 1: {count} запросов подряд (пауза {delay}с, таймаут {timeout}с) ═══{RESET}")
    
    connector = aiohttp.TCPConnector(force_close=True)
    results = []

    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(count):
            r = await single_request(session, i + 1, timeout_s=timeout)
            results.append(r)
            print_result(r)
            if i < count - 1:
                await asyncio.sleep(delay)

    print_stats(results, f"последовательные x{count}")
    return results


async def test_burst(count: int = 5, timeout: float = 10.0):
    """Тест 2: быстрая очередь без пауз."""
    print(f"\n{BOLD}{CYAN}═══ ТЕСТ 2: {count} запросов БЕЗ паузы (burst, таймаут {timeout}с) ═══{RESET}")
    
    connector = aiohttp.TCPConnector(force_close=True)
    results = []

    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(count):
            r = await single_request(session, i + 1, timeout_s=timeout)
            results.append(r)
            print_result(r)

    print_stats(results, f"burst x{count}")
    return results


async def test_parallel(count: int = 3, timeout: float = 10.0):
    """Тест 3: параллельные запросы."""
    print(f"\n{BOLD}{CYAN}═══ ТЕСТ 3: {count} параллельных запросов (таймаут {timeout}с) ═══{RESET}")
    
    connector = aiohttp.TCPConnector(force_close=True)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [single_request(session, i + 1, timeout_s=timeout) for i in range(count)]
        results = await asyncio.gather(*tasks)

    results = list(results)
    for r in results:
        print_result(r)

    print_stats(results, f"параллельные x{count}")
    return results


async def test_new_session_each(count: int = 10, timeout: float = 10.0):
    """Тест 4: каждый запрос — новая сессия (как в боте)."""
    print(f"\n{BOLD}{CYAN}═══ ТЕСТ 4: {count} запросов, каждый в НОВОЙ сессии (как бот, таймаут {timeout}с) ═══{RESET}")
    
    results = []
    for i in range(count):
        connector = aiohttp.TCPConnector(force_close=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            r = await single_request(session, i + 1, timeout_s=timeout)
            results.append(r)
            print_result(r)
        await asyncio.sleep(0.5)

    print_stats(results, f"новая сессия x{count}")
    return results


async def test_timeout_values():
    """Тест 5: разные таймауты — найти оптимальный."""
    print(f"\n{BOLD}{CYAN}═══ ТЕСТ 5: Поиск оптимального таймаута ═══{RESET}")
    
    timeouts = [3, 5, 8, 10, 15]
    
    for t in timeouts:
        print(f"\n  {YELLOW}--- Таймаут: {t}с (3 запроса) ---{RESET}")
        connector = aiohttp.TCPConnector(force_close=True)
        ok = 0
        async with aiohttp.ClientSession(connector=connector) as session:
            for i in range(3):
                r = await single_request(session, i + 1, timeout_s=t)
                print_result(r)
                if r["success"]:
                    ok += 1
                await asyncio.sleep(0.5)
        pct = ok / 3 * 100
        color = GREEN if pct >= 90 else YELLOW if pct >= 50 else RED
        print(f"  Результат: {color}{ok}/3 ({pct:.0f}%){RESET}")


async def main():
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  СТРЕСС-ТЕСТ API: {URL}{RESET}")
    print(f"{BOLD}  Модель: {MODEL}{RESET}")
    print(f"{BOLD}  API Key: {API_KEY[:8]}...{API_KEY[-4:]}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    # DNS тест
    parsed = urlparse(URL)
    hostname = parsed.hostname
    print(f"\n{CYAN}DNS resolve: {hostname}{RESET}")
    for i in range(3):
        ip, ms = dns_resolve(hostname)
        print(f"  #{i+1}: {ip} ({ms:.1f}ms)")

    # Тесты
    await test_sequential(count=20, delay=1.0, timeout=10.0)
    await test_burst(count=5, timeout=10.0)
    await test_parallel(count=3, timeout=10.0)
    await test_new_session_each(count=10, timeout=10.0)
    await test_timeout_values()

    print(f"\n{BOLD}{GREEN}{'='*60}{RESET}")
    print(f"{BOLD}{GREEN}  ТЕСТ ЗАВЕРШЁН{RESET}")
    print(f"{BOLD}{GREEN}{'='*60}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
