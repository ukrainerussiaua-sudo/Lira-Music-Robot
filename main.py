import subprocess
import sys
import os

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])

for pkg in ["python-telegram-bot==21.5", "yt-dlp", "python-dotenv", "requests"]:
    install(pkg)

# Обновляем yt-dlp до последней версии
subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "-q"])
print("✅ Библиотеки установлены, запускаю бота...")

import asyncio
import re
import uuid
import stat
import time
import urllib.request
import requests
import threading
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
    InlineQueryHandler,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Lira_music_robot")
CHANNEL = "@Lira_projects"
DOWNLOAD_DIR = "/tmp/music"
BANNER_PATH = "lira_banner.png"
FFMPEG_DIR = "/tmp/ffmpeg_bin"
FFMPEG_PATH = os.path.join(FFMPEG_DIR, "ffmpeg")
MAX_DURATION = 10 * 60
IDLE_TIMEOUT = 30 * 60

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
print(f"Токен: {'OK' if BOT_TOKEN else '❌ НЕ НАЙДЕН!'}")
print(f"Баннер: {'OK' if os.path.exists(BANNER_PATH) else 'нет'}")

FFMPEG_LOCATION = None

def find_or_download_ffmpeg():
    global FFMPEG_LOCATION
    if os.path.exists(FFMPEG_PATH):
        FFMPEG_LOCATION = FFMPEG_DIR
        print("✅ ffmpeg: кэш")
        return
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        if r.returncode == 0:
            print("✅ ffmpeg: системный")
            return
    except Exception:
        pass
    try:
        import tarfile
        os.makedirs(FFMPEG_DIR, exist_ok=True)
        urllib.request.urlretrieve(
            "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
            "/tmp/ffmpeg.tar.xz"
        )
        with tarfile.open("/tmp/ffmpeg.tar.xz") as tar:
            for member in tar.getmembers():
                bn = os.path.basename(member.name)
                if bn in ("ffmpeg", "ffprobe"):
                    member.name = bn
                    tar.extract(member, FFMPEG_DIR)
                    os.chmod(os.path.join(FFMPEG_DIR, bn),
                             stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        FFMPEG_LOCATION = FFMPEG_DIR
        print("✅ ffmpeg: установлен")
    except Exception as e:
        print(f"⚠️ ffmpeg: {e}")

threading.Thread(target=find_or_download_ffmpeg, daemon=True).start()

# Хранилище pending треков: track_id → info
pending_tracks: dict = {}
user_last_action: dict = {}
user_reminder_sent: dict = {}

def update_activity(user_id: int):
    user_last_action[user_id] = time.time()
    user_reminder_sent[user_id] = False

def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def fmt_duration(seconds) -> str:
    if not seconds: return "?"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

async def is_subscribed(user_id: int, context) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return True

def get_welcome_caption():
    return (
        "🎵 <b>Lira Music</b> — твой музыкальный бот!\n\n"
        "Отправь мне:\n"
        "🔗 Ссылку на <b>YouTube / YouTube Music</b>\n"
        "🔗 Ссылку на <b>SoundCloud</b>\n"
        "🔗 Ссылку на <b>Spotify</b>\n"
        "🔍 Или просто <b>название трека</b>\n\n"
        f"В группе: @{BOT_USERNAME} + трек 🎧"
    )

async def send_welcome(chat_id, context):
    caption = get_welcome_caption()
    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as p:
            await context.bot.send_photo(chat_id=chat_id, photo=p,
                caption=caption, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML")

async def show_subscribe_screen(chat_id, context, pending_track_id=None):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Перейти на канал", url="https://t.me/Lira_projects")],
        [InlineKeyboardButton("✅ Я подписался",
            callback_data=f"check_sub|{pending_track_id or ''}")],
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
        await context.bot.send_message(chat_id=chat_id,
            text=caption, parse_mode="HTML", reply_markup=keyboard)

def detect_source(text: str) -> str:
    t = text.lower()
    if "spotify.com" in t: return "spotify"
    if "youtube.com" in t or "youtu.be" in t: return "youtube"
    if "soundcloud.com" in t: return "soundcloud"
    return "search"

def get_spotify_title(url: str) -> str:
    try:
        r = requests.get("https://open.spotify.com/oembed",
                         params={"url": url}, timeout=10)
        if r.status_code == 200:
            return r.json().get("title", "") or url
    except Exception:
        pass
    return url

def get_soundcloud_query(url: str) -> str:
    try:
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            t = info.get("title", "")
            u = info.get("uploader", "")
            return f"{u} {t}".strip() if u else t
    except Exception:
        parts = url.rstrip("/").split("/")
        return parts[-1].replace("-", " ")

# ──────────────────────────────────────────────
# yt-dlp конфиги — несколько клиентов для обхода блокировки
# ──────────────────────────────────────────────
COOKIES_PATH = "cookies.txt"

YDL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

YT_CLIENTS = [
    ["ios"],
    ["android"],
    ["tv_simply"],
    ["mweb"],
]

def make_ydl_opts(extra: dict = {}) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "http_headers": YDL_HEADERS,
        **extra,
    }
    if os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    return opts

def search_tracks(query: str, limit: int = 7) -> list:
    opts = make_ydl_opts({"extract_flat": True})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return [{
            "title": e.get("title", "Unknown"),
            "url": f"https://youtube.com/watch?v={e.get('id')}",
            "duration": e.get("duration", 0),
            "uploader": e.get("uploader", ""),
        } for e in info.get("entries", [])
        if not e.get("duration") or e.get("duration", 0) <= MAX_DURATION]

def download_audio(url: str, source: str) -> dict:
    if source == "soundcloud":
        url = f"ytsearch1:{get_soundcloud_query(url)}"
    elif source == "spotify":
        url = f"ytsearch1:{get_spotify_title(url)}"
    elif source == "search":
        url = f"ytsearch1:{url}"

    base_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3", "preferredquality": "192"}],
        "writethumbnail": True,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {MAX_DURATION}"),
    }
    if FFMPEG_LOCATION:
        base_opts["ffmpeg_location"] = FFMPEG_LOCATION

    last_error = None
    for client in YT_CLIENTS:
        try:
            opts = make_ydl_opts({
                **base_opts,
                "extractor_args": {"youtube": {"player_client": client}},
            })
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
        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            if "sign in" in err_str or "bot" in err_str or "403" in err_str:
                print(f"⚠️ Клиент {client} заблокирован, пробую следующий...")
                continue
            raise e

    raise Exception(f"Все клиенты заблокированы YouTube: {last_error}")

