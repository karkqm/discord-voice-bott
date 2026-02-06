import os
import tempfile
import threading
from typing import Optional

from utils.logger import setup_logger

log = setup_logger("tts_engine")

# Добавляем директорию проекта в PATH для mpv.exe и libmpv-2.dll
_project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _project_dir + os.pathsep + os.environ.get("PATH", "")
try:
    os.add_dll_directory(_project_dir)
except (OSError, AttributeError):
    pass


class TTSEngine:
    """TTS через RealtimeTTS (EdgeEngine) — стриминговый синтез речи.
    
    Записывает аудио в WAV файл через output_wavfile, затем отдаёт байты.
    """

    def __init__(
        self,
        engine: str = "edge",
        voice: str = "ru-RU-DmitryNeural",
    ):
        self.engine_name = engine
        self.voice = voice
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

            tts_engine = EdgeEngine()
            tts_engine.set_voice(self.voice)

            self._stream = TextToAudioStream(tts_engine, log_characters=False)
            self._ready = True
            log.info(f"TTS engine ready (RealtimeTTS EdgeEngine, voice={self.voice})")
        except Exception as e:
            log.error(f"Failed to start TTS engine: {e}", exc_info=True)

    def _synthesize_sync(self, text: str, tmp_path: str) -> Optional[bytes]:
        """Синхронный синтез — вызывается в executor."""
        try:
            self._stream.feed(text)
            self._stream.play(
                output_wavfile=tmp_path,
                muted=True,
                language="ru",
            )

            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                with open(tmp_path, 'rb') as f:
                    return f.read()
            return None
        except Exception as e:
            log.error(f"TTS synthesis error: {e}", exc_info=True)
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Синтезирует текст в WAV аудио через RealtimeTTS (не блокирует event loop).
        
        Args:
            text: Текст для озвучивания
            
        Returns:
            WAV байты или None при ошибке
        """
        if not self._ready or not self._stream or not text.strip():
            return None

        self._is_speaking = True
        tmp_path = tempfile.mktemp(suffix=".wav")
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._synthesize_sync, text, tmp_path)
            if result:
                log.info(f"TTS synthesized {len(result)} bytes for: {text[:50]}...")
            return result
        finally:
            self._is_speaking = False

    def interrupt(self) -> None:
        self._is_speaking = False
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking
