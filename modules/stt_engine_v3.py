"""STT Engine v3 — Hybrid: per-user VAD + shared faster-whisper.

Каждый юзер имеет свой Silero VAD + аудио буфер.
Когда VAD определяет конец речи, буфер отправляется в shared Whisper модель.
100% точное определение говорящего.
"""

import threading
import time
import io
import wave
import struct
import numpy as np
from collections import defaultdict
from typing import Callable, Optional
from concurrent.futures import ThreadPoolExecutor

from utils.logger import setup_logger

log = setup_logger("stt_engine_v2")

# VAD параметры
VAD_SAMPLE_RATE = 16000
VAD_CHUNK_SAMPLES = 512  # Silero VAD требует ровно 512 сэмплов для 16kHz
VAD_CHUNK_BYTES = VAD_CHUNK_SAMPLES * 2  # 1024 bytes

# Параметры определения речи
SPEECH_THRESHOLD = 0.5       # порог VAD для определения речи
SILENCE_DURATION = 0.4       # секунды тишины для завершения фразы
MIN_SPEECH_DURATION = 0.5    # минимальная длительность речи
MAX_SPEECH_DURATION = 30.0   # максимальная длительность (защита от зависания)


class UserVADState:
    """Состояние VAD для одного пользователя."""
    
    def __init__(self):
        self.audio_buffer = bytearray()   # накопленное аудио текущей фразы
        self.is_speaking = False
        self.speech_start_time = 0.0
        self.last_speech_time = 0.0       # последний раз когда VAD видел речь
        self.vad_buffer = bytearray()     # буфер для накопления до размера VAD чанка


