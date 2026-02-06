import asyncio
import base64
import io
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Callable

from PIL import Image

from utils.logger import setup_logger

log = setup_logger("screen_capture")


class ScreenCapture:
    """Захватывает кадры из демонстрации экрана Discord.
    
    Использует FFmpeg для декодирования видеопотока из Discord.
    Периодически делает скриншоты и отправляет их на анализ.
    """

    def __init__(
        self,
        interval: int = 5,
        on_frame_ready: Optional[Callable[[str], None]] = None,
        max_resolution: tuple[int, int] = (640, 360),
    ):
        self.interval = interval
        self.on_frame_ready = on_frame_ready
        self.max_resolution = max_resolution
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        self._last_frame: Optional[str] = None  # base64
        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._stream_url: Optional[str] = None

    def start(self, stream_url: Optional[str] = None) -> None:
        """Запускает периодический захват кадров.
        
        Args:
            stream_url: URL видеопотока (если доступен напрямую)
        """
        self._stream_url = stream_url
        self._is_running = True
        self._task = asyncio.get_event_loop().create_task(self._capture_loop())
        log.info(f"Screen capture started (interval={self.interval}s)")

    def stop(self) -> None:
        """Останавливает захват."""
        self._is_running = False
        if self._task:
            self._task.cancel()
        self._stop_ffmpeg()
        log.info("Screen capture stopped")

    async def _capture_loop(self) -> None:
        """Основной цикл захвата кадров."""
        while self._is_running:
            try:
                frame_b64 = await self._capture_frame()
                if frame_b64:
                    self._last_frame = frame_b64
                    if self.on_frame_ready:
                        self.on_frame_ready(frame_b64)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Frame capture error: {e}")

            await asyncio.sleep(self.interval)

    async def _capture_frame(self) -> Optional[str]:
        """Захватывает один кадр из видеопотока.
        
        Returns:
            Base64-encoded JPEG изображение или None
        """
        if not self._stream_url:
            return None

        try:
            # Используем FFmpeg для захвата одного кадра
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._ffmpeg_grab_frame
            )
            return result
        except Exception as e:
            log.error(f"FFmpeg capture error: {e}")
            return None

    def _ffmpeg_grab_frame(self) -> Optional[str]:
        """Захватывает один кадр через FFmpeg (синхронно)."""
        if not self._stream_url:
            return None

        try:
            cmd = [
                "ffmpeg",
                "-i", self._stream_url,
                "-vframes", "1",
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-q:v", "5",
                "-vf", f"scale={self.max_resolution[0]}:{self.max_resolution[1]}",
                "-y",
                "pipe:1",
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=10,
            )

            if proc.returncode == 0 and proc.stdout:
                return base64.b64encode(proc.stdout).decode("utf-8")

        except subprocess.TimeoutExpired:
            log.warning("FFmpeg frame capture timed out")
        except FileNotFoundError:
            log.error("FFmpeg not found! Please install FFmpeg.")
        except Exception as e:
            log.error(f"FFmpeg error: {e}")

        return None

    def capture_from_image(self, image_data: bytes) -> Optional[str]:
        """Конвертирует сырые данные изображения в base64 JPEG.
        
        Полезно когда кадр получен другим способом (например, через API).
        """
        try:
            img = Image.open(io.BytesIO(image_data))
            img.thumbnail(self.max_resolution, Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=60)
            return base64.b64encode(buffer.getvalue()).decode("utf-8")
        except Exception as e:
            log.error(f"Image conversion error: {e}")
            return None

    def _stop_ffmpeg(self) -> None:
        """Останавливает FFmpeg процесс."""
        if self._ffmpeg_process:
            try:
                self._ffmpeg_process.terminate()
                self._ffmpeg_process.wait(timeout=5)
            except Exception:
                self._ffmpeg_process.kill()
            self._ffmpeg_process = None

    @property
    def last_frame(self) -> Optional[str]:
        """Последний захваченный кадр в base64."""
        return self._last_frame

    @property
    def is_running(self) -> bool:
        return self._is_running
