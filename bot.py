import asyncio
import os
import re
import threading
import time
from typing import Optional

import discord
from discord.ext import commands

from config import Config
from modules.voice_receiver import RealtimeSink
if Config().STT_BACKEND == "realtime":
    from modules.stt_engine_v3 import STTEngine
else:
    from modules.stt_engine import STTEngine
from modules.llm_engine import LLMEngine
from modules.tts_engine import TTSEngine
from modules.voice_player import VoicePlayer
from modules.screen_capture import ScreenCapture
from modules.conversation import Conversation
from modules import web_search
from modules.music_player import MusicPlayer, is_music_request
from utils.logger import suppress_noisy_loggers
suppress_noisy_loggers()

# MinecraftBot is optional (requires Node.js + javascript pip package)
try:
    from modules.minecraft_bot import MinecraftBot
    MINECRAFT_AVAILABLE = True
except ImportError:
    MinecraftBot = None
    MINECRAFT_AVAILABLE = False

from utils.logger import setup_logger, BOLD, RESET, GRAY, GREEN, YELLOW, RED, CYAN

log = setup_logger("bot")

config = Config()

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Module Instances ---
conversation = Conversation(config)
llm_engine = LLMEngine(config)
tts_engine = TTSEngine(
    engine=config.TTS_ENGINE,
    voice=config.TTS_VOICE,
    kokoro_voice=config.KOKORO_VOICE,
    on_audio_chunk=lambda chunk, rate: asyncio.run_coroutine_threadsafe(
        voice_player.play_stream_chunk(chunk, rate), 
        bot.loop
    )
)
voice_player = VoicePlayer()
screen_capture = ScreenCapture(
    interval=config.SCREEN_CAPTURE_INTERVAL,
)
minecraft_bot = MinecraftBot(bot_name=config.BOT_NAME) if MINECRAFT_AVAILABLE else None
music_player = MusicPlayer()

# STT создаётся позже, т.к. нужны колбэки
stt_engine: Optional[STTEngine] = None

# Генерация: одна активная задача + флаг "есть новое обращение"
_generation_task: Optional[asyncio.Task] = None
_pending: bool = False
_shutup: bool = False

# ID голосового канала для автоматического подключения
AUTO_JOIN_CHANNEL_ID: int = int(os.getenv("AUTO_JOIN_CHANNEL_ID", "0") or "0")

# Текстовый канал для отправки ссылок (устанавливается при подключении)
_text_channel: Optional[discord.TextChannel] = None

# Пасхалка: Стендофф мем (состояние: 0=ждём, 1=сказали "идём в стендофф", 2=сказали "айди диктуй", 3=сказали "кто")
_standoff_state: int = 0
_standoff_timer: float = 0.0

# Команды "заткнись" — только прямые команды боту
_SHUTUP_PATTERNS = [
    r"\bзаткнись\b", r"\bзаткнитесь\b",
    r"\bпомолчи\b", r"\bзамолчи\b", r"\bзамолкни\b",
    r"\bзакрой рот\b", r"\bзавали\b",
    r"\bshut up\b", r"\bstfu\b",
]


# --- Callbacks ---

def on_stt_text_ready(text: str, user_id: int) -> None:
    """Колбэк: STT распознал финальный текст от пользователя."""
    asyncio.run_coroutine_threadsafe(
        handle_user_speech(text, user_id),
        bot.loop,
    )


def _is_shutup_command(text: str) -> bool:
    """Проверяет, просят ли бота замолчать."""
    text_lower = text.lower().strip()
    for pattern in _SHUTUP_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def on_voice_audio_chunk(audio_data: bytes, user_id: int) -> None:
    """Колбэк: аудио-чанк от пользователя в реальном времени — подаём в STT."""
    if stt_engine:
        stt_engine.feed_audio(audio_data, user_id)


def on_screen_frame_ready(frame_b64: str) -> None:
    """Колбэк: новый кадр экрана захвачен."""
    if conversation.should_comment_screen():
        asyncio.run_coroutine_threadsafe(
            handle_screen_comment(frame_b64),
            bot.loop,
        )


