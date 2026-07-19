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
    InlineQueryHandler, ChosenInlineResultHandler
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Lira_music_robot")
CHANNEL = "@Lira_projects"
DOWNLOAD_DIR = "/tmp/music"
BANNER_PATH = "lira_banner.png"
FFMPEG_DIR = "/tmp/ffmpeg_bin"
FFMPEG_PATH = os.path.join(FFMPEG_DIR, "ffmpeg")
MAX_DURATION = 10 * 60  # 10 минут в секундах
IDLE_TIMEOUT = 30 * 60  # 30 минут в секундах

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

print(f"Токен: {'OK' if BOT_TOKEN else '❌ НЕ НАЙДЕН!'}")
print(f"Баннер: {'OK' if os.path.exists(BANNER_PATH) else 'нет'}")

FFMPEG_LOCATION = None

def find_or_download_ffmpeg():
    global FFMPEG_LOCATION
    if os.path.exists(FFMPEG_PATH):
        FFMPEG_LOCATION = FFMPEG_DIR
        print(f"✅ ffmpeg: кэш")
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
        url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
        urllib.request.urlretrieve(url, "/tmp/ffmpeg.tar.xz")
        with tarfile.open("/tmp/ffmpeg.tar.xz") as tar:
            for member in tar.getmembers():
                bn = os.path.basename(member.name)
                if bn in ("ffmpeg", "ffprobe"):
                    member.name = bn
                    tar.extract(member, FFMPEG_DIR)
                    os.chmod(os.path.join(FFMPEG_DIR, bn), stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        FFMPEG_LOCATION = FFMPEG_DIR
        print("✅ ffmpeg: установлен")
    except Exception as e:
        print(f"⚠️ ffmpeg: {e}")

threading.Thread(target=find_or_download_ffmpeg, daemon=True).start()

# ──────────────────────────────────────────────
# Хранилище: последняя активность и отправлен ли reminder
# ──────────────────────────────────────────────
user_last_action: dict[int, float] = {}
user_reminder_sent: dict[int, bool] = {}

def update_activity(user_id: int):
    user_last_action[user_id] = time.time()
    user_reminder_sent[user_id] = False

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
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "http_headers": YDL_HEADERS}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return [{
            "title": e.get("title", "Unknown"),
            "url": f"https://youtube.com/watch?v={e.get('id')}",
            "duration": e.get("duration", 0),
            "uploader": e.get("uploader", ""),
        } for e in info.get("entries", [])]

def download_audio(url: str, source: str) -> dict:
    if source == "soundcloud":
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
                info = ydl.extract_info(url, download=False)
                q = f"{info.get('uploader','')} {info.get('title','')}".strip()
            url = f"ytsearch1:{q}"
        except Exception:
            url = f"ytsearch1:{url}"
    elif source == "spotify":
        url = f"ytsearch1:{get_spotify_title(url)}"
    elif source == "search":
        url = f"ytsearch1:{url}"

    opts = {
        "quiet": True, "no_warnings": True, "http_headers": YDL_HEADERS,
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

def get_thumb(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            b = BytesIO(r.content)
            b.name = "thumb.jpg"
            return b
    except Exception:
        pass
    return None

def cleanup(filepath):
    try: os.remove(filepath)
    except Exception: pass
    for ext in [".jpg", ".jpeg", ".png", ".webp"]:
        tp = filepath.replace(".mp3", ext)
        try: os.remove(tp)
        except Exception: pass

# ──────────────────────────────────────────────
# 30-минутный напоминальщик
# ──────────────────────────────────────────────
async def idle_reminder_loop(app: Application):
    while True:
        await asyncio.sleep(60)  # проверяем каждую минуту
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
# Handlers
# ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_activity(user_id)
    if not await is_subscribed(user_id, context):
        await show_subscribe_screen(update.effective_chat.id, context)
        return
    await send_welcome(update.effective_chat.id, context)

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query.query.strip()
    user_id = update.inline_query.from_user.id
    update_activity(user_id)

    if not q:
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id="hint", title="🎵 Введи название трека",
                description=f"Пример: @{BOT_USERNAME} группа крови",
                input_message_content=InputTextMessageContent(
                    f"🎵 <b>Lira Music</b> — введи название трека", parse_mode="HTML"),
            )
        ], cache_time=5)
        return

    try:
        tracks = await asyncio.get_event_loop().run_in_executor(None, lambda: search_tracks(q, 5))
    except Exception:
        tracks = []

    results = []
    for t in tracks:
        # Пропускаем треки длиннее 10 минут
        if t["duration"] and t["duration"] > MAX_DURATION:
            continue
        results.append(
            InlineQueryResultArticle(
                id=t["url"],  # используем URL как ID для chosen_inline_result
                title=f"🎵 {t['title']}",
                description=f"👤 {t['uploader']} • ⏱ {fmt_duration(t['duration'])}",
                input_message_content=InputTextMessageContent(
                    f"🎵 <b>{esc(t['title'])}</b>\n"
                    f"👤 {esc(t['uploader'])}\n\n"
                    f"⏳ Скачиваю...\n\n"
                    f"<a href='https://t.me/{BOT_USERNAME}'>🎧 Lira Music</a>",
                    parse_mode="HTML"
                ),
            )
        )

    if not results:
        results = [InlineQueryResultArticle(
            id="no", title="❌ Ничего не найдено (или трек >10 мин)",
            input_message_content=InputTextMessageContent("❌ Ничего не найдено"),
        )]

    await update.inline_query.answer(results, cache_time=10)

