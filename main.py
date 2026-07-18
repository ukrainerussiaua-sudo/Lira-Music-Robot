import subprocess
import sys
import os
import stat
import urllib.request
import zipfile

# Автоустановка библиотек
def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])

for pkg in ["python-telegram-bot==21.5", "yt-dlp", "python-dotenv", "requests"]:
    install(pkg)

# ──────────────────────────────────────────────
# Установка ffmpeg статическим бинарником
# ──────────────────────────────────────────────
FFMPEG_DIR = "/tmp/ffmpeg_bin"
FFMPEG_PATH = os.path.join(FFMPEG_DIR, "ffmpeg")
FFPROBE_PATH = os.path.join(FFMPEG_DIR, "ffprobe")

def ensure_ffmpeg():
    # Уже скачан?
    if os.path.exists(FFMPEG_PATH):
        return FFMPEG_DIR
    # Попробуем системный
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True)
        if r.returncode == 0:
            return None  # None = системный, не нужен путь
    except Exception:
        pass
    # Скачиваем статический бинарник
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    print("⬇️ Скачиваю ffmpeg...")
    url = "https://github.com/nicholasess/ffmpeg-python-binary/releases/download/1.0.0/ffmpeg-release-amd64-static.zip"
    zip_path = "/tmp/ffmpeg.zip"
    try:
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as z:
            for name in z.namelist():
                if name.endswith("ffmpeg") or name.endswith("ffprobe"):
                    data = z.read(name)
                    out = FFMPEG_PATH if name.endswith("ffmpeg") else FFPROBE_PATH
                    with open(out, "wb") as f:
                        f.write(data)
                    os.chmod(out, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        print("✅ ffmpeg установлен")
        return FFMPEG_DIR
    except Exception as e:
        print(f"⚠️ Zip не сработал: {e}, пробую tar.xz...")

    # Запасной — tar.xz от yt-dlp builds
    try:
        import tarfile
        url2 = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
        tar_path = "/tmp/ffmpeg.tar.xz"
        urllib.request.urlretrieve(url2, tar_path)
        with tarfile.open(tar_path) as tar:
            for member in tar.getmembers():
                bn = os.path.basename(member.name)
                if bn in ("ffmpeg", "ffprobe"):
                    member.name = bn
                    tar.extract(member, FFMPEG_DIR)
                    os.chmod(os.path.join(FFMPEG_DIR, bn),
                             stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        print("✅ ffmpeg установлен (tar.xz)")
        return FFMPEG_DIR
    except Exception as e2:
        print(f"❌ ffmpeg не удалось установить: {e2}")
        return None

FFMPEG_LOCATION = ensure_ffmpeg()

# ──────────────────────────────────────────────
# Импорты
# ──────────────────────────────────────────────
import asyncio
import re
import requests
from io import BytesIO
from dotenv import load_dotenv
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
# Подписка
# ──────────────────────────────────────────────
async def is_subscribed(user_id: int, context) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

async def show_subscribe_screen(chat_id, context):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Перейти на канал", url="https://t.me/Lira_projects")],
        [InlineKeyboardButton("✅ Я подписался", callback_data="check_sub")],
    ])
    caption = (
        "👋 Привет! Добро пожаловать в *Lira Music* 🎵\n\n"
        "Чтобы пользоваться ботом — подпишись на наш канал!\n\n"
        "После подписки нажми *«Я подписался»* ✅"
    )
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as p:
            await context.bot.send_photo(chat_id=chat_id, photo=p,
                caption=caption, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=chat_id, text=caption,
            parse_mode="Markdown", reply_markup=keyboard)

# ──────────────────────────────────────────────
# Определить тип запроса
# ──────────────────────────────────────────────
def detect_source(text: str) -> str:
    t = text.lower()
    if "spotify.com" in t:
        return "spotify"
    if "youtube.com" in t or "youtu.be" in t or "music.youtube.com" in t:
        return "youtube"
    if "soundcloud.com" in t:
        return "soundcloud"
    return "search"

# ──────────────────────────────────────────────
# Spotify → название трека
# ──────────────────────────────────────────────
def get_spotify_title(url: str) -> str:
    try:
        r = requests.get("https://open.spotify.com/oembed",
                         params={"url": url}, timeout=10)
        if r.status_code == 200:
            title = r.json().get("title", "")
            if title:
                return title
    except Exception:
        pass
    return url

