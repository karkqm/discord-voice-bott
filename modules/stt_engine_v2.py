"""STT Engine v2 — RealtimeSTT (faster-whisper + CUDA).

Для использования с NVIDIA GPU. Тот же интерфейс что и stt_engine.py (v1/onnx-asr).
Переключение через .env: STT_BACKEND=realtime
"""

import threading
import time
import struct
from collections import defaultdict
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
        self._lock = threading.Lock()
        self._running = False
        self._speech_start_time: float = 0.0
        
        # Отслеживание говорящего: накапливаем энергию аудио по каждому юзеру
        self._user_energy: defaultdict[int, float] = defaultdict(float)
        self._user_chunks: defaultdict[int, int] = defaultdict(int)

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
                post_speech_silence_duration=0.4,
                silero_sensitivity=0.4,
                silero_use_onnx=True,
                webrtc_sensitivity=3,
                min_length_of_recording=0.3,
                min_gap_between_recordings=0.1,
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
        t_recognized = time.time()

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
            return

        # Определяем говорящего по максимальной энергии аудио
        with self._lock:
            if self._user_energy:
                user_id = max(self._user_energy, key=self._user_energy.get)
            else:
                user_id = 0

        # Время распознавания
        stt_ms = (t_recognized - self._speech_start_time) * 1000 if self._speech_start_time > 0 else 0
        log.info(f"[{user_id}] ({stt_ms:.0f}ms) {text}")

        if self.on_text_ready:
            try:
                self.on_text_ready(text, user_id)
            except Exception as e:
                log.error(f"Error in on_text_ready callback: {e}")

    @staticmethod
    def _audio_energy(data: bytes) -> float:
        """Вычисляет RMS энергию аудио чанка (int16 PCM)."""
        if len(data) < 2:
            return 0.0
        n_samples = len(data) // 2
        try:
            samples = struct.unpack(f'<{n_samples}h', data[:n_samples * 2])
            rms = (sum(s * s for s in samples) / n_samples) ** 0.5
            return rms
        except Exception:
            return 0.0

    def feed_audio(self, audio_data: bytes, user_id: int) -> None:
        """Подаёт аудио-данные (16kHz mono int16 PCM) в STT.
        
        Args:
            audio_data: Сырые PCM данные (16kHz, mono, int16)
            user_id: ID пользователя Discord
        """
        if not self._ready or not self._recorder:
            return

        # Накапливаем энергию по каждому юзеру
        energy = self._audio_energy(audio_data)
        with self._lock:
            self._user_energy[user_id] += energy
            self._user_chunks[user_id] += 1

        try:
            chunk_size = 4096
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                self._recorder.feed_audio(chunk)
        except Exception as e:
            log.error(f"Error feeding audio to STT: {e}")

    def _on_recording_start_wrapper(self):
        """Колбэк: началась запись (обнаружена речь)."""
        self._speech_start_time = time.time()
        # Сбрасываем счётчики энергии для нового сегмента речи
        with self._lock:
            self._user_energy.clear()
            self._user_chunks.clear()
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
