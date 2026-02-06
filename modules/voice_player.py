import asyncio
import tempfile
import os
import threading
from typing import Optional

import discord

from utils.logger import setup_logger

log = setup_logger("voice_player")


class VoicePlayer:
    """Управляет воспроизведением аудио в Discord голосовом канале."""

    def __init__(self):
        self._voice_client: Optional[discord.VoiceClient] = None
        self._is_playing = False
        self._done_event = threading.Event()
        self._tmp_path: Optional[str] = None

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """Подключается к голосовому каналу."""
        if self._voice_client and self._voice_client.is_connected():
            if self._voice_client.channel.id == channel.id:
                return self._voice_client
            await self._voice_client.disconnect()

        self._voice_client = await channel.connect()
        log.info(f"Connected to voice channel: {channel.name}")
        return self._voice_client

    async def disconnect(self) -> None:
        """Отключается от голосового канала."""
        if self._voice_client:
            self.stop()
            await self._voice_client.disconnect()
            self._voice_client = None
            log.info("Disconnected from voice channel")

    async def play_audio(self, audio_data: bytes, fmt: str = ".wav") -> None:
        """Воспроизводит аудио в Discord через FFmpeg.
        
        Args:
            audio_data: Аудио байты (WAV/MP3)
            fmt: Расширение файла (.wav или .mp3)
        """
        if not self._voice_client or not self._voice_client.is_connected():
            log.error("Not connected to voice channel")
            return

        self.stop()

        # Записываем во временный файл
        tmp_path = tempfile.mktemp(suffix=fmt)
        with open(tmp_path, 'wb') as f:
            f.write(audio_data)

        self._tmp_path = tmp_path
        self._done_event.clear()
        self._is_playing = True

        try:
            source = discord.FFmpegPCMAudio(tmp_path)
            self._voice_client.play(source, after=self._after_playback)
            log.info(f"Playing audio ({len(audio_data)} bytes)")

            # Ждём окончания в executor чтобы не блокировать event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._done_event.wait)

        except Exception as e:
            log.error(f"play_audio error: {e}", exc_info=True)
            self._is_playing = False

        # Удаляем файл после воспроизведения
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    def _after_playback(self, error: Optional[Exception]) -> None:
        """Колбэк завершения воспроизведения (вызывается из потока discord)."""
        if error:
            log.error(f"Playback error: {error}")
        self._is_playing = False
        self._done_event.set()

    def stop(self) -> None:
        """Останавливает текущее воспроизведение."""
        if self._voice_client and self._voice_client.is_playing():
            self._voice_client.stop()
        self._is_playing = False

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def voice_client(self) -> Optional[discord.VoiceClient]:
        return self._voice_client
