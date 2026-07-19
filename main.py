import subprocess
import sys
import os

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])

for pkg in ["python-telegram-bot==21.5", "yt-dlp", "python-dotenv", "requests"]:
    install(pkg)

print("✅ Библиотеки установлены, запускаю бота...")

import asyncio
import re
import uuid
import stat
import urllib.request
import requests
from io import BytesIO
from dotenv import load_dotenv
import yt_dlp
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
    InlineQueryHandler
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Lira_music_robot")
CHANNEL = "@Lira_projects"
DOWNLOAD_DIR = "/tmp/music"
BANNER_PATH = "lira_banner.png"
FFMPEG_DIR = "/tmp/ffmpeg_bin"
FFMPEG_PATH = os.path.join(FFMPEG_DIR, "ffmpeg")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

print(f"Токен: {'OK' if BOT_TOKEN else 'НЕ НАЙДЕН!'}")
print(f"Баннер: {'OK' if os.path.exists(BANNER_PATH) else 'нет файла'}")

# ffmpeg ищем асинхронно в фоне
FFMPEG_LOCATION = None

def find_or_download_ffmpeg():
    global FFMPEG_LOCATION
    # Уже есть?
    if os.path.exists(FFMPEG_PATH):
        FFMPEG_LOCATION = FFMPEG_DIR
        print(f"✅ ffmpeg: {FFMPEG_DIR}")
        return
    # Системный?
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        if r.returncode == 0:
            FFMPEG_LOCATION = None
            print("✅ ffmpeg: системный")
            return
    except Exception:
        pass
    # Скачиваем в фоне
    print("⬇️ ffmpeg не найден, скачиваю в фоне...")
    try:
        import tarfile
        os.makedirs(FFMPEG_DIR, exist_ok=True)
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
        FFMPEG_LOCATION = FFMPEG_DIR
        print("✅ ffmpeg установлен!")
    except Exception as e:
        print(f"⚠️ ffmpeg не удалось установить: {e}")

# Запускаем поиск ffmpeg в отдельном потоке чтобы не блокировать
import threading
threading.Thread(target=find_or_download_ffmpeg, daemon=True).start()

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def fmt_duration(seconds) -> str:
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

async def is_subscribed(user_id: int, context) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return True  # если не можем проверить — пускаем

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
    if "spotify.com" in t: return "spotify"
    if "youtube.com" in t or "youtu.be" in t: return "youtube"
    if "soundcloud.com" in t: return "soundcloud"
    return "search"

def get_spotify_title(url: str) -> str:
    try:
        r = requests.get("https://open.spotify.com/oembed", params={"url": url}, timeout=10)
        if r.status_code == 200:
            return r.json().get("title", "") or url
    except Exception:
        pass
    return url

YDL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