class STTEngine:
    """Гибридный STT: per-user Silero VAD + shared faster-whisper.
    
    Каждый юзер получает свой VAD детектор и аудио буфер.
    Одна shared Whisper модель транскрибирует аудио.
    """

    def __init__(
        self,
        model: str = "base",
        language: str = "ru",
        on_text_ready: Optional[Callable[[str, int], None]] = None,
        on_speech_begin: Optional[Callable[[], None]] = None,
    ):
        self.model_name = model
        self.language = language
        self.on_text_ready = on_text_ready
        self.on_speech_begin = on_speech_begin
        
        self._whisper_model = None
        self._vad_model = None
        self._vad_utils = None
        
        self._vad_ready = False  # VAD загружен — можно принимать аудио
        self._ready = False       # Whisper загружен — можно транскрибировать
        self._running = False
        self._lock = threading.Lock()
        self._audio_received_count = 0
        
        # Per-user состояния
        self._user_states: dict[int, UserVADState] = {}
        
        # Пул для транскрипции (1 поток — GPU не параллелится)
        self._transcribe_pool: Optional[ThreadPoolExecutor] = None
        
        # Фоновый поток для проверки таймаутов тишины
        self._monitor_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Запускает инициализацию в фоновом потоке."""
        self._running = True
        self._init_thread = threading.Thread(target=self._init_models, daemon=True)
        self._init_thread.start()
        log.info("STT engine v3 (hybrid per-user VAD + shared Whisper) initialization started...")

    def _init_models(self) -> None:
        """Загружает Silero VAD и faster-whisper модель."""
        try:
            import torch
            
            # 1. Загружаем Silero VAD (лёгкий, ONNX)
            log.info("Loading Silero VAD...")
            self._vad_model, self._vad_utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                onnx=True,
                trust_repo=True,
            )
            log.info("Silero VAD loaded")
            
            # VAD готов — можно начинать принимать аудио и детектить речь
            self._vad_ready = True
            
            # Запускаем монитор тишины
            self._monitor_thread = threading.Thread(target=self._silence_monitor, daemon=True)
            self._monitor_thread.start()
            
            # 2. Загружаем faster-whisper модель
            log.info(f"Loading faster-whisper model '{self.model_name}'...")
            from faster_whisper import WhisperModel
            
            self._whisper_model = WhisperModel(
                self.model_name,
                device="cuda",
                compute_type="float16",
            )
            log.info(f"Whisper '{self.model_name}' loaded, warming up...")
            
            # Прогрев Whisper (первый вызов на CUDA ~10s, последующие ~200ms)
            dummy = np.zeros(VAD_SAMPLE_RATE, dtype=np.float32)  # 1s тишины
            self._whisper_model.transcribe(dummy, language=self.language, beam_size=1, best_of=1, vad_filter=False)
            log.info("Whisper warmed up — STT fully ready")
            
            self._transcribe_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt")
            self._ready = True
            
        except Exception as e:
            log.error(f"Failed to start STT engine v3: {e}", exc_info=True)

    def _get_user_state(self, user_id: int) -> UserVADState:
        """Получает или создаёт состояние VAD для юзера."""
        if user_id not in self._user_states:
            self._user_states[user_id] = UserVADState()
        return self._user_states[user_id]

    def _run_vad(self, audio_chunk: bytes) -> float:
        """Запускает Silero VAD на чанке аудио. Возвращает вероятность речи 0-1."""
        if not self._vad_model:
            return 0.0
        try:
            import torch
            # int16 PCM -> float32 tensor
            samples = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(samples)
            prob = self._vad_model(tensor, VAD_SAMPLE_RATE).item()
            return prob
        except Exception as e:
            log.error(f"VAD error: {e}")
            return 0.0

    def feed_audio(self, audio_data: bytes, user_id: int) -> None:
        """Подаёт аудио от конкретного юзера в его персональный VAD."""
        if not self._vad_ready:
            return
        
        self._audio_received_count += 1
        if self._audio_received_count == 1:
            log.info(f"First audio chunk received from user {user_id} ({len(audio_data)} bytes)")
        
        with self._lock:
            state = self._get_user_state(user_id)
        
        # Добавляем в VAD буфер
        state.vad_buffer.extend(audio_data)
        
        # Обрабатываем полные VAD чанки (30ms = 960 bytes)
        while len(state.vad_buffer) >= VAD_CHUNK_BYTES:
            chunk = bytes(state.vad_buffer[:VAD_CHUNK_BYTES])
            del state.vad_buffer[:VAD_CHUNK_BYTES]
            
            # Запускаем VAD
            speech_prob = self._run_vad(chunk)
            now = time.time()
            
            if speech_prob >= SPEECH_THRESHOLD:
                # Речь обнаружена
                if not state.is_speaking:
                    # Начало речи
                    state.is_speaking = True
                    state.speech_start_time = now
                    state.audio_buffer = bytearray()
                    log.info(f"Speech start: user {user_id} (prob={speech_prob:.2f})")
                    
                    # Колбэк barge-in
                    if self.on_speech_begin:
                        try:
                            self.on_speech_begin()
                        except Exception:
                            pass
                
                state.last_speech_time = now
                state.audio_buffer.extend(chunk)
                
            elif state.is_speaking:
                # Тишина, но речь ещё идёт — добавляем в буфер
                state.audio_buffer.extend(chunk)
                
                # Проверяем таймаут тишины
                silence_elapsed = now - state.last_speech_time
                speech_duration = now - state.speech_start_time
                
                if silence_elapsed >= SILENCE_DURATION and speech_duration >= MIN_SPEECH_DURATION:
                    # Конец фразы — отправляем на транскрипцию
                    self._finalize_speech(user_id, state)
                elif speech_duration >= MAX_SPEECH_DURATION:
                    # Защита от слишком длинной записи
                    self._finalize_speech(user_id, state)

    def _finalize_speech(self, user_id: int, state: UserVADState) -> None:
        """Завершает запись речи юзера и отправляет на транскрипцию."""
        audio_data = bytes(state.audio_buffer)
        speech_start = state.speech_start_time
        
        # Сбрасываем состояние
        state.is_speaking = False
        state.audio_buffer = bytearray()
        state.speech_start_time = 0.0
        
        # Минимальная длительность
        duration = len(audio_data) / (VAD_SAMPLE_RATE * 2)  # bytes / (sample_rate * 2 bytes per sample)
        if duration < MIN_SPEECH_DURATION:
            return
        
        log.info(f"Speech end: user {user_id} ({duration:.1f}s audio)")
        
        if not self._ready:
            log.warning("Whisper not loaded yet, dropping audio")
            return
        
        # Отправляем в пул транскрипции
        if self._transcribe_pool:
            self._transcribe_pool.submit(self._transcribe, audio_data, user_id, speech_start)

    def _transcribe(self, audio_data: bytes, user_id: int, speech_start: float) -> None:
        """Транскрибирует аудио через faster-whisper (вызывается из пула)."""
        if not self._whisper_model:
            return
        
        try:
            t0 = time.time()
            
            # int16 PCM -> float32 numpy array
            audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            
            # Пропускаем слишком старые сегменты (очередь забилась)
            if t0 - speech_start > 15.0:
                log.warning(f"Dropping stale audio from user {user_id} ({t0 - speech_start:.1f}s old)")
                return
            
            # faster-whisper транскрипция
            segments_iter, info = self._whisper_model.transcribe(
                audio_np,
                language=self.language,
                beam_size=1,
                best_of=1,
                vad_filter=False,
            )
            
            seg_list = list(segments_iter)
            text = " ".join(seg.text.strip() for seg in seg_list).strip()
            
            t_transcribe = time.time()
            
            if not text or len(text) < 3:
                return
            
            # Фильтр по no_speech_prob (галлюцинации имеют высокий no_speech_prob)
            if seg_list:
                avg_no_speech = sum(s.no_speech_prob for s in seg_list) / len(seg_list)
                if avg_no_speech > 0.6:
                    log.debug(f"Filtered hallucination (no_speech={avg_no_speech:.2f}): {text}")
                    return
            
            # Фильтр мусора и галлюцинаций Whisper
            garbage = {
                "субтитры сделал", "dimatorzok",
                "продолжение следует", "спасибо за просмотр",
                "подписывайтесь на канал", "www.moifilm.ru",
                "редактор субтитров", "amara.org",
                "[silence]", "silence", "thanks for watching",
                "thank you for watching",
                "разговор на русском языке", "discord голосовом чате",
                "до новых встреч", "спасибо за внимание",
                "с вами был", "следующей части видео",
                "смех", "labeling",
            }
            if any(g in text.lower() for g in garbage):
                return
            
            # Тайминги
            total_ms = (t_transcribe - speech_start) * 1000
            transcribe_ms = (t_transcribe - t0) * 1000
            audio_duration = len(audio_np) / VAD_SAMPLE_RATE
            
            log.info(f"[{user_id}] ({total_ms:.0f}ms, whisper={transcribe_ms:.0f}ms, {audio_duration:.1f}s audio) {text}")
            
            if self.on_text_ready:
                try:
                    self.on_text_ready(text, user_id)
                except Exception as e:
                    log.error(f"Error in on_text_ready callback: {e}")
                    
        except Exception as e:
            log.error(f"Transcription error: {e}", exc_info=True)

    def _silence_monitor(self) -> None:
        """Фоновый поток: проверяет таймауты тишины для всех юзеров."""
        while self._running:
            time.sleep(0.05)  # 50ms
            
            now = time.time()
            with self._lock:
                user_ids = list(self._user_states.keys())
            
            for uid in user_ids:
                state = self._user_states.get(uid)
                if not state or not state.is_speaking:
                    continue
                
                silence_elapsed = now - state.last_speech_time
                speech_duration = now - state.speech_start_time
                
                if silence_elapsed >= SILENCE_DURATION and speech_duration >= MIN_SPEECH_DURATION:
                    self._finalize_speech(uid, state)
                elif speech_duration >= MAX_SPEECH_DURATION:
                    self._finalize_speech(uid, state)

    def stop(self) -> None:
        """Останавливает STT движок."""
        self._running = False
        self._ready = False
        if self._transcribe_pool:
            self._transcribe_pool.shutdown(wait=False)
            self._transcribe_pool = None
        self._whisper_model = None
        self._vad_model = None
        log.info("STT engine v3 stopped")
