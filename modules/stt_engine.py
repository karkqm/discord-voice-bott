import collections
import threading
import time
import numpy as np
from typing import Callable, Optional

from utils.logger import setup_logger

log = setup_logger("stt_engine")

# Параметры VAD
SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 0.8  # секунд тишины после речи
MIN_SPEECH_DURATION = 0.5  # минимальная длительность речи (сек)
MAX_SPEECH_DURATION = 15.0  # максимальная длительность (сек)
VAD_CHUNK_MS = 30  # размер чанка для VAD (мс)
VAD_CHUNK_SAMPLES = int(SAMPLE_RATE * VAD_CHUNK_MS / 1000)


class STTEngine:
    """STT через onnx-asr с Silero VAD и DirectML (AMD GPU).
    
    Буферизует аудио, определяет речь через VAD,
    отправляет в onnx-asr для распознавания.
    """

    def __init__(
        self,
        model: str = "onnx-community/whisper-base",
        language: str = "ru",
        on_text_ready: Optional[Callable[[str, int], None]] = None,
    ):
        self.model_name = model
        self.language = language
        self.on_text_ready = on_text_ready
        self._model = None
        self._vad = None
        self._ready = False
        self._running = False

        # Аудио буфер и VAD состояние
        self._audio_buffer = collections.deque()
        self._is_speaking = False
        self._silence_start: float = 0
        self._speech_start: float = 0
        self._current_user_id: int = 0
        self._lock = threading.Lock()

        # VAD state (Silero VAD нужен h/c state)
        self._vad_h = None
        self._vad_c = None

    def start(self) -> None:
        """Запускает инициализацию в фоновом потоке."""
        self._running = True
        self._init_thread = threading.Thread(target=self._init_model, daemon=True)
        self._init_thread.start()
        log.info("STT engine initialization started in background...")

    def _init_model(self) -> None:
        """Загрузка onnx-asr модели и Silero VAD."""
        try:
            import onnx_asr

            providers = [("DmlExecutionProvider", {}), ("CPUExecutionProvider", {})]

            log.info(f"Loading STT model: {self.model_name}...")
            self._model = onnx_asr.load_model(self.model_name, providers=providers)

            # Принудительно ставим русский язык для Whisper
            if hasattr(self._model, 'asr') and hasattr(self._model.asr, '_transcribe_input'):
                lang_token = self._model.asr._tokens.get(f'<|{self.language}|>')
                if lang_token is not None:
                    import numpy as np
                    self._model.asr._transcribe_input[0][1] = lang_token
                    log.info(f"Set STT language to '{self.language}' (token {lang_token})")

            self._ready = True
            log.info(f"STT engine ready (onnx-asr, model={self.model_name}, DirectML)")
        except Exception as e:
            log.error(f"Failed to start STT engine: {e}", exc_info=True)

    def feed_audio(self, audio_data: bytes, user_id: int) -> None:
        """Подаёт аудио-данные (16kHz mono int16 PCM).
        
        Args:
            audio_data: Сырые PCM данные (16kHz, mono, int16)
            user_id: ID пользователя Discord
        """
        if not self._ready:
            return

        with self._lock:
            self._current_user_id = user_id

        # Конвертируем int16 PCM → float32 [-1, 1]
        pcm = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        self._audio_buffer.extend(pcm)

        # Обрабатываем VAD чанками
        self._process_vad()

    def _process_vad(self) -> None:
        """Простой VAD на основе энергии сигнала."""
        now = time.time()

        # Считаем энергию последнего чанка
        if len(self._audio_buffer) < VAD_CHUNK_SAMPLES:
            return

        # Берём последний чанк для анализа
        recent = list(self._audio_buffer)[-VAD_CHUNK_SAMPLES:]
        energy = np.sqrt(np.mean(np.array(recent) ** 2))
        is_voice = energy > 0.01  # порог энергии

        if is_voice:
            if not self._is_speaking:
                self._is_speaking = True
                self._speech_start = now
                log.info("VAD: речь началась")
            self._silence_start = 0
        else:
            if self._is_speaking and self._silence_start == 0:
                self._silence_start = now

        # Проверяем условия завершения речи
        speech_duration = now - self._speech_start if self._is_speaking else 0

        should_transcribe = False
        if self._is_speaking:
            # Тишина после речи
            if self._silence_start > 0 and (now - self._silence_start) >= SILENCE_THRESHOLD:
                should_transcribe = True
            # Максимальная длительность
            if speech_duration >= MAX_SPEECH_DURATION:
                should_transcribe = True

        if should_transcribe and speech_duration >= MIN_SPEECH_DURATION:
            audio = np.array(list(self._audio_buffer), dtype=np.float32)
            self._audio_buffer.clear()
            self._is_speaking = False
            self._silence_start = 0

            duration = len(audio) / SAMPLE_RATE
            log.info(f"VAD: речь закончилась ({duration:.1f}с), транскрибирую...")

            with self._lock:
                uid = self._current_user_id

            # Транскрибируем в отдельном потоке
            threading.Thread(
                target=self._transcribe, args=(audio, uid), daemon=True
            ).start()
        elif should_transcribe:
            # Слишком короткая речь — сбрасываем
            self._audio_buffer.clear()
            self._is_speaking = False
            self._silence_start = 0

    def _transcribe(self, audio: np.ndarray, user_id: int) -> None:
        """Транскрибирует аудио в текст."""
        try:
            t0 = time.time()
            result = self._model.recognize(audio, sample_rate=SAMPLE_RATE)
            elapsed = time.time() - t0

            if not result or not result.strip():
                return

            text = result.strip()

            # Фильтруем мусор
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

            log.info(f"STT [{user_id}]: {text} ({elapsed:.1f}s)")

            if self.on_text_ready:
                self.on_text_ready(text, user_id)

        except Exception as e:
            log.error(f"STT transcription error: {e}", exc_info=True)

    def stop(self) -> None:
        """Останавливает STT движок."""
        self._running = False
        self._ready = False
        self._model = None
        self._vad = None
        self._audio_buffer.clear()
        log.info("STT engine stopped")
