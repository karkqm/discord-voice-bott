import asyncio
import base64
import json
import time
from typing import AsyncGenerator, Optional

import aiohttp

from config import Config
from utils.logger import setup_logger

log = setup_logger("llm_engine")


class LLMEngine:
    """Генерация ответов через raw HTTP (как curl). Без openai SDK, без connection pool."""

    def __init__(self, config: Config):
        self.config = config
        self._api_key: str = ""
        self._base_url: str = ""
        self._url: str = ""

    def start(self) -> None:
        self._api_key = self.config.OPENAI_API_KEY or ""
        self._base_url = (self.config.OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
        self._url = f"{self._base_url}/chat/completions"

        if not self._api_key:
            if self.config.IS_LOCAL_LLM:
                self._api_key = "local"
            else:
                log.warning("No OPENAI_API_KEY set. LLM might fail.")

        log.info(f"LLM engine started (model={self.config.LLM_MODEL}, url={self._url}, local={self.config.IS_LOCAL_LLM})")

    def stop(self) -> None:
        log.info("LLM engine stopped")

    async def generate_stream(
        self,
        messages: list[dict],
        image_base64: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Стриминговая генерация — raw HTTP SSE, как curl.
        
        Каждый вызов = новое TCP соединение. Никакого connection pool.
        """
        # Если есть скриншот, добавляем его к последнему сообщению
        request_messages = list(messages)
        if image_base64:
            request_messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Вот что сейчас на экране. Прокомментируй что видишь, если это интересно.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}",
                            "detail": "low",
                        },
                    },
                ],
            })

        payload = {
            "model": self.config.LLM_MODEL,
            "messages": request_messages,
            "max_tokens": self.config.LLM_MAX_TOKENS,
            "temperature": self.config.LLM_TEMPERATURE,
            "stream": True,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        
        CONNECT_TIMEOUT = 5   # макс время на connect + первый ответ сервера
        STREAM_TIMEOUT = 30   # макс время между SSE чанками при стриминге

        t0 = time.time()
        log.debug("[LLM] Sending request...")

        session = None
        resp = None
        try:
            connector = aiohttp.TCPConnector(force_close=True)
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None),  # мы сами контролируем
                connector=connector,
            )

            # Фаза 1: подключение + ожидание первого ответа (5с макс)
            try:
                resp = await asyncio.wait_for(
                    session.post(self._url, json=payload, headers=headers).__aenter__(),
                    timeout=CONNECT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.warning(f"LLM: сервер не ответил за {CONNECT_TIMEOUT}с — пропускаю")
                return

            t_connect = time.time() - t0
            log.debug(f"[LLM] Connected ({t_connect:.1f}s, status={resp.status})")

            if resp.status != 200:
                body = await resp.text()
                log.warning(f"LLM API error {resp.status}: {body[:200]}")
                return

            # Фаза 2: читаем SSE стрим (30с между чанками)
            buffer = ""
            sentence_enders = {".", "!", "?", "\u2026", "\n"}
            clause_enders = {",", ";", ":", "\u2014", " \u2013 "}

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
                if not content:
                    continue

                buffer += content

                while True:
                    split_pos = -1

                    for i, char in enumerate(buffer):
                        if char in sentence_enders:
                            split_pos = i
                            break
                        if char in clause_enders and i >= 15:
                            split_pos = i
                            break

                    if split_pos >= 0:
                        sentence = buffer[: split_pos + 1].strip()
                        buffer = buffer[split_pos + 1 :]
                        if sentence:
                            yield sentence
                    else:
                        break

            if buffer.strip():
                yield buffer.strip()

        except aiohttp.ClientError as e:
            log.warning(f"LLM connection error ({time.time() - t0:.1f}s): {e}")
        except Exception as e:
            log.error(f"LLM error ({time.time() - t0:.1f}s): {e}")
        finally:
            if resp is not None:
                resp.close()
            if session is not None:
                await session.close()

    async def generate(
        self,
        messages: list[dict],
        image_base64: Optional[str] = None,
    ) -> str:
        """Генерация полного ответа (не стриминг)."""
        parts = []
        async for sentence in self.generate_stream(messages, image_base64):
            parts.append(sentence)
        return " ".join(parts)
