import asyncio
import base64
import time
from typing import AsyncGenerator, Optional

import httpx
from openai import AsyncOpenAI

from config import Config
from utils.logger import setup_logger

log = setup_logger("llm_engine")


class LLMEngine:
    """Генерация ответов через OpenAI API с поддержкой стриминга и vision."""

    # Минимальный интервал между запросами (rate limit protection)
    _MIN_REQUEST_INTERVAL = 3.0  # секунды

    def __init__(self, config: Config):
        self.config = config
        self._client: Optional[AsyncOpenAI] = None
        self._last_request_time: float = 0.0

    def start(self) -> None:
        api_key = self.config.OPENAI_API_KEY
        base_url = self.config.OPENAI_BASE_URL

        if not api_key and not base_url:
             log.warning("No OPENAI_API_KEY or OPENAI_BASE_URL set. LLM might fail.")

        # Для локальных LLM ключ может быть любым, но не пустым
        if self.config.IS_LOCAL_LLM and not api_key:
            api_key = "local"

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url if base_url else None,
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
            max_retries=0,  # мы сами ретрайм
        )
        log.info(f"LLM engine started (model={self.config.LLM_MODEL}, base_url={base_url}, local={self.config.IS_LOCAL_LLM})")

    def stop(self) -> None:
        self._client = None
        log.info("LLM engine stopped")

    async def generate_stream(
        self,
        messages: list[dict],
        image_base64: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Стриминговая генерация ответа, отдаёт текст по предложениям.
        
        Args:
            messages: История сообщений в формате OpenAI
            image_base64: Опциональный скриншот экрана в base64
            
        Yields:
            Текст по предложениям (для быстрого начала TTS)
        """
        if not self._client:
            log.error("LLM engine not started")
            return

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

        # Rate limit: ждём минимальный интервал между запросами
        elapsed = time.time() - self._last_request_time
        if elapsed < self._MIN_REQUEST_INTERVAL:
            wait = self._MIN_REQUEST_INTERVAL - elapsed
            log.debug(f"Rate limit cooldown: {wait:.1f}s")
            await asyncio.sleep(wait)
        self._last_request_time = time.time()

        # Ретрай: 2 попытки с таймаутом 10с каждая
        for attempt in range(2):
            try:
                stream = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self.config.LLM_MODEL,
                        messages=request_messages,
                        max_tokens=self.config.LLM_MAX_TOKENS,
                        temperature=self.config.LLM_TEMPERATURE,
                        stream=True,
                    ),
                    timeout=10.0,
                )

                buffer = ""
                sentence_enders = {".", "!", "?", "…", "\n"}
                clause_enders = {",", ";", ":", "—", " – "}

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        buffer += delta.content

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
                return  # успех — выходим

            except asyncio.TimeoutError:
                if attempt == 0:
                    log.warning("LLM API timeout, retrying...")
                    await asyncio.sleep(1.0)
                else:
                    log.warning("LLM API timeout (2/2), giving up")
            except Exception as e:
                log.error(f"LLM generation error: {e}")
                return

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
