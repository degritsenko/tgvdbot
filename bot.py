import os
import logging
import time
import asyncio
import sys
import subprocess

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
# LOGGING
# =======================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# =======================
# CONFIG
# =======================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
DOWNLOAD_DIR = "downloads"

# =======================
# DOWNLOAD
# =======================

def download_video(url: str, user_id: int) -> str:
    timestamp = int(time.time())
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    logger.info(f"[user_id={user_id}] Начало загрузки видео: {url}")

    ydl_opts = {
        "outtmpl": f"{DOWNLOAD_DIR}/video_{user_id}_{timestamp}.%(ext)s",
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "prefer_ffmpeg": True,
        "ffmpeg_location": "/usr/bin/ffmpeg",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = info.get("_filename") or ydl.prepare_filename(info)

    if not os.path.exists(filepath):
        logger.error(f"[user_id={user_id}] Файл не найден после загрузки")
        raise FileNotFoundError("Файл не найден после загрузки")

    logger.info(f"[user_id={user_id}] Видео загружено: {filepath}")
    return filepath

# =======================
# REENCODE
# =======================

def recompress_video(input_path: str, user_id: int) -> str:
    output_path = input_path.replace(".mp4", "_reencoded.mp4")

    logger.info(f"[user_id={user_id}] Перекодирование видео через ffmpeg")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vcodec", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-movflags", "+faststart",
        output_path,
    ]

    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not os.path.exists(output_path):
        logger.error(f"[user_id={user_id}] Ошибка перекодирования")
        raise RuntimeError("Ошибка перекодирования видео")

    return output_path

# =======================
# HANDLERS
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"[user_id={user_id}] Команда /start")
    await update.message.reply_text(
        "Отправь ссылку на пост из X.com — я скачаю видео."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    url = update.message.text

    logger.info(f"[user_id={user_id}] Получена ссылка: {url}")

    await update.message.reply_text("Загружаю видео...")

    filepath = None

    try:
        filepath = await asyncio.to_thread(download_video, url, user_id)

        size = os.path.getsize(filepath)
        size_mb = size / (1024 * 1024)
        logger.info(f"[user_id={user_id}] Размер после загрузки: {size_mb:.1f} MB")

        if size > MAX_FILE_SIZE:
            filepath_new = await asyncio.to_thread(
                recompress_video, filepath, user_id
            )
            os.remove(filepath)
            filepath = filepath_new

            size = os.path.getsize(filepath)
            size_mb = size / (1024 * 1024)
            logger.info(
                f"[user_id={user_id}] Размер после перекодирования: {size_mb:.1f} MB"
            )

        if size > MAX_FILE_SIZE:
            logger.warning(
                f"[user_id={user_id}] Файл всё ещё больше лимита Telegram"
            )
            raise ValueError("Файл больше 50 МБ")

        with open(filepath, "rb") as video:
            await update.message.reply_video(video)

        logger.info(f"[user_id={user_id}] Видео успешно отправлено")

    except Exception as e:
        logger.exception(f"[user_id={user_id}] Ошибка обработки видео")
        await update.message.reply_text(
            "❌ Не удалось отправить видео (слишком большое или ошибка обработки)."
        )
    finally:
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"[user_id={user_id}] Временный файл удалён")
            except OSError:
                pass

# =======================
# MAIN
# =======================

def main():
    logger.info("Запуск Telegram-бота")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()