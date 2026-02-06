import io
from typing import Callable, Optional

import discord
from discord.sinks import Sink, Filters, default_filters

from utils.audio_utils import resample_audio
from utils.logger import setup_logger

log = setup_logger("voice_receiver")


class RealtimeSink(Sink):
    """Кастомный Sink для py-cord, который передаёт аудио в реальном времени.
    
    Discord отдаёт 48kHz stereo 16-bit PCM.
    Мы ресемплим в 16kHz mono и подаём чанки в колбэк.
    """

    def __init__(self, on_audio_chunk: Callable[[bytes, int], None], bot_user_id: int = 0, *, filters=None):
        super().__init__(filters=filters)
        self.on_audio_chunk = on_audio_chunk
        self.bot_user_id = bot_user_id

    @Filters.container
    def write(self, data: bytes, user: int) -> None:
        """Вызывается py-cord при получении каждого аудио-пакета."""
        # Игнорируем аудио от самого бота
        if user == self.bot_user_id:
            return

        # Сохраняем в audio_data для совместимости с базовым Sink
        if user not in self.audio_data:
            from discord.sinks import AudioData
            self.audio_data[user] = AudioData(io.BytesIO())

        try:
            # Discord: 48kHz stereo 16-bit PCM → 16kHz mono 16-bit PCM
            mono_16k = resample_audio(data, from_rate=48000, to_rate=16000, channels=2)
            self.on_audio_chunk(mono_16k, user)
        except Exception as e:
            log.error(f"Error processing audio chunk: {e}")

    def cleanup(self):
        self.finished = True
