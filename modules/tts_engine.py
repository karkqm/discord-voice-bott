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
# Kokoro TTS sample rate
KOKORO_SAMPLE_RATE = 24000


class TTSEngine:
    """TTS движок. Поддерживаемые движки:
    - kokoro: Kokoro ONNX (локальный, низкая задержка, ~24kHz) — рекомендуется
    - silero: Silero v4 (локальный, CUDA, ~48kHz) — хороший fallback
    - edge: Microsoft Edge TTS (онлайн)
    """

    def __init__(
        self,
        engine: str = "silero",
        voice: str = "xenia",
        on_audio_chunk: Optional[Callable[[bytes, int], None]] = None,
        kokoro_voice: str = "af_heart",
    ):
        self.engine_name = engine
        self.voice = voice
        self.kokoro_voice = kokoro_voice
        self.on_audio_chunk = on_audio_chunk
        self._model = None
        self._silero_pkg = None
        self._kokoro = None
        self._ready = False
        self._is_speaking = False
        self._stopped = False
        self._device = "cpu"
        self._sample_rate = SILERO_SAMPLE_RATE
        
        # Очередь текста для синтеза
        self._text_queue: queue.Queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Запускает инициализацию TTS в фоновом потоке."""
        self._stopped = False
        if self.engine_name == "kokoro":
            self._init_thread = threading.Thread(target=self._init_kokoro, daemon=True)
        else:
            self._init_thread = threading.Thread(target=self._init_engine, daemon=True)
        self._init_thread.start()
        log.info(f"TTS engine ({self.engine_name}) initialization started in background...")

    def _init_kokoro(self) -> None:
        """Инициализация Kokoro ONNX TTS."""
        try:
            from kokoro_onnx import Kokoro
            log.info(f"Loading Kokoro TTS (voice={self.kokoro_voice})...")
            self._kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
            self._sample_rate = KOKORO_SAMPLE_RATE
            # Прогрев
            samples, _ = self._kokoro.create("Привет.", voice=self.kokoro_voice, speed=1.0, lang="ru")
            log.info(f"Kokoro TTS ready (voice={self.kokoro_voice}, {self._sample_rate}Hz)")
            self._ready = True
            self._worker_thread = threading.Thread(target=self._synthesis_worker, daemon=True)
            self._worker_thread.start()
        except ImportError:
            log.error("kokoro-onnx not installed! Run: pip install kokoro-onnx")
            log.warning("Falling back to Silero TTS...")
            self.engine_name = "silero"
            self._init_engine()
        except Exception as e:
            log.error(f"Failed to start Kokoro TTS: {e}", exc_info=True)
            log.warning("Falling back to Silero TTS...")
            self.engine_name = "silero"
            self._init_engine()

    def _synthesize_kokoro(self, text: str) -> Optional[bytes]:
        """Синтез через Kokoro ONNX → PCM int16."""
        if not self._kokoro:
            return None
        clean = text.strip().rstrip('.,!?;:…—')
        if len(clean) < 2:
            return None
        try:
            samples, sample_rate = self._kokoro.create(
                text, voice=self.kokoro_voice, speed=1.0, lang="ru"
            )
            # samples — float32 array [-1, 1], конвертируем в int16
            pcm = (samples * 32767).astype(np.int16)
            return pcm.tobytes()
        except Exception as e:
            log.error(f"Kokoro synth error: {e}", exc_info=True)
            return None

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
            
            log.info(f"Loading Silero TTS v4 on {self._device}...")
            
            # Подавляем loguru спам
            try:
                from loguru import logger as loguru_logger
                loguru_logger.disable("silero_tts")
                loguru_logger.disable("")
            except ImportError:
                pass
            
            # Загрузка модели через torch.hub (save_wav метод)
            try:
                model, example = torch.hub.load(
                    repo_or_dir='snakers4/silero-models',
                    model='silero_tts',
                    language='ru',
                    speaker='v4_ru',
                    trust_repo=True,
                )
                if model is not None and hasattr(model, 'save_wav'):
                    self._model = model
                    # Перемещаем на GPU если нужно
                    if self._device == "cuda":
                        try:
                            self._model = model.to(torch.device('cuda')) or model
                        except Exception:
                            pass
                    log.info(f"Loaded via torch.hub (type={type(model).__name__})")
                else:
                    raise RuntimeError(f"Invalid model: {type(model)}")
            except Exception as hub_err:
                log.warning(f"torch.hub failed: {hub_err}, using silero_tts package...")
                from silero_tts.silero_tts import SileroTTS
                self._silero_pkg = SileroTTS(
                    model_id='v4_ru', language='ru', speaker=self.voice,
                    sample_rate=SILERO_SAMPLE_RATE, device=self._device,
                )
                self._model = None  # используем _silero_pkg
            
            # Прогрев
            log.info("Warming up TTS...")
            for _ in range(3):
                self._synthesize_pcm("Привет.")
            
            self._ready = True
            mode = "torch.hub" if self._model else "silero_tts pkg"
            log.info(f"TTS ready (Silero v4, {self.voice}, {self._device}, {mode})")
            
            # Один worker поток
            self._worker_thread = threading.Thread(target=self._synthesis_worker, daemon=True)
            self._worker_thread.start()
            
        except Exception as e:
            log.error(f"Failed to start TTS engine: {e}", exc_info=True)

    def _synthesize_pcm(self, text: str) -> Optional[bytes]:
        """Синтезирует текст в PCM int16 bytes."""
        import tempfile, os, wave
        
        # Silero v4 падает на слишком коротком тексте (< 3 символа без пунктуации)
        clean = text.strip().rstrip('.,!?;:…—')
        if len(clean) < 2:
            log.debug(f"TTS skip too short: '{text}'")
            return None
        
        try:
            tmp_path = os.path.join(tempfile.gettempdir(), f"silero_tts_{threading.get_ident()}.wav")
            
            if self._model is not None:
                # torch.hub модель — save_wav напрямую (без silero_tts пакета)
                self._model.save_wav(
                    text=text,
                    speaker=self.voice,
                    sample_rate=SILERO_SAMPLE_RATE,
                    audio_path=tmp_path,
                )
            else:
                # silero_tts пакет
                self._silero_pkg.tts(text, tmp_path)
            
            with wave.open(tmp_path, 'rb') as wf:
                pcm_bytes = wf.readframes(wf.getnframes())
            
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            
            return pcm_bytes
        except ValueError:
            log.warning(f"TTS can't synthesize: '{text}' (too short/invalid)")
            return None
        except Exception as e:
            log.error(f"TTS synth error: {e}", exc_info=True)
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
                
                if self.engine_name == "kokoro":
                    pcm_bytes = self._synthesize_kokoro(text)
                else:
                    pcm_bytes = self._synthesize_pcm(text)
                
                elapsed = (time.time() - t0) * 1000
                
                if pcm_bytes:
                    log.info(f"({elapsed:.0f}ms) {text[:50]}")
                    
                    if self.on_audio_chunk and not self._stopped:
                        self.on_audio_chunk(pcm_bytes, self._sample_rate)
                    
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

    def wait_until_done(self, timeout: float = 30.0) -> None:
        """Блокирует до завершения всех синтезов в очереди."""
        deadline = time.time() + timeout
        while (self._is_speaking or not self._text_queue.empty()) and time.time() < deadline:
            time.sleep(0.05)

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