# ──────────────────────────────────────────────
# Скачать аудио
# ──────────────────────────────────────────────
def download_audio(query: str, source: str) -> dict:
    if source == "spotify":
        search_q = get_spotify_title(query)
        url = f"ytsearch1:{search_q}"
    elif source == "search":
        url = f"ytsearch1:{query}"
    else:
        url = query  # youtube / soundcloud — прямая ссылка

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
    if FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = info["entries"][0]
        filename = ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp3"
        return {
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", "Unknown"),
            "thumbnail": info.get("thumbnail", None),
            "filename": filename,
        }

# ──────────────────────────────────────────────
# Поиск (только для текстовых запросов)
# ──────────────────────────────────────────────
def search_tracks(query: str, limit: int = 5) -> list:
    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    if FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        results = []
        for entry in info.get("entries", []):
            results.append({
                "title": entry.get("title", "Unknown"),
                "url": f"https://youtube.com/watch?v={entry.get('id')}",
                "duration": entry.get("duration", 0),
                "uploader": entry.get("uploader", ""),
            })
        return results

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
        await show_subscribe_screen(update.effective_chat.id, context)
        return
    caption = (
        "🎵 *Lira Music* — твой музыкальный бот!\n\n"
        "Отправь мне:\n"
        "🔗 Ссылку на *YouTube / YouTube Music*\n"
        "🔗 Ссылку на *SoundCloud*\n"
        "🔗 Ссылку на *Spotify*\n"
        "🔍 Или просто *название трека*\n\n"
        "В группе: @упомяни_бота + запрос 🎧"
    )
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as p:
            await update.message.reply_photo(photo=p, caption=caption, parse_mode="Markdown")
    else:
        await update.message.reply_text(caption, parse_mode="Markdown")

# ──────────────────────────────────────────────
# Callback
# ──────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "check_sub":
        if await is_subscribed(query.from_user.id, context):
            try:
                await query.message.delete()
            except Exception:
                pass
            caption = "✅ *Подписка подтверждена!*\n\n🎵 Отправь ссылку или название трека 🎧"
            if os.path.exists(BANNER_PATH):
                with open(BANNER_PATH, "rb") as p:
                    await context.bot.send_photo(chat_id=query.message.chat_id,
                        photo=p, caption=caption, parse_mode="Markdown")
            else:
                await context.bot.send_message(chat_id=query.message.chat_id,
                    text=caption, parse_mode="Markdown")
        else:
            await query.answer("❌ Ты ещё не подписан!", show_alert=True)
        return

    if data.startswith("dl|"):
        url = data[3:]
        msg = await query.message.reply_text("⏳ Скачиваю...")
        await _do_download(query.message, context, url, "youtube", msg)

# ──────────────────────────────────────────────
# Сообщения
# ──────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

    # Группа — только по упоминанию
    if chat_type in ("group", "supergroup"):
        bot_username = (await context.bot.get_me()).username
        mention = f"@{bot_username}"
        if mention.lower() not in text.lower():
            return
        text = re.sub(re.escape(mention), "", text, flags=re.IGNORECASE).strip()
        if not text:
            return
        await _handle_query(update, context, text)
        return

    # Личка — проверяем подписку
    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update.effective_chat.id, context)
        return

    await _handle_query(update, context, text)

async def _handle_query(update, context, text):
    source = detect_source(text)

    # Ссылки — сразу скачиваем
    if source in ("youtube", "soundcloud", "spotify"):
        label = {"youtube": "YouTube", "soundcloud": "SoundCloud", "spotify": "Spotify"}[source]
        msg = await update.message.reply_text(f"⏳ Скачиваю с {label}...")
        await _do_download(update.message, context, text, source, msg)
        return

    # Текст — показываем список
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
                caption=(
                    f"🎵 *{info['title']}*\n"
                    f"👤 {info['uploader']}\n"
                    f"⏱ {fmt_duration(info['duration'])}\n\n"
                    f"_Lira Music_ 🎧"
                ),
                parse_mode="Markdown"
            )

        await msg.delete()
        os.remove(filepath)

        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            tp = filepath.replace(".mp3", ext)
            if os.path.exists(tp):
                os.remove(tp)

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

# ──────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден в .env!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Lira Music Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