async def handle_user_speech(text: str, user_id: int) -> None:
    """Обрабатывает распознанную речь пользователя."""
    global _generation_task, _pending, _shutup
    
    try:
        user_name = f"User_{user_id}"
        for guild in bot.guilds:
            member = guild.get_member(user_id)
            if member:
                user_name = member.display_name
                break

        # Проверяем команду "заткнись" — останавливаем TTS/плеер (не HTTP!)
        if _is_shutup_command(text):
            log.info(f"{RED}🤐 {user_name}: {text} — замолкаю!{RESET}")
            conversation.add_user_message(text, user_name)
            _shutup = True
            tts_engine.stop()
            voice_player.stop()
            music_player.stop()
            return

        # Проверяем, обращаются ли к боту
        is_addressed = conversation.is_addressed_to_bot(text)
        
        # ВАЖНО: Всегда добавляем в историю, чтобы бот помнил контекст
        conversation.add_user_message(text, user_name)

        if not is_addressed:
            # Может бот сам хочет встрять?
            if conversation.should_auto_interject():
                log.info(f"{CYAN}[ВСТРЕВАЮ] {user_name}: {text}{RESET}")
                is_addressed = True
            else:
                log.info(f"{GRAY}{user_name}: {text}{RESET}")
                return

        log.info(f"{BOLD}{user_name}{RESET}: {text}")

        # === Пасхалка: Стендофф мем ===
        standoff_resp = _check_standoff_easter_egg(text)
        if standoff_resp:
            tts_engine.stop()
            voice_player.stop()
            for line in standoff_resp:
                tts_engine.feed(line)
            voice_player.mark_done()
            conversation.add_bot_message(" ".join(standoff_resp))
            return

        # === Музыка: перехватываем до LLM ===
        music_req = is_music_request(text)
        if music_req:
            await _handle_music_request(music_req, text)
            return

        # === Ссылки: "скинь ссылку на ..." ===
        if _is_link_request(text):
            asyncio.create_task(_handle_link_request(text))
            # Не return — пусть LLM тоже ответит голосом

        # Если бот сейчас генерирует — просто ставим флаг, ответит когда закончит
        if _generation_task and not _generation_task.done():
            _pending = True
            log.info(f"{YELLOW}[ОЖИДАНИЕ] Бот занят, отвечу после{RESET}")
            return
        
        # Бот свободен — отвечаем
        _pending = False
        _shutup = False
        _generation_task = asyncio.create_task(_generation_worker())
            
    except Exception as e:
        log.error(f"[PIPELINE] handle_user_speech error: {e}", exc_info=True)


def _check_standoff_easter_egg(text: str) -> Optional[list[str]]:
    """Пасхалка: мем про Стендофф 2. Возвращает список фраз для TTS или None."""
    global _standoff_state, _standoff_timer
    
    text_lower = text.lower().strip()
    now = time.time()
    
    # Сброс если прошло больше 30 секунд с последнего шага
    if _standoff_state > 0 and (now - _standoff_timer) > 30:
        _standoff_state = 0
    
    # Шаг 1: "мы в стендофф идём" / "идём в стендофф" / "пошли в стендофф"
    if _standoff_state == 0:
        standoff_triggers = ["стендофф", "стэндофф", "стандофф", "standoff", "stand off"]
        go_triggers = ["идём", "идем", "пошли", "погнали", "пойдём", "пойдем", "го в"]
        has_standoff = any(t in text_lower for t in standoff_triggers)
        has_go = any(t in text_lower for t in go_triggers)
        if has_standoff and has_go:
            _standoff_state = 1
            _standoff_timer = now
            log.info(f"{CYAN}[EASTER EGG] Standoff meme: stage 1{RESET}")
            return ["ПОГНАЛИ!"]
    
    # Шаг 2: "айди диктуй" / "диктуй айди" / "ник скажи"
    elif _standoff_state == 1:
        if any(t in text_lower for t in ["айди", "ник ", "никнейм", "диктуй", "скажи ник", "как тебя"]):
            _standoff_state = 2
            _standoff_timer = now
            log.info(f"{CYAN}[EASTER EGG] Standoff meme: stage 2{RESET}")
            return ["ДАНИЛ КОЛБАСЕНКО!"]
    
    # Шаг 3: "кто?!" / "кто" / "чё"
    elif _standoff_state == 2:
        if any(t in text_lower for t in ["кто", "чё", "че ", "что", "какой"]):
            _standoff_state = 3
            _standoff_timer = now
            log.info(f"{CYAN}[EASTER EGG] Standoff meme: stage 3{RESET}")
            return ["ДАНИЛ КОЛБАСЕНКО!"]
    
    # Шаг 4: "я те щас дам нахуй" / "клянись"
    elif _standoff_state == 3:
        if any(t in text_lower for t in ["дам нахуй", "клянись", "клянусь", "я тебе", "я те "]):
            _standoff_state = 0  # Сброс — мем завершён
            log.info(f"{CYAN}[EASTER EGG] Standoff meme: COMPLETE!{RESET}")
            return ["Я ТЕ ЩАС ДАМ НАХУЙ!", "ТЫ ЧЕ КЛЯНИСЬ!"]
    
    return None


