import os
import sys
import time
import asyncio
import logging
from collections import defaultdict
from typing import Optional
import yt_dlp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)



# =======================
# CONFIG
# =======================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

MAX_PARALLEL_DOWNLOADS = 3
RATE_LIMIT_REQUESTS = 5
RATE_LIMIT_WINDOW = 60

INSTAGRAM_COOKIES = "/app/cookies/instagram.txt"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =======================
# LOGGING
# =======================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)
logging.getLogger("telegram.ext").setLevel(logging.INFO)
logging.getLogger("yt_dlp").setLevel(logging.ERROR)

# =======================
# GLOBALS
# =======================

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
LAST_REQUESTS = defaultdict(list)

STATS = {
    "total": 0,
    "instagram": 0,
    "x": 0,
    "errors": 0,
    "users": set(),
}

# =======================
# HELPERS
# =======================

def is_supported_url(url: str) -> bool:
    return any(x in url for x in ("twitter.com", "x.com", "t.co/", "instagram.com"))

def is_allowed(user_id: int) -> tuple[bool, Optional[int]]:
    now = time.time()
    LAST_REQUESTS[user_id] = [t for t in LAST_REQUESTS[user_id] if now - t < RATE_LIMIT_WINDOW]

    if len(LAST_REQUESTS[user_id]) >= RATE_LIMIT_REQUESTS:
        wait = int(RATE_LIMIT_WINDOW - (now - LAST_REQUESTS[user_id][0]))
        return False, max(1, wait)

    LAST_REQUESTS[user_id].append(now)
    return True, None

# =======================
# DOWNLOAD
# =======================

def download_video(url: str, user_id: int) -> str:
    ts = int(time.time())
    outtmpl = f"{DOWNLOAD_DIR}/video_{user_id}_{ts}.%(ext)s"

    is_instagram = "instagram.com" in url
    platform = "instagram" if is_instagram else "x"

    logger.info(f"[user={user_id}] download start instagram={is_instagram} url={url}")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    if is_instagram and os.path.exists(INSTAGRAM_COOKIES):
        ydl_opts["cookiefile"] = INSTAGRAM_COOKIES
        logger.info(f"[user={user_id}] using instagram cookies")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = info.get("_filename") or ydl.prepare_filename(info)

    size = os.path.getsize(filepath)
    logger.info(f"[user={user_id}] downloaded {size / 1024 / 1024:.1f} MB")

    STATS["total"] += 1
    STATS["users"].add(user_id)
    STATS[platform] += 1

    if size > MAX_FILE_SIZE:
        os.remove(filepath)
        raise ValueError("Файл больше 50 МБ")

    return filepath

# =======================
# HANDLERS
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Пришли ссылку на X (Twitter) или Instagram Reel — пришлю видео.\n"
        "⚠️ Видео больше 50 МБ не поддерживаются."
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    await update.message.reply_text(
        "📊 Статистика:\n\n"
        f"Всего запросов: {STATS['total']}\n"
        f"Instagram: {STATS['instagram']}\n"
        f"X (Twitter): {STATS['x']}\n"
        f"Ошибок: {STATS['errors']}\n"
        f"Пользователей: {len(STATS['users'])}"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    url = (update.message.text or "").strip()

    if not is_supported_url(url):
        return

    allowed, wait = is_allowed(user_id)
    if not allowed:
        await update.message.reply_text(f"Подожди {wait} сек.")
        return

    status = await update.message.reply_text("⏳ Загружаю...")
    filepath: Optional[str] = None

    try:
        async with DOWNLOAD_SEMAPHORE:
            filepath = await asyncio.to_thread(download_video, url, user_id)

        await status.edit_text("📤 Отправляю...")
        with open(filepath, "rb") as f:
            await update.message.reply_video(f, supports_streaming=True)

        logger.info(f"[user={user_id}] sent")

    except Exception as e:
        STATS["errors"] += 1
        logger.exception(f"[user={user_id}] error")
        await status.edit_text(str(e))

    finally:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)

# =======================
# MAIN
# =======================

def main():
    logger.info("Bot started")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()