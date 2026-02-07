import threading
import time
import queue
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor, Future
from collections import OrderedDict

import numpy as np
import torch

from utils.logger import setup_logger

log = setup_logger("tts_engine")

# Silero TTS sample rate
SILERO_SAMPLE_RATE = 48000

# Количество TTS worker потоков
TTS_WORKERS = 2


class TTSEngine:
    """TTS через Silero v4 — локальный, быстрый, CUDA.
    
    Синтез ~50-100ms на GPU, ~200ms на CPU для одного предложения.
    Прямой PCM выход, без MP3/pydub/mpv зависимостей.
    Пул worker потоков для параллельного синтеза.
    """

    def __init__(
        self,
        engine: str = "silero",
        voice: str = "xenia",
        on_audio_chunk: Optional[Callable[[bytes, int], None]] = None
    ):
        self.engine_name = engine
        self.voice = voice
        self.on_audio_chunk = on_audio_chunk
        self._model = None
        self._ready = False
        self._is_speaking = False
        self._stopped = False
        self._device = "cpu"
        
        # Очередь текста для синтеза
        self._text_queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        
        # Lock для доступа к модели Silero (GPU не thread-safe)
        self._model_lock = threading.Lock()
        
        # Пул потоков для параллельного синтеза
        self._executor: Optional[ThreadPoolExecutor] = None
        
        # Очередь готовых аудио для воспроизведения по порядку
        self._seq_counter = 0
        self._seq_lock = threading.Lock()
        self._playback_queue: queue.Queue = queue.Queue()  # (seq_num, pcm_bytes, text)
        self._playback_thread: Optional[threading.Thread] = None
        self._reset_event = threading.Event()  # сигнал сброса очереди

    def start(self) -> None:
        """Запускает инициализацию Silero TTS в фоновом потоке."""
        self._stopped = False
        self._init_thread = threading.Thread(target=self._init_engine, daemon=True)
        self._init_thread.start()
        log.info("TTS engine initialization started in background...")

    def _check_cuda_compatible(self) -> bool:
        """Проверяет совместимость GPU с текущей версией PyTorch."""
        if not torch.cuda.is_available():
            return False
        try:
            # Пробуем создать тензор на GPU — если sm_xxx не поддерживается, упадёт
            t = torch.zeros(1, device="cuda")
            del t
            return True
        except Exception as e:
            log.warning(f"CUDA available but incompatible: {e}")
            return False

    def _init_engine(self) -> None:
        """Загрузка Silero TTS v4 модели."""
        try:
            # Определяем устройство с проверкой совместимости
            if self._check_cuda_compatible():
                self._device = "cuda"
            else:
                self._device = "cpu"
                if torch.cuda.is_available():
                    log.warning("CUDA detected but not compatible with this PyTorch. Using CPU.")
                    log.warning("For RTX 50xx: pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128")
            
            log.info(f"Loading Silero TTS v4 on {self._device}...")
            
            from silero_tts.silero_tts import SileroTTS
            
            self._silero = SileroTTS(
                model_id='v4_ru',
                language='ru',
                speaker=self.voice,
                sample_rate=SILERO_SAMPLE_RATE,
                device=self._device,
            )
            self._model = self._silero  # for compatibility
            
            log.info(f"Silero TTS initialized: model=v4_ru, speaker={self.voice}, device={self._device}")
            
            # Прогрев модели (2 раза для стабильности CUDA)
            log.info("Warming up TTS model...")
            import tempfile, os
            warmup_path = os.path.join(tempfile.gettempdir(), "silero_warmup.wav")
            for _ in range(2):
                self._silero.tts("Привет.", warmup_path)
            try:
                os.remove(warmup_path)
            except Exception:
                pass
            
            self._ready = True
            log.info(f"TTS engine ready (Silero v4, voice={self.voice}, device={self._device})")
            
            # Запускаем dispatcher (берёт из очереди, отправляет в пул)
            self._executor = ThreadPoolExecutor(max_workers=TTS_WORKERS, thread_name_prefix="tts")
            self._worker_thread = threading.Thread(target=self._dispatch_worker, daemon=True)
            self._worker_thread.start()
            
            # Запускаем playback worker (воспроизводит аудио по порядку)
            self._playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
            self._playback_thread.start()
            
        except Exception as e:
            log.error(f"Failed to start TTS engine: {e}", exc_info=True)

    def _next_seq(self) -> int:
        """Возвращает следующий порядковый номер для очереди воспроизведения."""
        with self._seq_lock:
            self._seq_counter += 1
            return self._seq_counter

    def _dispatch_worker(self) -> None:
        """Dispatcher: берёт текст из очереди, отправляет синтез в пул потоков."""
        while not self._stopped:
            try:
                text = self._text_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            
            if self._stopped:
                break
            
            seq = self._next_seq()
            # Отправляем синтез в пул — он выполнится параллельно
            self._executor.submit(self._synthesize_one, text, seq)
            self._text_queue.task_done()

    def _synthesize_one(self, text: str, seq: int) -> None:
        """Синтезирует одну фразу (вызывается из пула потоков)."""
        try:
            self._is_speaking = True
            t0 = time.time()
            
            import tempfile, os, wave
            tmp_path = os.path.join(tempfile.gettempdir(), f"silero_tts_{seq}.wav")
            
            # Lock на модель — GPU Silero не thread-safe
            with self._model_lock:
                t_lock = time.time()
                self._silero.tts(text, tmp_path)
            
            t_synth = time.time()
            
            # Read WAV -> PCM int16
            with wave.open(tmp_path, 'rb') as wf:
                pcm_bytes = wf.readframes(wf.getnframes())
            
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            
            pcm_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
            duration = len(pcm_int16) / SILERO_SAMPLE_RATE
            lock_wait = (t_lock - t0) * 1000
            synth_time = (t_synth - t_lock) * 1000
            
            log.info(f"[TIMING] TTS #{seq}: {text[:40]}... (lock={lock_wait:.0f}ms, synth={synth_time:.0f}ms, audio={duration:.1f}s)")
            
            # Кладём в очередь воспроизведения
            self._playback_queue.put((seq, pcm_bytes, text))
                
        except Exception as e:
            log.error(f"TTS synthesis error: {e}", exc_info=True)
        finally:
            self._is_speaking = False

    def _playback_worker(self) -> None:
        """Воспроизводит аудио по порядку seq номеров."""
        next_seq = 1  # ожидаемый следующий номер
        pending = {}  # seq -> (pcm_bytes, text) — пришли не по порядку
        
        while not self._stopped:
            # Проверяем сброс
            if self._reset_event.is_set():
                self._reset_event.clear()
                pending.clear()
                with self._seq_lock:
                    next_seq = self._seq_counter + 1
                continue
            
            try:
                item = self._playback_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            
            if self._stopped:
                break
            
            seq, pcm_bytes, text = item
            
            # Пропускаем старые фрагменты (до сброса)
            if seq < next_seq:
                continue
            
            pending[seq] = (pcm_bytes, text)
            
            # Воспроизводим все готовые по порядку
            while next_seq in pending:
                pcm, txt = pending.pop(next_seq)
                if self.on_audio_chunk and not self._stopped:
                    self.on_audio_chunk(pcm, SILERO_SAMPLE_RATE)
                next_seq += 1

    def feed(self, text: str) -> None:
        """Подаёт текст в очередь синтеза."""
        if not self._ready:
            log.warning("TTS not ready, skipping text")
            return
        if not text.strip():
            return
        self._text_queue.put(text)
        log.debug(f"TTS queued: {text[:30]}...")

    def stop(self) -> None:
        """Останавливает синтез и очищает очереди."""
        # Очищаем очередь текста
        while not self._text_queue.empty():
            try:
                self._text_queue.get_nowait()
                self._text_queue.task_done()
            except queue.Empty:
                break
        # Очищаем очередь воспроизведения
        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
            except queue.Empty:
                break
        # Сигнализируем playback worker о сбросе
        self._reset_event.set()
        self._is_speaking = False

    def shutdown(self) -> None:
        """Полностью останавливает движок."""
        self._stopped = True
        self.stop()
        if self._executor:
            self._executor.shutdown(wait=False)
        self._ready = False
        self._model = None
        log.info("TTS engine stopped")

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking or not self._text_queue.empty()

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Legacy метод (совместимость)."""
        self.feed(text)
        return None
