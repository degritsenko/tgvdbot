import asyncio
import logging
import os
import sys
import time
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import uuid4

import yt_dlp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =======================
# CONFIG
# =======================


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} должен быть числом") from exc


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

OWNER_ID = get_env_int("OWNER_ID", 0)
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
MAX_FILE_SIZE = get_env_int("MAX_FILE_SIZE", 50 * 1024 * 1024)
MAX_PARALLEL_DOWNLOADS = get_env_int("MAX_PARALLEL_DOWNLOADS", 3)
RATE_LIMIT_REQUESTS = get_env_int("RATE_LIMIT_REQUESTS", 5)
RATE_LIMIT_WINDOW = get_env_int("RATE_LIMIT_WINDOW", 60)
INSTAGRAM_COOKIES = os.getenv("INSTAGRAM_COOKIES", "/app/cookies/instagram.txt")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

X_HOSTS = {"twitter.com", "www.twitter.com", "x.com", "www.x.com", "t.co"}
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}

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
LAST_REQUESTS: dict[int, list[float]] = defaultdict(list)

STATS = {
    "total": 0,
    "instagram": 0,
    "x": 0,
    "errors": 0,
    "users": set(),
}


class UserFacingError(Exception):
    pass


# =======================
# HELPERS
# =======================


def parse_platform(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None

    host = (parsed.hostname or "").lower()
    if host in INSTAGRAM_HOSTS:
        return "instagram"
    if host in X_HOSTS:
        return "x"
    return None


def is_allowed(user_id: int) -> tuple[bool, Optional[int]]:
    now = time.time()
    LAST_REQUESTS[user_id] = [t for t in LAST_REQUESTS[user_id] if now - t < RATE_LIMIT_WINDOW]

    if len(LAST_REQUESTS[user_id]) >= RATE_LIMIT_REQUESTS:
        wait = int(RATE_LIMIT_WINDOW - (now - LAST_REQUESTS[user_id][0]))
        return False, max(1, wait)

    LAST_REQUESTS[user_id].append(now)
    return True, None


def extract_known_size(info: dict[str, Any]) -> Optional[int]:
    direct = info.get("filesize") or info.get("filesize_approx")
    if isinstance(direct, int) and direct > 0:
        return direct

    requested = info.get("requested_downloads")
    if isinstance(requested, list):
        sizes = []
        for item in requested:
            if not isinstance(item, dict):
                continue
            value = item.get("filesize") or item.get("filesize_approx")
            if isinstance(value, int) and value > 0:
                sizes.append(value)
        if sizes:
            return sum(sizes)

    return None


# =======================
# DOWNLOAD
# =======================


def build_ydl_opts(outtmpl: str, is_instagram: bool) -> dict[str, Any]:
    ydl_opts: dict[str, Any] = {
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

    return ydl_opts


def download_video(url: str, user_id: int, platform: str) -> str:
    unique_id = uuid4().hex
    outtmpl = f"{DOWNLOAD_DIR}/video_{user_id}_{unique_id}.%(ext)s"
    is_instagram = platform == "instagram"

    logger.info("[user=%s] download start platform=%s url=%s", user_id, platform, url)
    ydl_opts = build_ydl_opts(outtmpl, is_instagram)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        meta = ydl.extract_info(url, download=False)
        known_size = extract_known_size(meta)
        if known_size and known_size > MAX_FILE_SIZE:
            raise UserFacingError("Видео больше лимита Telegram (50 МБ)")

        info = ydl.extract_info(url, download=True)
        filepath = info.get("_filename") or ydl.prepare_filename(info)

    size = os.path.getsize(filepath)
    logger.info("[user=%s] downloaded %.1f MB", user_id, size / 1024 / 1024)

    if size > MAX_FILE_SIZE:
        os.remove(filepath)
        raise UserFacingError("Видео больше лимита Telegram (50 МБ)")

    STATS["total"] += 1
    STATS["users"].add(user_id)
    STATS[platform] += 1

    return filepath


# =======================
# HANDLERS
# =======================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.message:
        return

    await update.message.reply_text(
        "Пришли ссылку на X (Twitter) или Instagram Reel, пришлю видео.\n"
        "Видео больше 50 МБ не поддерживаются."
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.message or not update.effective_user:
        return

    if update.effective_user.id != OWNER_ID:
        return

    await update.message.reply_text(
        "Статистика:\n\n"
        f"Всего запросов: {STATS['total']}\n"
        f"Instagram: {STATS['instagram']}\n"
        f"X (Twitter): {STATS['x']}\n"
        f"Ошибок: {STATS['errors']}\n"
        f"Пользователей: {len(STATS['users'])}"
    )


async def safe_edit_status(status_message, text: str):
    try:
        await status_message.edit_text(text)
    except Exception:
        logger.warning("Failed to edit status message")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    url = (update.message.text or "").strip()

    platform = parse_platform(url)
    if platform is None:
        return

    allowed, wait = is_allowed(user_id)
    if not allowed:
        await update.message.reply_text(f"Подожди {wait} сек.")
        return

    status = await update.message.reply_text("Загружаю...")
    filepath: Optional[str] = None

    try:
        async with DOWNLOAD_SEMAPHORE:
            filepath = await asyncio.to_thread(download_video, url, user_id, platform)

        await safe_edit_status(status, "Отправляю...")
        with open(filepath, "rb") as file_obj:
            await update.message.reply_video(file_obj, supports_streaming=True)

        logger.info("[user=%s] sent", user_id)

    except UserFacingError as exc:
        STATS["errors"] += 1
        logger.info("[user=%s] user-facing error: %s", user_id, exc)
        await safe_edit_status(status, str(exc))
    except Exception:
        STATS["errors"] += 1
        logger.exception("[user=%s] unexpected error", user_id)
        await safe_edit_status(status, "Не удалось скачать видео. Попробуй другую ссылку позже.")
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
