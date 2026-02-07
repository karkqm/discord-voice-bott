import threading
import time
import queue
from typing import Optional, Callable

import numpy as np
import torch

from utils.logger import setup_logger

log = setup_logger("tts_engine")

# Silero TTS sample rate
SILERO_SAMPLE_RATE = 48000


class TTSEngine:
    """TTS через Silero v4 — локальный, быстрый, CUDA.
    
    Использует silero_tts для скачивания модели, затем вызывает модель напрямую
    (без файлового I/O) для максимальной скорости.
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
            
            # Подавляем loguru спам от silero_tts
            try:
                from loguru import logger as loguru_logger
                loguru_logger.disable("silero_tts")
            except ImportError:
                pass
            
            # Используем silero_tts для скачивания модели, затем извлекаем torch модель
            from silero_tts.silero_tts import SileroTTS
            
            silero = SileroTTS(
                model_id='v4_ru',
                language='ru',
                speaker=self.voice,
                sample_rate=SILERO_SAMPLE_RATE,
                device=self._device,
            )
            
            # Извлекаем низкоуровневую torch модель для прямого вызова (без файлового I/O)
            self._model = silero.model
            if self._model is None:
                raise RuntimeError("Failed to extract torch model from SileroTTS")
            
            log.info(f"Silero TTS model extracted: type={type(self._model).__name__}, device={self._device}")
            
            # Прогрев модели (3 раза для стабильности CUDA)
            log.info("Warming up TTS model...")
            for _ in range(3):
                self._synth_direct("Привет.")
            
            self._ready = True
            log.info(f"TTS engine ready (Silero v4, voice={self.voice}, device={self._device})")
            
            # Один worker поток для синтеза (модель на GPU не параллелится)
            self._worker_thread = threading.Thread(target=self._synthesis_worker, daemon=True)
            self._worker_thread.start()
            
        except Exception as e:
            log.error(f"Failed to start TTS engine: {e}", exc_info=True)

    def _synth_direct(self, text: str) -> Optional[bytes]:
        """Прямой синтез через torch модель (без файлового I/O)."""
        if not self._model:
            return None
        try:
            # Silero v4 model: save_wav / apply_tts возвращает tensor
            audio = self._model.apply_tts(
                text=text,
                speaker=self.voice,
                sample_rate=SILERO_SAMPLE_RATE,
            )
            # audio — torch.Tensor float32 [-1, 1]
            if isinstance(audio, torch.Tensor):
                audio_np = audio.cpu().numpy()
            else:
                audio_np = np.array(audio, dtype=np.float32)
            
            # float32 -> int16 PCM
            pcm_int16 = (audio_np * 32767).astype(np.int16)
            return pcm_int16.tobytes()
        except Exception as e:
            log.error(f"Direct TTS synth error: {e}", exc_info=True)
            return None

    def _synthesis_worker(self) -> None:
        """Фоновый поток: берёт текст из очереди, синтезирует, отдаёт PCM."""
        while not self._stopped:
            try:
                text = self._text_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            
            if self._stopped:
                break
                
            try:
                self._is_speaking = True
                t0 = time.time()
                
                pcm_bytes = self._synth_direct(text)
                
                elapsed = (time.time() - t0) * 1000
                
                if pcm_bytes:
                    pcm_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
                    duration = len(pcm_int16) / SILERO_SAMPLE_RATE
                    log.info(f"({elapsed:.0f}ms) {text[:50]}")
                    
                    if self.on_audio_chunk and not self._stopped:
                        self.on_audio_chunk(pcm_bytes, SILERO_SAMPLE_RATE)
                    
            except Exception as e:
                log.error(f"TTS synthesis error: {e}", exc_info=True)
            finally:
                self._is_speaking = False
                self._text_queue.task_done()

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
        """Останавливает синтез и очищает очередь."""
        while not self._text_queue.empty():
            try:
                self._text_queue.get_nowait()
                self._text_queue.task_done()
            except queue.Empty:
                break
        self._is_speaking = False

    def shutdown(self) -> None:
        """Полностью останавливает движок."""
        self._stopped = True
        self.stop()
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