async def _handle_music_request(music_req: str, original_text: str) -> None:
    """Обрабатывает запрос на музыку."""
    if music_req == "__STOP__":
        music_player.stop()
        log.info(f"{CYAN}[MUSIC] Stopped{RESET}")
        tts_engine.feed("Окей, выключаю музыку.")
        voice_player.mark_done()
        return
    
    if music_req == "__SKIP__":
        music_player.stop()
        log.info(f"{CYAN}[MUSIC] Skipped{RESET}")
        tts_engine.feed("Пропускаю.")
        voice_player.mark_done()
        return
    
    # Останавливаем TTS чтобы не мешал
    tts_engine.stop()
    voice_player.stop()
    
    log.info(f"{CYAN}[MUSIC] Request: {music_req}{RESET}")
    tts_engine.feed(f"Ищу {music_req}...")
    voice_player.mark_done()
    
    # Ждём пока TTS "Ищу..." доиграет, потом останавливаем аудио
    await asyncio.sleep(2.5)
    tts_engine.stop()
    voice_player.stop()
    
    # Передаём voice client музыкальному плееру
    music_player.set_voice_client(voice_player.voice_client)
    
    track = await music_player.play(music_req)
    if track:
        title = track.get("title", "Unknown")
        log.info(f"{GREEN}[MUSIC] Now playing: {title}{RESET}")
        # Отправляем ссылку в текстовый чат
        if _text_channel and track.get("url"):
            try:
                await _text_channel.send(f"🎵 **Сейчас играет:** {title}\n{track['url']}")
            except Exception:
                pass
    else:
        log.info(f"{YELLOW}[MUSIC] Nothing found for: {music_req}{RESET}")
        tts_engine.feed(f"Не нашёл {music_req}, извини.")
        voice_player.mark_done()


def _is_link_request(text: str) -> bool:
    """Проверяет, просят ли скинуть ссылку."""
    text_lower = text.lower()
    return any(p in text_lower for p in [
        "скинь ссылку", "кинь ссылку", "дай ссылку", "отправь ссылку",
        "скинь линк", "кинь линк", "дай линк",
        "скинь в чат", "кинь в чат",
    ])


async def _handle_link_request(text: str) -> None:
    """Ищет ссылку и отправляет в текстовый чат."""
    if not _text_channel:
        log.warning("[LINK] No text channel set")
        return
    
    query = text.lower()
    for word in ["скинь", "кинь", "дай", "отправь", "ссылку", "линк", "в чат", "на", "андрей", "алекс", "бот"]:
        query = query.replace(word, "")
    query = re.sub(r'\s+', ' ', query).strip()
    
    if len(query) < 3:
        return
    
    log.info(f"{CYAN}[LINK] Searching for: {query}{RESET}")
    
    from modules.web_search import _rewrite_query_llm, _ddg_search
    
    rewritten = await _rewrite_query_llm(query)
    search_query = rewritten or query
    
    results_raw = await _ddg_search(search_query, 3)
    
    if results_raw:
        lines = []
        for r in results_raw[:3]:
            title = r.get("title", "")
            href = r.get("href", "")
            if href:
                lines.append(f"**{title}**\n{href}")
        
        if lines:
            msg = f"🔗 Вот что нашёл по запросу **{query}**:\n\n" + "\n\n".join(lines)
            try:
                await _text_channel.send(msg)
                log.info(f"{GREEN}[LINK] Sent {len(lines)} links to chat{RESET}")
            except Exception as e:
                log.error(f"[LINK] Failed to send: {e}")
    else:
        try:
            await _text_channel.send(f"Не нашёл ссылок по запросу: {query}")
        except Exception:
            pass