def get_thumb(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            b = BytesIO(r.content); b.name = "thumb.jpg"; return b
    except Exception:
        pass
    return None

def cleanup(filepath):
    try: os.remove(filepath)
    except Exception: pass
    for ext in [".jpg", ".jpeg", ".png", ".webp"]:
        try: os.remove(filepath.replace(".mp3", ext))
        except Exception: pass

async def idle_reminder_loop(app):
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for user_id, last in list(user_last_action.items()):
            if not user_reminder_sent.get(user_id, False):
                if now - last >= IDLE_TIMEOUT:
                    try:
                        await send_welcome(user_id, app)
                        user_reminder_sent[user_id] = True
                    except Exception:
                        pass

# ──────────────────────────────────────────────
# /start — обрабатывает deep link с track_id
# ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_activity(user_id)

    track_id = context.args[0] if context.args else None

    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update.effective_chat.id, context, track_id)
        return

    if track_id and track_id in pending_tracks:
        track_info = pending_tracks[track_id]
        msg = await update.message.reply_text(
            f"⏳ Скачиваю <b>{esc(track_info['title'])}</b>...", parse_mode="HTML")
        await _do_download_to_chat(update.effective_chat.id, context,
                                   track_info["url"], "youtube", msg)
        return

    await send_welcome(update.effective_chat.id, context)

# ──────────────────────────────────────────────
# Inline — поиск, в группу летит анонс с кнопкой
# ──────────────────────────────────────────────
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query.query.strip()
    update_activity(update.inline_query.from_user.id)

    if not q:
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id="hint", title="🎵 Введи название трека",
                description=f"Пример: @{BOT_USERNAME} группа крови",
                input_message_content=InputTextMessageContent(
                    f"🎵 <b>Lira Music</b>", parse_mode="HTML"),
            )
        ], cache_time=5)
        return

    try:
        tracks = await asyncio.get_event_loop().run_in_executor(
            None, lambda: search_tracks(q))
    except Exception:
        tracks = []

    results = []
    for t in tracks:
        track_id = f"track_{uuid.uuid4().hex[:16]}"
        pending_tracks[track_id] = {
            "title": t["title"],
            "uploader": t["uploader"],
            "duration": t["duration"],
            "url": t["url"],
        }
        deep_link = f"https://t.me/{BOT_USERNAME}?start={track_id}"

        results.append(InlineQueryResultArticle(
            id=track_id,
            title=f"🎵 {t['title']}",
            description=f"👤 {t['uploader']} • ⏱ {fmt_duration(t['duration'])}",
            input_message_content=InputTextMessageContent(
                f"🎵 <b>{esc(t['title'])}</b>\n"
                f"👤 {esc(t['uploader'])} • ⏱ {fmt_duration(t['duration'])}\n\n"
                f"<a href='https://t.me/{BOT_USERNAME}'>🎧 Lira Music</a>",
                parse_mode="HTML"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎵 Слушать", url=deep_link)
            ]])
        ))

    if not results:
        results = [InlineQueryResultArticle(
            id="no", title="❌ Ничего не найдено",
            input_message_content=InputTextMessageContent("❌ Ничего не найдено"),
        )]

    await update.inline_query.answer(results, cache_time=10)

