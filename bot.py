import asyncio
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

# STT создаётся позже, т.к. нужны колбэки
stt_engine: Optional[STTEngine] = None

# Генерация: одна активная задача
_generation_task: Optional[asyncio.Task] = None
_generation_lock = asyncio.Lock()

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
    global _generation_task
    
    try:
        user_name = f"User_{user_id}"
        for guild in bot.guilds:
            member = guild.get_member(user_id)
            if member:
                user_name = member.display_name
                break

        # Проверяем команду "заткнись" — немедленно останавливаем всё
        if _is_shutup_command(text):
            log.info(f"{RED}🤐 {user_name}: {text} — замолкаю!{RESET}")
            conversation.add_user_message(text, user_name)
            await _stop_generation()
            return

        # Проверяем, обращаются ли к боту
        is_addressed = conversation.is_addressed_to_bot(text)
        
        # ВАЖНО: Всегда добавляем в историю, чтобы бот помнил контекст
        conversation.add_user_message(text, user_name)

        if not is_addressed:
            log.info(f"{GRAY}{user_name}: {text}{RESET}")
            return

        log.info(f"{BOLD}{user_name}{RESET}: {text}")

        # Если бот сейчас генерирует — останавливаем чисто и начинаем заново
        if _generation_task and not _generation_task.done():
            log.info(f"{YELLOW}[ПЕРЕЗАПУСК] Новое обращение, перегенерирую...{RESET}")
            await _stop_generation()
        
        # Запускаем новую генерацию
        _generation_task = asyncio.create_task(_do_generate())
            
    except Exception as e:
        log.error(f"[PIPELINE] handle_user_speech error: {e}", exc_info=True)


async def _stop_generation() -> None:
    """Чисто останавливает текущую генерацию: LLM стрим, TTS, плеер."""
    global _generation_task
    
    # 1. Отменяем LLM стрим через event (чистое закрытие HTTP)
    llm_engine.cancel()
    
    # 2. Останавливаем TTS и плеер
    tts_engine.stop()
    voice_player.stop()
    
    # 3. Ждём завершения таска (он завершится быстро т.к. LLM стрим отменён)
    if _generation_task and not _generation_task.done():
        try:
            await asyncio.wait_for(_generation_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            # Если не завершился за 2с — принудительно отменяем
            _generation_task.cancel()
            try:
                await _generation_task
            except (asyncio.CancelledError, Exception):
                pass
    _generation_task = None
    log.debug("[STOP] Generation stopped cleanly")


async def _do_generate() -> None:
    """Генерирует один ответ."""
    try:
        await generate_and_speak(
            include_screen=screen_capture.last_frame is not None,
            include_minecraft=minecraft_bot.is_running if minecraft_bot else False
        )
    except asyncio.CancelledError:
        log.debug("Generation task cancelled")
        return
    except Exception as e:
        log.error(f"[PIPELINE] generate error: {e}")


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
        
        mc_context = minecraft_bot.get_status_info() if (include_minecraft and minecraft_bot) else None
        messages = conversation.get_messages(
            include_screen=include_screen,
            minecraft_context=mc_context
        )

        screen_frame = image_base64 or (
            screen_capture.last_frame if include_screen else None
        )

        full_response = ""
        first_sentence = True
        sentence_count = 0
        t_llm_start = time.time()
        t_first_sentence = 0.0
        
        log.debug(f"[GEN] Calling LLM ({len(messages)} messages)")

        async for sentence in llm_engine.generate_stream(messages, screen_frame):
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

    # TTS и STT — тяжёлые, запускаем в фоне (не блокируют event loop)
    tts_engine.start()

    stt_kwargs = {
        "model": config.STT_MODEL,
        "language": config.STT_LANGUAGE,
        "on_text_ready": on_stt_text_ready,
    }
    
    stt_engine = STTEngine(**stt_kwargs)
    stt_engine.start()

    log.info("Bot ready! TTS and STT loading in background...")


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