async def _auto_join_voice() -> None:
    """Автоматически подключается к голосовому каналу."""
    global _text_channel
    
    channel_id = AUTO_JOIN_CHANNEL_ID
    if not channel_id:
        return
    
    channel = bot.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.VoiceChannel):
        log.warning(f"[AUTO-JOIN] Channel {channel_id} not found or not a voice channel")
        return
    
    log.info(f"[AUTO-JOIN] Connecting to: {channel.name}")
    await voice_player.connect(channel)
    
    # Начинаем слушать голос
    vc = voice_player.voice_client
    if vc:
        sink = RealtimeSink(on_audio_chunk=on_voice_audio_chunk, bot_user_id=bot.user.id)
        vc.start_recording(sink, _on_recording_done, None)
        log.info("Started realtime audio recording")
    
    # Находим текстовый канал в том же сервере
    if channel.guild:
        for tc in channel.guild.text_channels:
            if tc.permissions_for(channel.guild.me).send_messages:
                _text_channel = tc
                log.info(f"[AUTO-JOIN] Text channel: {tc.name}")
                break
    
    log.info(f"Joined voice channel: {channel.name}")


async def _generation_worker() -> None:
    """Генерирует ответ. После завершения проверяет _pending и отвечает ещё раз."""
    global _pending, _shutup
    
    while True:
        _pending = False
        _shutup = False
        try:
            await generate_and_speak(
                include_screen=screen_capture.last_frame is not None,
                include_minecraft=minecraft_bot.is_running if minecraft_bot else False
            )
        except Exception as e:
            log.error(f"[PIPELINE] generate error: {e}")
        
        # Если пока генерировали пришло новое обращение — отвечаем на него
        if _pending:
            log.info(f"{CYAN}[ОЧЕРЕДЬ] Отвечаю на новое обращение...{RESET}")
            continue
        
        break


async def handle_screen_comment(frame_b64: str) -> None:
    """Генерирует комментарий к экрану."""
    await generate_and_speak(
        image_base64=frame_b64,
        include_screen=True,
        include_minecraft=minecraft_bot.is_running if minecraft_bot else False
    )


