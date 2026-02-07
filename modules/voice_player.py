import asyncio
import threading
import queue
import collections
from typing import Optional

import discord
import numpy as np

from utils.logger import setup_logger
from utils.audio_utils import resample_to_48k_stereo

log = setup_logger("voice_player")


class PCMStreamAudioSource(discord.AudioSource):
    """Источник аудио для Discord, который читает из потока (очереди)."""

    def __init__(self):
        # Очередь чанков (каждый чанк — 20мс аудио 48kHz stereo 16bit)
        # 48000 Hz * 2 channels * 2 bytes = 192,000 bytes/sec
        # 20ms = 192000 * 0.02 = 3840 bytes
        self._buffer = queue.Queue()
        self._buffering = True  # Сначала накапливаем немного
        self._min_buffer_size = 10 # ~200мс (10 * 20мс) для старта
        self._max_buffer_size = 50 # ~1 секунда
        self._finished = False
        
        # Для заполнения тишиной если данных нет, но поток жив
        self._silence = b'\x00' * 3840 

    def add_data(self, data: bytes) -> None:
        """Добавляет сырые PCM данные (48kHz stereo 16bit)."""
        # Разбиваем на чанки по 3840 байт (20мс)
        chunk_size = 3840
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            if len(chunk) < chunk_size:
                # Добиваем тишиной последний кусок
                chunk += b'\x00' * (chunk_size - len(chunk))
            self._buffer.put(chunk)

    def read(self) -> bytes:
        # Если буферизация активна, ждем накопления
        if self._buffering:
            if self._buffer.qsize() >= self._min_buffer_size or self._finished:
                self._buffering = False
                # log.debug("Buffering complete, starting playback")
            else:
                return self._silence

        if self._buffer.empty():
            if self._finished:
                return b''
            # Буфер опустел — кратковременная пауза для накопления (анти-джиттер)
            self._buffering = True
            # log.warning("Buffer underrun, buffering...")
            return self._silence

        return self._buffer.get()

    def cleanup(self) -> None:
        self._finished = True

    def mark_finished(self):
        """Помечает что данных больше не будет."""
        self._finished = True
    
    def is_opus(self) -> bool:
        return False


class VoicePlayer:
    """Управляет воспроизведением аудио в Discord через PCM поток."""

    def __init__(self):
        self._voice_client: Optional[discord.VoiceClient] = None
        self._current_source: Optional[PCMStreamAudioSource] = None
        self._lock = asyncio.Lock()

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
        self.stop()
        if self._voice_client:
            await self._voice_client.disconnect()
            self._voice_client = None
            log.info("Disconnected from voice channel")

    async def play_stream_chunk(self, audio_data: bytes, sample_rate: int = 24000) -> None:
        """Принимает кусок аудио, ресемплит и отправляет в поток."""
        if not self._voice_client or not self._voice_client.is_connected():
            return

        # Ресемплинг в 48kHz stereo
        pcm_48k = resample_to_48k_stereo(audio_data, sample_rate, channels=1)

        async with self._lock:
            # Если уже играет наш стрим — просто добавляем данные
            if self._voice_client.is_playing() and isinstance(self._voice_client.source, PCMStreamAudioSource):
                self._voice_client.source.add_data(pcm_48k)
                self._current_source = self._voice_client.source
            else:
                # Если молчит или играло что-то другое — запускаем новый стрим
                self.stop() # на всякий случай
                log.info("Starting new audio stream")
                self._current_source = PCMStreamAudioSource()
                self._current_source.add_data(pcm_48k)
                self._voice_client.play(self._current_source, after=self._after_playback)

    async def play_audio(self, audio_data: bytes) -> None:
        """Воспроизводит готовый кусок аудио (legacy wrapper)."""
        # Предполагаем что это WAV или raw PCM?
        # Для совместимости с ботом, который передает байты.
        # Если это WAV с заголовком, надо бы скипнуть заголовок.
        # Но пока просто считаем что это PCM или короткий WAV.
        # Лучше использовать play_stream_chunk.
        # Если это WAV файл целиком:
        if audio_data.startswith(b'RIFF'):
             # Пропускаем header (просто 44 байта, грубо)
             audio_data = audio_data[44:]
        
        await self.play_stream_chunk(audio_data, sample_rate=24000)

    def _after_playback(self, error: Optional[Exception]) -> None:
        if error:
            log.error(f"Playback error: {error}")
        self._current_source = None

    def stop(self) -> None:
        """Останавливает текущее воспроизведение."""
        if self._current_source:
             self._current_source.mark_finished()
        
        if self._voice_client and self._voice_client.is_playing():
            self._voice_client.stop()
            
        self._current_source = None

    @property
    def is_playing(self) -> bool:
        return self._voice_client and self._voice_client.is_playing()

    @property
    def voice_client(self) -> Optional[discord.VoiceClient]:
        return self._voice_client
