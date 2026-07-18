import subprocess
import sys
import os
import stat
import urllib.request
import zipfile

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])

for pkg in ["python-telegram-bot==21.5", "yt-dlp", "python-dotenv", "requests"]:
    install(pkg)

FFMPEG_DIR = "/tmp/ffmpeg_bin"
FFMPEG_PATH = os.path.join(FFMPEG_DIR, "ffmpeg")

def ensure_ffmpeg():
    if os.path.exists(FFMPEG_PATH):
        return FFMPEG_DIR
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True)
        if r.returncode == 0:
            return None
    except Exception:
        pass
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    print("⬇️ Скачиваю ffmpeg...")
    try:
        import tarfile
        url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
        tar_path = "/tmp/ffmpeg.tar.xz"
        urllib.request.urlretrieve(url, tar_path)
        with tarfile.open(tar_path) as tar:
            for member in tar.getmembers():
                bn = os.path.basename(member.name)
                if bn in ("ffmpeg", "ffprobe"):
                    member.name = bn
                    tar.extract(member, FFMPEG_DIR)
                    os.chmod(os.path.join(FFMPEG_DIR, bn),
                             stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        print("✅ ffmpeg установлен")
        return FFMPEG_DIR
    except Exception as e:
        print(f"❌ ffmpeg не удалось установить: {e}")
        return None

FFMPEG_LOCATION = ensure_ffmpeg()

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

def esc(text: str) -> str:
    """Экранируем спецсимволы для HTML"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

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
        "👋 Привет! Добро пожаловать в <b>Lira Music</b> 🎵\n\n"
        "Чтобы пользоваться ботом — подпишись на наш канал!\n\n"
        "После подписки нажми <b>«Я подписался»</b> ✅"
    )
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as p:
            await context.bot.send_photo(chat_id=chat_id, photo=p,
                caption=caption, parse_mode="HTML", reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=chat_id, text=caption,
            parse_mode="HTML", reply_markup=keyboard)

def detect_source(text: str) -> str:
    t = text.lower()
    if "spotify.com" in t:
        return "spotify"
    if "youtube.com" in t or "youtu.be" in t or "music.youtube.com" in t:
        return "youtube"
    if "soundcloud.com" in t:
        return "soundcloud"
    return "search"

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

def download_audio(query: str, source: str) -> dict:
    if source == "spotify":
        search_q = get_spotify_title(query)
        url = f"ytsearch1:{search_q}"
    elif source == "search":
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update.effective_chat.id, context)
        return
    caption = (
        "🎵 <b>Lira Music</b> — твой музыкальный бот!\n\n"
        "Отправь мне:\n"
        "🔗 Ссылку на <b>YouTube / YouTube Music</b>\n"
        "🔗 Ссылку на <b>SoundCloud</b>\n"
        "🔗 Ссылку на <b>Spotify</b>\n"
        "🔍 Или просто <b>название трека</b>\n\n"
        "В группе: @упомяни_бота + запрос 🎧"
    )
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as p:
            await update.message.reply_photo(photo=p, caption=caption, parse_mode="HTML")
    else:
        await update.message.reply_text(caption, parse_mode="HTML")

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
            caption = "✅ <b>Подписка подтверждена!</b>\n\n🎵 Отправь ссылку или название трека 🎧"
            if os.path.exists(BANNER_PATH):
                with open(BANNER_PATH, "rb") as p:
                    await context.bot.send_photo(chat_id=query.message.chat_id,
                        photo=p, caption=caption, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=query.message.chat_id,
                    text=caption, parse_mode="HTML")
        else:
            await query.answer("❌ Ты ещё не подписан!", show_alert=True)
        return

    if data.startswith("dl|"):
        url = data[3:]
        msg = await query.message.reply_text("⏳ Скачиваю...")
        await _do_download(query.message, context, url, "youtube", msg)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

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

    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update.effective_chat.id, context)
        return

    await _handle_query(update, context, text)

async def _handle_query(update, context, text):
    source = detect_source(text)

    if source in ("youtube", "soundcloud", "spotify"):
        labels = {"youtube": "YouTube", "soundcloud": "SoundCloud", "spotify": "Spotify"}
        msg = await update.message.reply_text(f"⏳ Скачиваю с {labels[source]}...")
        await _do_download(update.message, context, text, source, msg)
        return

    msg = await update.message.reply_text(f"🔍 Ищу: <b>{esc(text)}</b>...", parse_mode="HTML")
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
            f"🎵 Результаты для: <b>{esc(text)}</b>\n\nВыбери трек 👇",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка поиска: {e}")

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

        await msg.edit_text(f"📤 Отправляю: <b>{esc(info['title'])}</b>...", parse_mode="HTML")

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
                    f"🎵 <b>{esc(info['title'])}</b>\n"
                    f"👤 {esc(info['uploader'])}\n"
                    f"⏱ {fmt_duration(info['duration'])}\n\n"
                    f"<i>Lira Music</i> 🎧"
                ),
                parse_mode="HTML"
            )

        await msg.delete()
        os.remove(filepath)

        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            tp = filepath.replace(".mp3", ext)
            if os.path.exists(tp):
                os.remove(tp)

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

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