def search_tracks(query: str, limit: int = 5) -> list:
    opts = {
        "quiet": True, "no_warnings": True, "extract_flat": True,
        "http_headers": YDL_HEADERS,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return [{
            "title": e.get("title", "Unknown"),
            "url": f"https://youtube.com/watch?v={e.get('id')}",
            "duration": e.get("duration", 0),
            "uploader": e.get("uploader", ""),
        } for e in info.get("entries", [])]

def download_audio(query: str, source: str) -> dict:
    if source == "soundcloud":
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
                info = ydl.extract_info(query, download=False)
                t = info.get("title", "")
                u = info.get("uploader", "")
                q = f"{u} {t}".strip()
            url = f"ytsearch1:{q}"
        except Exception:
            url = f"ytsearch1:{query}"
    elif source == "spotify":
        url = f"ytsearch1:{get_spotify_title(query)}"
    elif source == "search":
        url = f"ytsearch1:{query}"
    else:
        url = query

    opts = {
        "quiet": True, "no_warnings": True,
        "http_headers": YDL_HEADERS,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        "writethumbnail": True,
    }
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = info["entries"][0]
        filename = ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp3"
        return {
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", "Unknown"),
            "thumbnail": info.get("thumbnail"),
            "filename": filename,
        }

# ──────────────────────────────────────────────
# Handlers
# ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update.effective_chat.id, context)
        return
    caption = (
        "🎵 <b>Lira Music</b> — твой музыкальный бот!\n\n"
        "Отправь ссылку или название трека 🎧\n\n"
        "В группе: введи в поле сообщения\n"
        f"<code>@{BOT_USERNAME} название</code>"
    )
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as p:
            await update.message.reply_photo(photo=p, caption=caption, parse_mode="HTML")
    else:
        await update.message.reply_text(caption, parse_mode="HTML")

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query.query.strip()
    if not q:
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id="hint", title="🎵 Введи название трека",
                description=f"Пример: @{BOT_USERNAME} группа крови",
                input_message_content=InputTextMessageContent("🎵 Lira Music"),
            )
        ], cache_time=5)
        return
    try:
        loop = asyncio.get_event_loop()
        tracks = await loop.run_in_executor(None, lambda: search_tracks(q, 5))
    except Exception:
        tracks = []

    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"🎵 {t['title']}",
            description=f"👤 {t['uploader']} • ⏱ {fmt_duration(t['duration'])}",
            input_message_content=InputTextMessageContent(
                f"🎵 <b>{esc(t['title'])}</b>\n👤 {esc(t['uploader'])}\n\n"
                f"<a href='https://t.me/{BOT_USERNAME}'>🎧 Lira Music</a>",
                parse_mode="HTML"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎵 Скачать", callback_data=f"inline_dl|{t['url']}")
            ]])
        ) for t in tracks
    ] or [InlineQueryResultArticle(
        id="no", title="❌ Ничего не найдено",
        input_message_content=InputTextMessageContent("❌ Ничего не найдено"),
    )]
    await update.inline_query.answer(results, cache_time=10)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data == "check_sub":
        await q.answer()
        if await is_subscribed(q.from_user.id, context):
            try: await q.message.delete()
            except Exception: pass
            caption = "✅ <b>Подписка подтверждена!</b>\n\n🎵 Отправь ссылку или название трека 🎧"
            if os.path.exists(BANNER_PATH):
                with open(BANNER_PATH, "rb") as p:
                    await context.bot.send_photo(chat_id=q.message.chat_id, photo=p, caption=caption, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=q.message.chat_id, text=caption, parse_mode="HTML")
        else:
            await q.answer("❌ Ты ещё не подписан!", show_alert=True)
        return

    if data.startswith("dl|"):
        await q.answer()
        msg = await q.message.reply_text("⏳ Скачиваю...")
        await _do_download(q.message, context, data[3:], "youtube", msg)
        return

    if data.startswith("inline_dl|"):
        await q.answer("⏳ Скачиваю...")
        url = data[10:]
        chat_id = q.message.chat_id
        try: await q.edit_message_reply_markup(reply_markup=None)
        except Exception: pass
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: download_audio(url, "youtube"))
            filepath = info["filename"]
            if not os.path.exists(filepath): return
            if os.path.getsize(filepath) / 1024 / 1024 > 50:
                os.remove(filepath); return
            thumb = None
            if info.get("thumbnail"):
                try:
                    r = requests.get(info["thumbnail"], timeout=10)
                    if r.status_code == 200:
                        thumb = BytesIO(r.content); thumb.name = "thumb.jpg"
                except Exception: pass
            with open(filepath, "rb") as f:
                await context.bot.send_audio(
                    chat_id=chat_id, audio=f,
                    title=info["title"], performer=info["uploader"],
                    duration=info["duration"], thumbnail=thumb,
                    caption=f"🎵 <b>{esc(info['title'])}</b>\n👤 {esc(info['uploader'])}\n\n<a href='https://t.me/{BOT_USERNAME}'>🎧 Lira Music</a>",
                    parse_mode="HTML"
                )
            os.remove(filepath)
            for ext in [".jpg", ".jpeg", ".png", ".webp"]:
                tp = filepath.replace(".mp3", ext)
                if os.path.exists(tp): os.remove(tp)
        except Exception as e:
            print(f"❌ inline_dl: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if update.effective_chat.type in ("group", "supergroup"): return
    text = update.message.text.strip()
    if not await is_subscribed(update.effective_user.id, context):
        await show_subscribe_screen(update.effective_chat.id, context); return
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
        results = await asyncio.get_event_loop().run_in_executor(None, lambda: search_tracks(text))
        if not results:
            await msg.edit_text("❌ Ничего не найдено"); return
        keyboard = [[InlineKeyboardButton(f"🎵 {r['title'][:38]} [{fmt_duration(r['duration'])}]", callback_data=f"dl|{r['url']}")] for r in results]
        await msg.edit_text(f"🎵 Результаты: <b>{esc(text)}</b>\n\nВыбери 👇",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

async def _do_download(message, context, url, source, msg):
    try:
        info = await asyncio.get_event_loop().run_in_executor(None, lambda: download_audio(url, source))
        filepath = info["filename"]
        if not os.path.exists(filepath):
            await msg.edit_text("❌ Файл не найден"); return
        if os.path.getsize(filepath) / 1024 / 1024 > 50:
            await msg.edit_text("❌ Файл >50MB"); os.remove(filepath); return
        await msg.edit_text(f"📤 Отправляю: <b>{esc(info['title'])}</b>...", parse_mode="HTML")
        thumb = None
        if info.get("thumbnail"):
            try:
                r = requests.get(info["thumbnail"], timeout=10)
                if r.status_code == 200:
                    thumb = BytesIO(r.content); thumb.name = "thumb.jpg"
            except Exception: pass
        with open(filepath, "rb") as f:
            await message.reply_audio(
                audio=f, title=info["title"], performer=info["uploader"],
                duration=info["duration"], thumbnail=thumb,
                caption=f"🎵 <b>{esc(info['title'])}</b>\n👤 {esc(info['uploader'])}\n\n<a href='https://t.me/{BOT_USERNAME}'>🎧 Lira Music</a>",
                parse_mode="HTML"
            )
        await msg.delete()
        os.remove(filepath)
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            tp = filepath.replace(".mp3", ext)
            if os.path.exists(tp): os.remove(tp)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден!"); return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Lira Music Bot запущен!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