async def generate_and_speak(
    image_base64: Optional[str] = None,
    include_screen: bool = False,
    include_minecraft: bool = False,
) -> None:
    """Генерирует ответ LLM стримом и озвучивает каждое предложение сразу."""
    try:
        pipeline_start = time.time()
        log.debug(f"[GEN] Pipeline start")
        
        # Останавливаем музыку если играет — бот хочет говорить
        if music_player.is_playing:
            music_player.stop()
            log.info(f"{CYAN}[MUSIC] Stopped for TTS{RESET}")
        
        mc_context = minecraft_bot.get_status_info() if (include_minecraft and minecraft_bot) else None
        messages = conversation.get_messages(
            include_screen=include_screen,
            minecraft_context=mc_context
        )

        # Веб-поиск: проверяем последнее сообщение пользователя
        last_user_text = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                last_user_text = msg["content"]
                break
        
        search_query = web_search.needs_search(last_user_text)
        if search_query:
            log.info(f"{CYAN}[SEARCH] {search_query}{RESET}")
            search_results = await web_search.search(search_query)
            if search_results:
                # Вставляем результаты ПЕРЕД последним сообщением пользователя,
                # чтобы LLM воспринял их как свои знания, а не проигнорировал
                search_msg = {
                    "role": "user",
                    "content": (
                        f"[СИСТЕМА: Ты загуглил и нашёл следующую информацию]:\n{search_results}\n\n"
                        "Используй ЭТУ информацию чтобы ответить на следующий вопрос. "
                        "НЕ говори что не знаешь или не играешь. Перескажи найденное коротко."
                    ),
                }
                # Находим позицию последнего user сообщения и вставляем перед ним
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i]["role"] == "user":
                        messages.insert(i, search_msg)
                        break
                else:
                    messages.append(search_msg)
                log.info(f"{GREEN}[SEARCH] Results injected before last user msg{RESET}")
            else:
                log.info(f"{YELLOW}[SEARCH] No results{RESET}")

        screen_frame = image_base64 or (
            screen_capture.last_frame if include_screen else None
        )

        full_response = ""
        first_sentence = True
        sentence_count = 0
        t_llm_start = time.time()
        t_first_sentence = 0.0
        
        log.debug(f"[GEN] Calling LLM ({len(messages)} messages)")

        _name_prefixes = [
            f"{config.BOT_NAME}:", f"[{config.BOT_NAME}]",
            f"{config.BOT_NAME} говорит:", f"{config.BOT_NAME} отвечает:",
        ]
        _name_prefixes_lower = [p.lower() for p in _name_prefixes]

        async for sentence in llm_engine.generate_stream(messages, screen_frame):
            # Если попросили заткнуться — прекращаем озвучку (но LLM стрим дочитываем)
            if _shutup:
                log.debug("[GEN] Shutup flag — skipping TTS")
                continue

            # Убираем имя бота из начала ответа (LLM иногда пишет "Андрей: ...")
            s_lower = sentence.lstrip().lower()
            for prefix in _name_prefixes_lower:
                if s_lower.startswith(prefix):
                    sentence = sentence.lstrip()[len(prefix):].lstrip()
                    break
            if not sentence.strip():
                continue
                
            # Проверяем на наличие команд Minecraft
            if "[MC:" in sentence and minecraft_bot:
                # Попытка выполнить команду и вырезать её из речи
                import re
                try:
                    # Ищем все команды
                    commands = re.findall(r"\[MC: (.*?)\]", sentence)
                    for cmd in commands:
                        log.info(f"[MC-Command] Executing: {cmd}")
                        parts = cmd.split(" ", 1)
                        action = parts[0].lower()
                        args = parts[1] if len(parts) > 1 else ""
                        
                        if action == "chat":
                            minecraft_bot.chat(args.strip('"'))
                        elif action == "goto":
                            try:
                                coords = [float(c) for c in args.split()]
                                if len(coords) == 3:
                                    minecraft_bot.move_to(*coords)
                            except ValueError:
                                log.error(f"[MC-Command] Invalid coords: {args}")
                        elif action == "follow":
                            minecraft_bot.follow_player(args.strip('"'))
                        elif action == "stop":
                            minecraft_bot.stop_moving()
                        elif action == "mine":
                            # [MC: mine "oak_log" 5]
                            parts_args = args.split('"')
                            if len(parts_args) >= 3:
                                blk = parts_args[1]
                                count = 1
                                try:
                                    count = int(parts_args[2].strip())
                                except: pass
                                minecraft_bot.mine_block(blk, count)
                        elif action == "attack":
                            # [MC: attack "zombie"]
                            minecraft_bot.attack_entity(args.strip('"'))
                        elif action == "equip":
                            # [MC: equip "sword"]
                            minecraft_bot.equip_item(args.strip('"'))
                        elif action == "inventory":
                            # Force status update or say inventory
                            inv = minecraft_bot.get_inventory()
                            log.info(f"Inventory check: {inv}")
                    
                    # Удаляем команды из текста для TTS
                    text_for_tts = re.sub(r"\[MC: .*?\]", "", sentence).strip()
                    if not text_for_tts:
                        continue # Если только команда, не озвучиваем
                    
                    sentence = text_for_tts
                except Exception as mc_err:
                    log.error(f"[MC-Command] Error: {mc_err}")

            full_response += " " + sentence
            sentence_count += 1
            if first_sentence:
                t_first_sentence = time.time()
                first_sentence = False

            # Отправляем в TTS (стриминг начнётся через колбэк)
            tts_engine.feed(sentence)

        if full_response.strip():
            conversation.add_bot_message(full_response.strip())
        
        # Сигнализируем плееру что данных больше не будет (не блокируем — TTS доиграет сам)
        voice_player.mark_done()
        
        total_time = time.time() - pipeline_start
        llm_ms = (t_first_sentence - t_llm_start) * 1000 if t_first_sentence else 0
        resp_preview = full_response.strip()[:60]
        log.info(
            f"{GREEN}>>> {resp_preview}{RESET}  "
            f"{GRAY}[LLM {llm_ms:.0f}ms | {sentence_count} frags | total {total_time:.1f}s]{RESET}"
        )

    except Exception as e:
        log.error(f"[PIPELINE] generate_and_speak error: {e}", exc_info=True)


