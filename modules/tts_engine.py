import threading
import io
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
        
        # Буфер для накопления MP3 перед декодированием (оптимизация pydub overhead)
        self._mp3_buffer = io.BytesIO()
        self._mp3_buffer_size = 0
        self._min_decode_size = 4096  # ~0.25 сек аудио 128kbps

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
        self._mp3_buffer = io.BytesIO()
        self._mp3_buffer_size = 0

    def _on_stream_stop(self):
        # Декодируем остаток буфера
        self._flush_mp3_buffer()
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
        """Обертка для колбэка — накапливаем и декодируем MP3."""
        if not self.on_audio_chunk:
            return
            
        self._mp3_buffer.write(chunk)
        self._mp3_buffer_size += len(chunk)
        
        # Если накопили достаточно — декодируем
        if self._mp3_buffer_size >= self._min_decode_size:
            self._flush_mp3_buffer()

    def _flush_mp3_buffer(self):
        """Декодирует накопленный MP3 буфер и отправляет в PCM."""
        if self._mp3_buffer_size == 0:
            return
            
        try:
            from pydub import AudioSegment
            
            self._mp3_buffer.seek(0)
            # Декодируем MP3 -> PCM
            try:
                audio = AudioSegment.from_mp3(self._mp3_buffer)
            except Exception:
                # Если чанк битый или неполный, pydub может упасть.
                # В стриминге это возможно. Просто игнорируем ошибку декодирования конкретного куска.
                return

            # Конвертируем в нужный формат: 24kHz mono 16-bit
            audio = audio.set_frame_rate(24000).set_channels(1).set_sample_width(2)
            pcm_data = audio.raw_data
            
            self.on_audio_chunk(pcm_data, 24000)
            
            # Сбрасываем буфер
            self._mp3_buffer = io.BytesIO()
            self._mp3_buffer_size = 0
            
        except Exception as e:
            log.error(f"MP3 flush error: {e}")

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