# ──────────────────────────────────────────────
# Callback
# ──────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    update_activity(q.from_user.id)

    if data.startswith("check_sub"):
        await q.answer()
        parts = data.split("|", 1)
        track_id = parts[1] if len(parts) > 1 and parts[1] else None

        if await is_subscribed(q.from_user.id, context):
            try: await q.message.delete()
            except Exception: pass

            if track_id and track_id in pending_tracks:
                track_info = pending_tracks[track_id]
                msg = await context.bot.send_message(
                    chat_id=q.message.chat_id,
                    text=f"⏳ Скачиваю <b>{esc(track_info['title'])}</b>...",
                    parse_mode="HTML"
                )
                await _do_download_to_chat(q.message.chat_id, context,
                                           track_info["url"], "youtube", msg)
            else:
                await send_welcome(q.message.chat_id, context)
        else:
            await q.answer("❌ Ты ещё не подписан!", show_alert=True)
        return

    if data.startswith("dl|"):
        await q.answer()
        msg = await q.message.reply_text("⏳ Скачиваю...")
        await _do_download_to_chat(q.message.chat_id, context,
                                   data[3:], "youtube", msg)

# ──────────────────────────────────────────────
# Личные сообщения
# ──────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if update.effective_chat.type in ("group", "supergroup"): return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    update_activity(user_id)

    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update.effective_chat.id, context)
        return

    await _handle_query(update, context, text)

async def _handle_query(update, context, text):
    source = detect_source(text)
    if source in ("youtube", "soundcloud", "spotify"):
        labels = {"youtube": "YouTube", "soundcloud": "SoundCloud", "spotify": "Spotify"}
        msg = await update.message.reply_text(f"⏳ Скачиваю с {labels[source]}...")
        await _do_download_to_chat(update.effective_chat.id, context, text, source, msg)
        return

    msg = await update.message.reply_text(
        f"🔍 Ищу: <b>{esc(text)}</b>...", parse_mode="HTML")
    try:
        results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: search_tracks(text))
        if not results:
            await msg.edit_text("❌ Ничего не найдено")
            return
        keyboard = [[InlineKeyboardButton(
            f"🎵 {r['title'][:38]} [{fmt_duration(r['duration'])}]",
            callback_data=f"dl|{r['url']}"
        )] for r in results]
        await msg.edit_text(
            f"🎵 <b>{esc(text)}</b>\n\nВыбери трек 👇",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

# ──────────────────────────────────────────────
# Скачать и отправить в чат
# ──────────────────────────────────────────────
async def _do_download_to_chat(chat_id, context, url, source, msg):
    try:
        info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: download_audio(url, source))
        if info["duration"] and info["duration"] > MAX_DURATION:
            await msg.edit_text("❌ Трек длиннее 10 минут"); return
        filepath = info["filename"]
        if not os.path.exists(filepath):
            await msg.edit_text("❌ Файл не найден"); return
        if os.path.getsize(filepath) / 1024 / 1024 > 50:
            await msg.edit_text("❌ Файл >50MB"); cleanup(filepath); return
        await msg.edit_text(
            f"📤 Отправляю: <b>{esc(info['title'])}</b>...", parse_mode="HTML")
        thumb = get_thumb(info["thumbnail"]) if info.get("thumbnail") else None
        caption = (
            f"🎵 <b>{esc(info['title'])}</b>\n"
            f"👤 {esc(info['uploader'])}\n\n"
            f"<a href='https://t.me/{BOT_USERNAME}'>🎧 Lira Music</a>"
        )
        with open(filepath, "rb") as f:
            await context.bot.send_audio(
                chat_id=chat_id, audio=f,
                title=info["title"], performer=info["uploader"],
                duration=info["duration"], thumbnail=thumb,
                caption=caption, parse_mode="HTML")
        await msg.delete()
        cleanup(filepath)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

async def post_init(app: Application):
    asyncio.create_task(idle_reminder_loop(app))

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден!"); return
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Lira Music Bot запущен!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
