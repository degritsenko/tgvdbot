import os
import sys
import time
import asyncio
import logging
import subprocess
from collections import defaultdict
from typing import Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import yt_dlp

# =======================
# CONFIG
# =======================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(50 * 1024 * 1024)))
MAX_PARALLEL_DOWNLOADS = int(os.getenv("MAX_PARALLEL_DOWNLOADS", "3"))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

INSTAGRAM_COOKIES = "/app/cookies/instagram.txt"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# =======================
# GLOBALS
# =======================

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
LAST_REQUESTS = defaultdict(list)

# =======================
# HELPERS
# =======================

def is_supported_url(url: str) -> bool:
    return (
        "twitter.com" in url
        or "x.com" in url
        or "t.co/" in url
        or "instagram.com" in url
    )

def is_allowed(user_id: int) -> tuple[bool, Optional[int]]:
    now = time.time()
    window = RATE_LIMIT_WINDOW

    LAST_REQUESTS[user_id] = [
        t for t in LAST_REQUESTS[user_id] if now - t < window
    ]

    if len(LAST_REQUESTS[user_id]) >= RATE_LIMIT_REQUESTS:
        wait = int(window - (now - LAST_REQUESTS[user_id][0]))
        return False, max(1, wait)

    LAST_REQUESTS[user_id].append(now)
    return True, None

def detect_gif(info: dict) -> bool:
    return (
        info.get("acodec") in (None, "none")
        and (info.get("duration") or 0) <= 15
    )

# =======================
# DOWNLOAD
# =======================

def download_video(url: str, user_id: int) -> tuple[str, bool]:
    ts = int(time.time())
    outtmpl = f"{DOWNLOAD_DIR}/video_{user_id}_{ts}.%(ext)s"

    platform = "instagram" if "instagram.com" in url else "x"
    logger.info(f"[user={user_id}] platform={platform} url={url}")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "best[height<=720]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "prefer_ffmpeg": True,
        "ffmpeg_location": "/usr/bin/ffmpeg",
    }

    # ✅ Instagram cookies (optional, but strongly recommended)
    if platform == "instagram":
        if os.path.exists(INSTAGRAM_COOKIES):
            ydl_opts["cookiefile"] = INSTAGRAM_COOKIES
            logger.info(f"[user={user_id}] using instagram cookies")
        else:
            logger.warning(f"[user={user_id}] instagram cookies not found")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = info.get("_filename") or ydl.prepare_filename(info)

    if not os.path.exists(filepath):
        raise FileNotFoundError("Файл не найден после загрузки")

    is_gif = detect_gif(info)
    size_mb = os.path.getsize(filepath) / (1024 * 1024)

    logger.info(
        f"[user={user_id}] downloaded {os.path.basename(filepath)} "
        f"({size_mb:.1f} MB, {'GIF' if is_gif else 'video'})"
    )

    return filepath, is_gif

# =======================
# OPTIMIZATION
# =======================

def optimize_video(path: str, user_id: int) -> str:
    size = os.path.getsize(path)

    if size <= MAX_FILE_SIZE * 0.9:
        return path

    # Fast remux
    logger.info(f"[user={user_id}] remux")
    remux_path = path.replace(".mp4", "_opt.mp4")

    subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-c", "copy", "-movflags", "+faststart", remux_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if os.path.exists(remux_path) and os.path.getsize(remux_path) <= MAX_FILE_SIZE:
        os.remove(path)
        return remux_path

    # Full recompress
    logger.info(f"[user={user_id}] recompress")
    out = path.replace(".mp4", "_compressed.mp4")

    subprocess.run(
        [
            "ffmpeg", "-y", "-i", path,
            "-vcodec", "libx264",
            "-preset", "veryfast",
            "-crf", "28",
            "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease",
            "-movflags", "+faststart",
            out,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not os.path.exists(out):
        raise RuntimeError("Ошибка перекодирования")

    os.remove(path)
    return out

# =======================
# HANDLERS
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Пришли ссылку на X (Twitter) или Instagram Reel — пришлю видео."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    url = (update.message.text or "").strip()

    if not is_supported_url(url):
        await update.message.reply_text(
            "Поддерживаются ссылки на X (Twitter) и Instagram Reels."
        )
        return

    allowed, wait = is_allowed(user_id)
    if not allowed:
        await update.message.reply_text(f"Подожди {wait} сек.")
        return

    status = await update.message.reply_text("⏳ Загружаю...")
    filepath: Optional[str] = None

    try:
        async with DOWNLOAD_SEMAPHORE:
            filepath, is_gif = await asyncio.to_thread(
                download_video, url, user_id
            )

        if os.path.getsize(filepath) > MAX_FILE_SIZE:
            await status.edit_text("🔄 Сжимаю видео...")
            filepath = await asyncio.to_thread(optimize_video, filepath, user_id)

        if os.path.getsize(filepath) > MAX_FILE_SIZE:
            raise ValueError("Видео больше 50 МБ даже после сжатия")

        await status.edit_text("📤 Отправляю...")

        with open(filepath, "rb") as f:
            if is_gif:
                await update.message.reply_animation(f)
            else:
                await update.message.reply_video(f, supports_streaming=True)

        await status.delete()
        logger.info(f"[user={user_id}] sent")

    except Exception:
        logger.exception(f"[user={user_id}] error")
        await status.edit_text(
            "❌ Не удалось загрузить видео.\n"
            "Для Instagram: возможно, требуется авторизация."
        )

    finally:
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass

# =======================
# MAIN
# =======================

def main():
    logger.info("Bot started")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()