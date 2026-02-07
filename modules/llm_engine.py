import asyncio
import base64
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI

from config import Config
from utils.logger import setup_logger

log = setup_logger("llm_engine")


class LLMEngine:
    """Генерация ответов через OpenAI API с поддержкой стриминга и vision."""

    def __init__(self, config: Config):
        self.config = config
        self._client: Optional[AsyncOpenAI] = None

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

        try:
            log.debug(f"LLM request: model={self.config.LLM_MODEL}, msgs={len(request_messages)}")
            stream = await self._client.chat.completions.create(
                model=self.config.LLM_MODEL,
                messages=request_messages,
                max_tokens=self.config.LLM_MAX_TOKENS,
                temperature=self.config.LLM_TEMPERATURE,
                stream=True,
            )
            log.debug("LLM stream created, reading chunks...")

            buffer = ""
            sentence_enders = {".", "!", "?", "…", "\n"}

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    buffer += delta.content
                    log.debug(f"LLM chunk: {delta.content!r}")

                    while True:
                        split_pos = -1
                        for i, char in enumerate(buffer):
                            if char in sentence_enders:
                                split_pos = i
                                break

                        if split_pos >= 0:
                            sentence = buffer[: split_pos + 1].strip()
                            buffer = buffer[split_pos + 1 :]
                            if sentence:
                                log.debug(f"LLM yielding sentence: {sentence}")
                                yield sentence
                        else:
                            break

            if buffer.strip():
                log.debug(f"LLM yielding remainder: {buffer.strip()}")
                yield buffer.strip()

            log.debug("LLM stream complete")

        except Exception as e:
            log.error(f"LLM generation error: {e}", exc_info=True)
            yield "Ой, что-то пошло не так, повтори пожалуйста."

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
