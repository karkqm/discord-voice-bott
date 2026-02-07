import io
import struct
import numpy as np


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 48000, channels: int = 2, sample_width: int = 2) -> bytes:
    """Конвертирует сырые PCM данные в WAV формат."""
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        sample_rate * channels * sample_width,
        channels * sample_width,
        sample_width * 8,
        b"data",
        data_size,
    )
    return header + pcm_data


def resample_audio(audio_data: bytes, from_rate: int, to_rate: int, channels: int = 1) -> bytes:
    """Простой ресемплинг аудио через линейную интерполяцию."""
    samples = np.frombuffer(audio_data, dtype=np.int16)
    if channels > 1:
        samples = samples[::channels]  # берём только левый канал

    duration = len(samples) / from_rate
    new_length = int(duration * to_rate)
    indices = np.linspace(0, len(samples) - 1, new_length)
    resampled = np.interp(indices, np.arange(len(samples)), samples.astype(np.float64))
    return resampled.astype(np.int16).tobytes()


def float32_to_int16(audio: bytes) -> bytes:
    """Конвертирует float32 аудио в int16."""
    samples = np.frombuffer(audio, dtype=np.float32)
    samples = np.clip(samples * 32767, -32768, 32767)
    return samples.astype(np.int16).tobytes()


def int16_to_float32(audio: bytes) -> bytes:
    """Конвертирует int16 аудио в float32."""
    samples = np.frombuffer(audio, dtype=np.int16)
    return (samples.astype(np.float32) / 32767.0).tobytes()


def resample_to_48k_stereo(pcm_data: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Ресемплирует аудио в 48kHz stereo 16-bit PCM (стандарт Discord)."""
    if not pcm_data:
        return b""
        
    samples = np.frombuffer(pcm_data, dtype=np.int16)
    
    # 1. Ресемплинг если нужно
    if sample_rate != 48000:
        duration = len(samples) / sample_rate
        new_length = int(duration * 48000)
        # Простая линейная интерполяция (быстро)
        indices = np.linspace(0, len(samples) - 1, new_length)
        samples = np.interp(indices, np.arange(len(samples)), samples)
        
    # 2. Конвертация в стерео если моно
    if channels == 1:
        # Дублируем для стерео
        # [[L, R], [L, R]...] -> flatten
        samples = np.repeat(samples, 2)
        
    return samples.astype(np.int16).tobytes()