# --- Discord Events ---

@bot.event
async def on_ready():
    log.info(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    log.info(f"Connected to {len(bot.guilds)} guild(s)")

    global stt_engine

    # LLM — лёгкий, можно синхронно
    llm_engine.start()

    # Web search — передаём LLM credentials для переформулировки запросов
    web_search.init(
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL or "https://api.openai.com/v1",
        model=config.LLM_MODEL,
    )

    # TTS и STT — тяжёлые, запускаем в фоне (не блокируют event loop)
    tts_engine.start()

    stt_kwargs = {
        "model": config.STT_MODEL,
        "language": config.STT_LANGUAGE,
        "on_text_ready": on_stt_text_ready,
        "gpu_backend": config.GPU_BACKEND,
    }
    
    stt_engine = STTEngine(**stt_kwargs)
    stt_engine.start()

    log.info("Bot ready! TTS and STT loading in background...")

    # Автоматическое подключение к голосовому каналу
    if AUTO_JOIN_CHANNEL_ID:
        await asyncio.sleep(2)  # Даём время на загрузку TTS/STT
        await _auto_join_voice()


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Автономный режим: бот сам заходит/выходит из голосовых каналов."""
    global _text_channel

    # Игнорируем самого себя
    if member == bot.user:
        return

    vc = voice_player.voice_client
    bot_channel = vc.channel if vc and vc.is_connected() else None

    # Пользователь зашёл в голосовой канал
    if after.channel and after.channel != before.channel:
        # Если бот ещё нигде не сидит — подключаемся к тому же каналу
        if not bot_channel:
            log.info(f"[AUTO-JOIN] {member.display_name} joined {after.channel.name} — connecting")
            await voice_player.connect(after.channel)
            vc = voice_player.voice_client
            if vc:
                sink = RealtimeSink(on_audio_chunk=on_voice_audio_chunk, bot_user_id=bot.user.id)
                vc.start_recording(sink, _on_recording_done, None)
                log.info("Started realtime audio recording")
            # Ищем текстовый канал в том же сервере
            for tc in after.channel.guild.text_channels:
                if tc.permissions_for(after.channel.guild.me).send_messages:
                    _text_channel = tc
                    break

    # Пользователь вышел из канала где сидит бот — проверяем пустоту
    if before.channel and before.channel == bot_channel:
        non_bot_members = [m for m in before.channel.members if not m.bot]
        if not non_bot_members:
            log.info(f"[AUTO-LEAVE] Channel {before.channel.name} is empty — leaving")
            vc2 = voice_player.voice_client
            if vc2 and vc2.recording:
                vc2.stop_recording()
            screen_capture.stop()
            await voice_player.disconnect()
            conversation.clear()


@bot.event
async def on_message(message: discord.Message):
    """Обрабатывает текстовые сообщения."""
    if message.author == bot.user:
        return

    # Обрабатываем команды
    await bot.process_commands(message)



# --- Slash / Prefix Commands ---

@bot.command(name="join", aliases=["j"])
async def join_voice(ctx: commands.Context):
    """Подключает бота к голосовому каналу."""
    if not ctx.author.voice:
        await ctx.send("Ты не в голосовом канале!")
        return

    channel = ctx.author.voice.channel
    await voice_player.connect(channel)

    # Начинаем слушать голос через кастомный RealtimeSink
    vc = voice_player.voice_client
    if vc:
        sink = RealtimeSink(on_audio_chunk=on_voice_audio_chunk, bot_user_id=bot.user.id)
        vc.start_recording(sink, _on_recording_done, ctx.channel)
        log.info("Started realtime audio recording")

    # Запоминаем текстовый канал для отправки ссылок
    global _text_channel
    _text_channel = ctx.channel

    await ctx.send(f"Подключился к **{channel.name}**! 🎤")
    log.info(f"Joined voice channel: {channel.name}")


@bot.command(name="leave", aliases=["l"])
async def leave_voice(ctx: commands.Context):
    """Отключает бота от голосового канала."""
    vc = voice_player.voice_client
    if vc and vc.recording:
        vc.stop_recording()
    screen_capture.stop()
    await voice_player.disconnect()
    conversation.clear()
    await ctx.send("Отключился! 👋")


@bot.command(name="screen", aliases=["s"])
async def toggle_screen(ctx: commands.Context):
    """Включает/выключает анализ демонстрации экрана."""
    if screen_capture.is_running:
        screen_capture.stop()
        await ctx.send("Анализ экрана выключен.")
    else:
        screen_capture.start()
        await ctx.send(
            f"Анализ экрана включён (каждые {config.SCREEN_CAPTURE_INTERVAL}с). "
            "Начни демонстрацию экрана в Discord!"
        )


@bot.command(name="clear", aliases=["c"])
async def clear_history(ctx: commands.Context):
    """Очищает историю диалога."""
    conversation.clear()
    await ctx.send("История диалога очищена! 🧹")


@bot.command(name="status")
async def show_status(ctx: commands.Context):
    """Показывает статус бота."""
    status_lines = [
        f"**{config.BOT_NAME}** — статус",
        f"• LLM: `{config.LLM_MODEL}`",
        f"• STT: RealtimeSTT (`{config.STT_MODEL}`)",
        f"• TTS: RealtimeTTS (`{config.TTS_ENGINE}` / `{config.TTS_VOICE}`)",
        f"• Голосовой канал: {'✅ подключён' if voice_player.voice_client else '❌ нет'}",
        f"• Анализ экрана: {'✅ вкл' if screen_capture.is_running else '❌ выкл'}",
        f"• История: {conversation.history_length} сообщений",
    ]
    await ctx.send("\n".join(status_lines))


@bot.command(name="mc_join")
async def mc_join(ctx: commands.Context, host: str = "localhost", port: int = 25565):
    """Подключает бота к серверу Minecraft. (Пример: !mc_join localhost 25565)"""
    if not minecraft_bot:
        await ctx.send("Minecraft бот недоступен (javascript модуль не установлен).")
        return
    try:
        minecraft_bot.connect(host, port)
        await ctx.send(f"Подключаюсь к Minecraft серверу {host}:{port}...")
    except Exception as e:
        await ctx.send(f"Ошибка подключения: {e}")


@bot.command(name="mc_leave")
async def mc_leave(ctx: commands.Context):
    """Отключает бота от сервера Minecraft."""
    if not minecraft_bot:
        await ctx.send("Minecraft бот недоступен.")
        return
    minecraft_bot.disconnect()
    await ctx.send("Бот отключен от Minecraft сервера.")


@bot.command(name="mc_status")
async def mc_status(ctx: commands.Context):
    """Статус Minecraft бота."""
    if not minecraft_bot:
        await ctx.send("Minecraft бот недоступен.")
        return
    status = minecraft_bot.get_status_info()
    await ctx.send(f"```\n{status}\n```")


async def _on_recording_done(sink, channel):
    """Колбэк: запись завершена (вызывается при stop_recording)."""
    log.info("Recording stopped")


# --- Entry Point ---

def main():
    if not config.DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set! Copy .env.example to .env and fill in your token.")
        return
    if not config.OPENAI_API_KEY:
        log.error("OPENAI_API_KEY not set! Copy .env.example to .env and fill in your key.")
        return

    log.info(f"Starting {config.BOT_NAME}...")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
