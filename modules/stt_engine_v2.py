"""STT Engine v2 — RealtimeSTT (faster-whisper + CUDA).

Для использования с NVIDIA GPU. Тот же интерфейс что и stt_engine.py (v1/onnx-asr).
Переключение через .env: STT_BACKEND=realtime
"""

import threading
from typing import Callable, Optional

from utils.logger import setup_logger

log = setup_logger("stt_engine_v2")


class STTEngine:
    """Обёртка над RealtimeSTT (KoljaB/RealtimeSTT) с CUDA.
    
    Использует feed_audio() для подачи PCM чанков из Discord
    и recorder.text(callback) в цикле для получения результатов.
    """

    def __init__(
        self,
        model: str = "base",
        language: str = "ru",
        on_text_ready: Optional[Callable[[str, int], None]] = None,
        on_speech_begin: Optional[Callable[[], None]] = None,
    ):
        self.model = model
        self.language = language
        self.on_text_ready = on_text_ready
        self.on_speech_begin = on_speech_begin
        self._recorder = None
        self._ready = False
        self._current_user_id: int = 0
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        """Запускает инициализацию RealtimeSTT в фоновом потоке."""
        self._running = True
        self._init_thread = threading.Thread(target=self._init_and_run, daemon=True)
        self._init_thread.start()
        log.info("STT engine v2 (RealtimeSTT/CUDA) initialization started...")

    def _init_and_run(self) -> None:
        """Инициализирует recorder и запускает цикл text() в одном потоке."""
        try:
            from RealtimeSTT import AudioToTextRecorder

            self._recorder = AudioToTextRecorder(
                model=self.model,
                language=self.language,
                spinner=False,
                use_microphone=False,
                post_speech_silence_duration=0.8,
                silero_sensitivity=0.5,
                silero_use_onnx=True,
                webrtc_sensitivity=3,
                min_length_of_recording=0.5,
                min_gap_between_recordings=0.2,
                enable_realtime_transcription=False,
                no_log_file=True,
                level=50,
                initial_prompt="Разговор на русском языке в Discord голосовом чате.",
                on_recording_start=self._on_recording_start_wrapper,
            )
            self._ready = True
            log.info(f"STT engine v2 ready (RealtimeSTT, model={self.model}, CUDA)")

            while self._running:
                try:
                    self._recorder.text(self._on_text)
                except Exception as e:
                    if self._running:
                        log.error(f"STT text loop error: {e}")

        except Exception as e:
            log.error(f"Failed to start STT engine v2: {e}", exc_info=True)

    def _on_text(self, text: str) -> None:
        """Колбэк: получен распознанный текст."""
        if not text or not text.strip():
            return

        text = text.strip()

        if len(text) < 3:
            return

        garbage = {
            "субтитры сделал dimatorzok", "dimatorzok",
            "продолжение следует", "спасибо за просмотр",
            "подписывайтесь на канал", "www.moifilm.ru",
            "редактор субтитров", "amara.org",
            "[silence]", "silence", "thanks for watching",
            "thank you for watching", "you", "the",
        }
        if any(g in text.lower() for g in garbage):
            log.debug(f"STT filtered (garbage): {text}")
            return

        with self._lock:
            user_id = self._current_user_id

        log.info(f"STT [{user_id}]: {text}")

        if self.on_text_ready:
            try:
                self.on_text_ready(text, user_id)
            except Exception as e:
                log.error(f"Error in on_text_ready callback: {e}")

    def feed_audio(self, audio_data: bytes, user_id: int) -> None:
        """Подаёт аудио-данные (16kHz mono int16 PCM) в STT.
        
        Args:
            audio_data: Сырые PCM данные (16kHz, mono, int16)
            user_id: ID пользователя Discord
        """
        if not self._ready or not self._recorder:
            return

        with self._lock:
            self._current_user_id = user_id

        try:
            chunk_size = 4096
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                self._recorder.feed_audio(chunk)
        except Exception as e:
            log.error(f"Error feeding audio to STT: {e}")

    def _on_recording_start_wrapper(self):
        """Колбэк: началась запись (обнаружена речь)."""
        if self.on_speech_begin:
            try:
                self.on_speech_begin()
            except Exception as e:
                log.error(f"Error in on_speech_begin callback: {e}")

    def stop(self) -> None:
        """Останавливает STT движок."""
        self._running = False
        self._ready = False
        if self._recorder:
            try:
                self._recorder.shutdown()
            except Exception:
                pass
            self._recorder = None
        log.info("STT engine v2 stopped")