async def chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Автоматически скачивает когда пользователь выбирает трек из inline"""
    result = update.chosen_inline_result
    url = result.result_id
    user_id = result.from_user.id
    inline_message_id = result.inline_message_id
    update_activity(user_id)

    if url in ("hint", "no"):
        return

    try:
        info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: download_audio(url, "youtube")
        )
        filepath = info["filename"]
        if not os.path.exists(filepath):
            return

        if os.path.getsize(filepath) / 1024 / 1024 > 50:
            cleanup(filepath)
            return

        thumb = get_thumb(info["thumbnail"]) if info.get("thumbnail") else None

        caption = (
            f"🎵 <b>{esc(info['title'])}</b>\n"
            f"👤 {esc(info['uploader'])}\n\n"
            f"<a href='https://t.me/{BOT_USERNAME}'>🎧 Lira Music</a>"
        )

        with open(filepath, "rb") as f:
            await context.bot.send_audio(
                chat_id=user_id,
                audio=f,
                title=info["title"],
                performer=info["uploader"],
                duration=info["duration"],
                thumbnail=thumb,
                caption=caption,
                parse_mode="HTML"
            )

        cleanup(filepath)

    except Exception as e:
        print(f"❌ chosen_inline: {e}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    update_activity(q.from_user.id)

    if data == "check_sub":
        await q.answer()
        if await is_subscribed(q.from_user.id, context):
            try: await q.message.delete()
            except Exception: pass
            await send_welcome(q.message.chat_id, context)
        else:
            await q.answer("❌ Ты ещё не подписан!", show_alert=True)
        return

    if data.startswith("dl|"):
        await q.answer()
        msg = await q.message.reply_text("⏳ Скачиваю...")
        await _do_download(q.message, context, data[3:], "youtube", msg)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if update.effective_chat.type in ("group", "supergroup"): return
    text = update.message.text.strip()
    user_id = update.effective_user.id
    update_activity(user_id)
    if not await is_subscribed(user_id, context):
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
        # Фильтруем треки >10 минут
        results = [r for r in results if not r["duration"] or r["duration"] <= MAX_DURATION]
        if not results:
            await msg.edit_text("❌ Ничего не найдено (или все треки >10 мин)")
            return
        keyboard = [[InlineKeyboardButton(
            f"🎵 {r['title'][:38]} [{fmt_duration(r['duration'])}]",
            callback_data=f"dl|{r['url']}"
        )] for r in results]
        await msg.edit_text(f"🎵 <b>{esc(text)}</b>\n\nВыбери трек 👇",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

async def _do_download(message, context, url, source, msg):
    try:
        info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: download_audio(url, source)
        )
        # Проверка длительности
        if info["duration"] and info["duration"] > MAX_DURATION:
            await msg.edit_text(f"❌ Трек слишком длинный (>{MAX_DURATION//60} мин)")
            return
        filepath = info["filename"]
        if not os.path.exists(filepath):
            await msg.edit_text("❌ Файл не найден"); return
        if os.path.getsize(filepath) / 1024 / 1024 > 50:
            await msg.edit_text("❌ Файл >50MB"); cleanup(filepath); return
        await msg.edit_text(f"📤 Отправляю: <b>{esc(info['title'])}</b>...", parse_mode="HTML")
        thumb = get_thumb(info["thumbnail"]) if info.get("thumbnail") else None
        caption = (
            f"🎵 <b>{esc(info['title'])}</b>\n"
            f"👤 {esc(info['uploader'])}\n\n"
            f"<a href='https://t.me/{BOT_USERNAME}'>🎧 Lira Music</a>"
        )
        with open(filepath, "rb") as f:
            await message.reply_audio(
                audio=f, title=info["title"], performer=info["uploader"],
                duration=info["duration"], thumbnail=thumb,
                caption=caption, parse_mode="HTML"
            )
        await msg.delete()
        cleanup(filepath)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

# ──────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────
async def post_init(app: Application):
    asyncio.create_task(idle_reminder_loop(app))

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден!"); return
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(post_init).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Lira Music Bot запущен!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
