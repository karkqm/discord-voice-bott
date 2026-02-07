import threading
from typing import Optional, Callable

from utils.logger import setup_logger

log = setup_logger("tts_engine")

class TTSEngine:
    """TTS через RealtimeTTS (EdgeEngine) — стриминговый синтез речи."""

    def __init__(
        self,
        engine: str = "edge",
        voice: str = "ru-RU-DmitryNeural",
        on_audio_chunk: Optional[Callable[[bytes, int], None]] = None
    ):
        self.engine_name = engine
        self.voice = voice
        self.on_audio_chunk = on_audio_chunk
        self._stream = None
        self._ready = False
        self._is_speaking = False

    def start(self) -> None:
        """Запускает инициализацию RealtimeTTS в фоновом потоке."""
        self._init_thread = threading.Thread(target=self._init_engine, daemon=True)
        self._init_thread.start()
        log.info("TTS engine initialization started in background...")

    def _init_engine(self) -> None:
        """Инициализация RealtimeTTS."""
        try:
            from RealtimeTTS import TextToAudioStream, EdgeEngine

            # TODO: Поддержка Coqui/System если нужно, пока только Edge
            tts_engine = EdgeEngine()
            tts_engine.set_voice(self.voice)

            self._stream = TextToAudioStream(
                tts_engine, 
                log_characters=False,
                on_audio_stream_start=self._on_stream_start,
                on_audio_stream_stop=self._on_stream_stop,
            )
            self._ready = True
            log.info(f"TTS engine ready (RealtimeTTS EdgeEngine, voice={self.voice})")
        except Exception as e:
            log.error(f"Failed to start TTS engine: {e}", exc_info=True)

    def _on_stream_start(self):
        self._is_speaking = True

    def _on_stream_stop(self):
        self._is_speaking = False

    def feed(self, text: str) -> None:
        """Подает текст в TTS поток."""
        if not self._ready or not self._stream:
            log.warning("TTS not ready, skipping text")
            return
            
        if not text.strip():
            return

        # Если поток еще не играет, запускаем (в отдельном потоке RealtimeTTS)
        if not self._stream.is_playing():
             # play_async запустит worker.
             self._stream.play_async(
                 muted=True,                 # Не играть на динамики сервера
                 on_audio_chunk=self._on_chunk_wrapper,
                 language="ru"
             )
        
        self._stream.feed(text)
        log.debug(f"TTS feed: {text[:30]}...")

    def _on_chunk_wrapper(self, chunk: bytes):
        """Обертка для колбэка, чтобы передавать sample_rate если нужно."""
        if self.on_audio_chunk:
            # EdgeTTS дает 24000Hz mono (обычно).
            self.on_audio_chunk(chunk, 24000)

    def stop(self) -> None:
        """Останавливает чтение и очищает очередь."""
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
        self._is_speaking = False

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking or (self._stream and self._stream.is_playing())

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Legacy метод (совместимость). Будет пустым."""
        self.feed(text)
        return None
