import os
import asyncio
import re
import yt_dlp
import requests
from io import BytesIO
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL = "@Lira_projects"
DOWNLOAD_DIR = "/tmp/music"
BANNER_PATH = "lira_banner.png"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ──────────────────────────────────────────────
# Проверка подписки
# ──────────────────────────────────────────────
async def is_subscribed(user_id: int, context) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

# ──────────────────────────────────────────────
# Показать экран подписки
# ──────────────────────────────────────────────
async def show_subscribe_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Перейти на канал", url=f"https://t.me/Lira_projects")],
        [InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")],
    ])
    with open(BANNER_PATH, "rb") as photo:
        msg = await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo,
            caption=(
                "👋 Привет! Добро пожаловать в *Lira Music* 🎵\n\n"
                "Чтобы пользоваться ботом — подпишись на наш канал!\n\n"
                "После подписки нажми *«Я подписался»* ✅"
            ),
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    context.user_data["sub_msg_id"] = msg.message_id

# ──────────────────────────────────────────────
# Определяем тип ссылки
# ──────────────────────────────────────────────
def detect_source(text: str) -> str:
    if "spotify.com" in text:
        return "spotify"
    if "youtube.com" in text or "youtu.be" in text:
        return "youtube"
    if "soundcloud.com" in text:
        return "soundcloud"
    return "search"

# ──────────────────────────────────────────────
# Скачать аудио
# ──────────────────────────────────────────────
def download_audio(query: str, source: str) -> dict:
    if source in ("spotify", "search"):
        url = f"ytsearch1:{query}"
    else:
        url = query

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = info["entries"][0]

        title = info.get("title", "Unknown")
        duration = info.get("duration", 0)
        uploader = info.get("uploader", "Unknown")
        thumbnail = info.get("thumbnail", None)
        filename = ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp3"

        return {
            "title": title,
            "duration": duration,
            "uploader": uploader,
            "thumbnail": thumbnail,
            "filename": filename,
        }

# ──────────────────────────────────────────────
# Поиск без скачивания
# ──────────────────────────────────────────────
def search_tracks(query: str, limit: int = 5) -> list:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        results = []
        for entry in info.get("entries", []):
            results.append({
                "title": entry.get("title", "Unknown"),
                "url": f"https://youtube.com/watch?v={entry.get('id')}",
                "duration": entry.get("duration", 0),
                "uploader": entry.get("uploader", ""),
                "thumbnail": entry.get("thumbnail", None),
            })
        return results

# ──────────────────────────────────────────────
# Форматируем длительность
# ──────────────────────────────────────────────
def fmt_duration(seconds) -> str:
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

# ──────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update, context)
        return

    with open(BANNER_PATH, "rb") as photo:
        await update.message.reply_photo(
            photo=photo,
            caption=(
                "🎵 *Lira Music* — твой музыкальный бот!\n\n"
                "Отправь мне:\n"
                "🔗 Ссылку на *YouTube*\n"
                "🔗 Ссылку на *SoundCloud*\n"
                "🔗 Ссылку на *Spotify*\n"
                "🔍 Или просто *название трека*\n\n"
                "И я пришлю MP3 за несколько секунд ✨"
            ),
            parse_mode="Markdown"
        )

# ──────────────────────────────────────────────
# Callback кнопки
# ──────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Проверка подписки
    if data == "check_sub":
        user_id = query.from_user.id
        if await is_subscribed(user_id, context):
            # Удаляем экран подписки
            try:
                await query.message.delete()
            except Exception:
                pass
            # Показываем главное меню
            with open(BANNER_PATH, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=photo,
                    caption=(
                        "✅ *Подписка подтверждена!*\n\n"
                        "🎵 *Lira Music* — твой музыкальный бот!\n\n"
                        "Отправь мне:\n"
                        "🔗 Ссылку на *YouTube / SoundCloud / Spotify*\n"
                        "🔍 Или просто *название трека*\n\n"
                        "И я пришлю MP3 за несколько секунд ✨"
                    ),
                    parse_mode="Markdown"
                )
        else:
            await query.answer("❌ Ты ещё не подписан! Подпишись и попробуй снова.", show_alert=True)
        return

    # Скачивание по выбору из списка
    if data.startswith("dl|"):
        url = data[3:]
        msg = await query.message.reply_text("⏳ Скачиваю...")
        await _do_download(query.message, context, url, "youtube", msg)

# ──────────────────────────────────────────────
# Сообщения (личка и группы)
# ──────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

    # В группе реагируем только на упоминание бота
    if chat_type in ("group", "supergroup"):
        bot_username = (await context.bot.get_me()).username
        mention = f"@{bot_username}"
        if mention.lower() not in text.lower():
            return
        text = text.replace(mention, "").replace(mention.lower(), "").strip()
        if not text:
            return
        # В группах проверку подписки не делаем
        await _handle_query(update, context, text, check_sub=False)
        return

    # В личке — проверяем подписку
    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update, context)
        return

    await _handle_query(update, context, text, check_sub=True)

async def _handle_query(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, check_sub: bool):
    source = detect_source(text)

    # Прямая ссылка — скачиваем сразу
    if source in ("youtube", "soundcloud"):
        msg = await update.message.reply_text("⏳ Скачиваю...")
        await _do_download(update.message, context, text, source, msg)
        return

    # Spotify — ищем на YouTube
    if source == "spotify":
        msg = await update.message.reply_text("🔍 Ищу трек из Spotify...")
        await _do_download(update.message, context, text, "spotify", msg)
        return

    # Поиск по названию
    msg = await update.message.reply_text(f"🔍 Ищу: *{text}*...", parse_mode="Markdown")
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: search_tracks(text))
        if not results:
            await msg.edit_text("❌ Ничего не найдено")
            return

        keyboard = []
        for r in results:
            label = f"🎵 {r['title'][:38]} [{fmt_duration(r['duration'])}]"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"dl|{r['url']}")])

        await msg.edit_text(
            f"🎵 Результаты для: *{text}*\n\nВыбери трек 👇",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка поиска: {e}")

# ──────────────────────────────────────────────
# Скачать и отправить
# ──────────────────────────────────────────────
async def _do_download(message, context, url, source, msg):
    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: download_audio(url, source))

        filepath = info["filename"]
        if not os.path.exists(filepath):
            await msg.edit_text("❌ Файл не найден после скачивания")
            return

        size_mb = os.path.getsize(filepath) / 1024 / 1024
        if size_mb > 50:
            await msg.edit_text("❌ Файл слишком большой (>50MB) для Telegram")
            os.remove(filepath)
            return

        await msg.edit_text(f"📤 Отправляю: *{info['title']}*...", parse_mode="Markdown")

        # Получаем обложку
        thumb = None
        if info.get("thumbnail"):
            try:
                resp = requests.get(info["thumbnail"], timeout=10)
                if resp.status_code == 200:
                    thumb = BytesIO(resp.content)
                    thumb.name = "thumb.jpg"
            except Exception:
                pass

        with open(filepath, "rb") as f:
            await message.reply_audio(
                audio=f,
                title=info["title"],
                performer=info["uploader"],
                duration=info["duration"],
                thumbnail=thumb,
                caption=f"🎵 *{info['title']}*\n👤 {info['uploader']}\n⏱ {fmt_duration(info['duration'])}\n\n_Lira Music_ 🎧",
                parse_mode="Markdown"
            )

        await msg.delete()
        os.remove(filepath)

        # Чистим временные файлы обложек
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            thumb_path = filepath.replace(".mp3", ext)
            if os.path.exists(thumb_path):
                os.remove(thumb_path)

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

# ──────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден в .env файле!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Lira Music Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
