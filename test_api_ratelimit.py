"""
Тест rate limits API neuroapi.host
Запусти: python test_api_ratelimit.py
"""
import asyncio
import time
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://neuroapi.host/v1")
MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")

client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, max_retries=0)

MESSAGES = [
    {"role": "system", "content": "Ты парень 19 лет. Отвечай коротко, 1 предложение."},
    {"role": "user", "content": "[Макс]: Здорово, как дела?"},
]


async def single_request(label: str) -> float:
    t0 = time.time()
    try:
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=MODEL, messages=MESSAGES,
                max_tokens=50, temperature=0.9, stream=True,
            ),
            timeout=15.0,
        )
        text = ""
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                text += chunk.choices[0].delta.content
        elapsed = (time.time() - t0) * 1000
        print(f"  {label}: {elapsed:.0f}ms — {text.strip()[:60]}")
        return elapsed
    except asyncio.TimeoutError:
        elapsed = (time.time() - t0) * 1000
        print(f"  {label}: TIMEOUT ({elapsed:.0f}ms)")
        return -1
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        print(f"  {label}: ERROR ({elapsed:.0f}ms) — {e}")
        return -1


async def main():
    print(f"API: {BASE_URL}")
    print(f"Model: {MODEL}")
    print(f"Key: {API_KEY[:8]}...")
    print()

    print("=== Тест 1: Прогрев ===")
    await single_request("warmup")
    print()

    print("=== Тест 2: 3 подряд без паузы ===")
    for i in range(3):
        await single_request(f"seq-{i+1}")
    print()

    print("=== Тест 3: 3 подряд с паузой 1с ===")
    for i in range(3):
        await single_request(f"1s-{i+1}")
        if i < 2: await asyncio.sleep(1.0)
    print()

    print("=== Тест 4: 3 параллельных ===")
    results = await asyncio.gather(*[single_request(f"par-{i+1}") for i in range(3)])
    print()

    print("=== Тест 5: Burst 5 подряд ===")
    for i in range(5):
        await single_request(f"burst-{i+1}")
    print()

    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
