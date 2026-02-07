"""Музыкальный плеер: поиск и воспроизведение с YouTube через yt-dlp."""

import asyncio
import re
from typing import Optional

import discord

from utils.logger import setup_logger

log = setup_logger("music")

# yt-dlp опции для аудио стриминга
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -filter:a loudnorm",
}

try:
    import yt_dlp
    YTDL_AVAILABLE = True
except ImportError:
    YTDL_AVAILABLE = False
    log.warning("yt-dlp not installed. Music disabled. pip install yt-dlp")


# ─── Паттерны для определения музыкальных запросов ─────────────────────

_MUSIC_PLAY_PATTERNS = [
    r"\b(?:включи|поставь|запусти|врубай|врубь|давай)\b",
    r"\b(?:play|put on)\b",
]

_MUSIC_STOP_PATTERNS = [
    r"\b(?:выключи|останови|выруби|убери)\s*(?:музыку|трек|песню|музон)\b",
    r"\b(?:хватит|стоп)\s*(?:музык|музон|петь)\b",
    r"\b(?:stop|pause)\s*(?:music|song|track)\b",
]

_MUSIC_SKIP_PATTERNS = [
    r"\b(?:следующ|скип|пропусти|дальше)\b",
    r"\b(?:skip|next)\b",
]

# Слова, которые указывают что это запрос музыки, а не обычная команда
_MUSIC_CONTEXT = [
    "музык", "музон", "песн", "трек", "song", "music", "track",
    "плейбой", "playboi", "carti", "карти", "моргенштерн", "morgenshtern",
    "oxxxymiron", "оксимирон", "скриптонит", "scriptonite",
    "drake", "дрейк", "kanye", "кание", "travis", "трэвис",
    "eminem", "эминем", "рэп", "rap", "рок", "rock", "поп", "pop",
]


def is_music_request(text: str) -> Optional[str]:
    """Определяет, просят ли включить музыку. Возвращает запрос или None."""
    text_lower = text.lower()
    
    # Проверяем стоп-команды
    for pattern in _MUSIC_STOP_PATTERNS:
        if re.search(pattern, text_lower):
            return "__STOP__"
    
    # Проверяем скип-команды
    for pattern in _MUSIC_SKIP_PATTERNS:
        if re.search(pattern, text_lower):
            return "__SKIP__"
    
    # Проверяем play-команды
    has_play_cmd = False
    for pattern in _MUSIC_PLAY_PATTERNS:
        if re.search(pattern, text_lower):
            has_play_cmd = True
            break
    
    if not has_play_cmd:
        return None
    
    # Есть команда "включи" — проверяем контекст
    # "включи музыку" / "включи [имя артиста]" / "включи [название песни]"
    has_music_context = any(w in text_lower for w in _MUSIC_CONTEXT)
    
    # Извлекаем что именно включить
    query = text_lower
    # Убираем команды
    for pattern in _MUSIC_PLAY_PATTERNS:
        query = re.sub(pattern, "", query)
    # Убираем обращения к боту
    for name in ["андрей", "алекс", "бот"]:
        query = re.sub(rf'\b{name}\b', '', query, flags=re.IGNORECASE)
    query = re.sub(r'[,\.\!\?]+', ' ', query)
    query = re.sub(r'\s+', ' ', query).strip()
    
    # Если осталось что-то осмысленное (>2 символов) — это запрос
    if len(query) > 2:
        return query
    
    # Если просто "включи музыку" без конкретики
    if has_music_context:
        return "popular music mix"
    
    return None


class MusicPlayer:
    """Управляет воспроизведением музыки с YouTube."""
    
    def __init__(self):
        self._voice_client: Optional[discord.VoiceClient] = None
        self._current_track: Optional[dict] = None
        self._is_playing: bool = False
    
    def set_voice_client(self, vc: Optional[discord.VoiceClient]) -> None:
        """Устанавливает voice client."""
        self._voice_client = vc
    
    @property
    def is_playing(self) -> bool:
        return self._is_playing and self._voice_client and self._voice_client.is_playing()
    
    @property
    def current_track(self) -> Optional[dict]:
        return self._current_track
    
    async def play(self, query: str) -> Optional[dict]:
        """Ищет и воспроизводит трек. Возвращает инфо о треке или None."""
        if not YTDL_AVAILABLE:
            log.warning("[MUSIC] yt-dlp not available")
            return None
        
        if not self._voice_client or not self._voice_client.is_connected():
            log.warning("[MUSIC] Not connected to voice")
            return None
        
        # Останавливаем текущее воспроизведение (музыка или TTS)
        self.stop()
        # Также убеждаемся что voice client не играет ничего (TTS мог остаться)
        if self._voice_client and self._voice_client.is_playing():
            self._voice_client.stop()
            await asyncio.sleep(0.2)
        
        # Ищем на YouTube
        log.info(f"[MUSIC] Searching: {query}")
        
        try:
            info = await asyncio.get_event_loop().run_in_executor(
                None, self._search_youtube, query
            )
        except Exception as e:
            log.error(f"[MUSIC] Search error: {e}")
            return None
        
        if not info:
            log.info(f"[MUSIC] Nothing found for: {query}")
            return None
        
        url = info.get("url")
        title = info.get("title", "Unknown")
        duration = info.get("duration", 0)
        webpage_url = info.get("webpage_url", "")
        
        log.info(f"[MUSIC] Playing: {title} ({duration}s)")
        
        # Воспроизводим через FFmpeg
        try:
            source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
            # Нормализуем громкость (музыка тише чтобы не перекрывать голос)
            source = discord.PCMVolumeTransformer(source, volume=0.3)
            
            self._current_track = {
                "title": title,
                "duration": duration,
                "url": webpage_url,
            }
            self._is_playing = True
            
            self._voice_client.play(source, after=self._after_playback)
            
            return self._current_track
            
        except Exception as e:
            log.error(f"[MUSIC] Playback error: {e}")
            self._is_playing = False
            return None
    
    def stop(self) -> None:
        """Останавливает музыку."""
        self._is_playing = False
        self._current_track = None
        if self._voice_client and self._voice_client.is_playing():
            self._voice_client.stop()
    
    def set_volume(self, volume: float) -> None:
        """Устанавливает громкость (0.0 - 1.0)."""
        if (self._voice_client and self._voice_client.source 
                and isinstance(self._voice_client.source, discord.PCMVolumeTransformer)):
            self._voice_client.source.volume = max(0.0, min(1.0, volume))
    
    def _after_playback(self, error: Optional[Exception]) -> None:
        if error:
            log.error(f"[MUSIC] Playback error: {error}")
        self._is_playing = False
        self._current_track = None
        log.info("[MUSIC] Track finished")
    
    def _search_youtube(self, query: str) -> Optional[dict]:
        """Синхронный поиск на YouTube через yt-dlp."""
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch:{query}", download=False)
                if info and "entries" in info and info["entries"]:
                    return info["entries"][0]
                return info
            except Exception as e:
                log.error(f"[MUSIC] yt-dlp error: {e}")
                return None
