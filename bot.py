import os
import sys
import time
import asyncio
import logging
import subprocess
import re
from typing import Optional, Tuple
from collections import defaultdict
from datetime import datetime, timedelta

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
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(50 * 1024 * 1024)))  # 50 MB
MAX_PARALLEL_DOWNLOADS = int(os.getenv("MAX_PARALLEL_DOWNLOADS", "3"))
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("❌ TELEGRAM_BOT_TOKEN не задан")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =======================
# LOGGING
# =======================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yt_dlp").setLevel(logging.WARNING)

# =======================
# RATE LIMITER
# =======================

class RateLimiter:
    """Ограничитель частоты запросов на основе скользящего окна."""
    
    def __init__(self, max_requests: int, window_seconds: int):
        self.requests = defaultdict(list)
        self.max_requests = max_requests
        self.window = timedelta(seconds=window_seconds)
    
    def is_allowed(self, user_id: int) -> Tuple[bool, Optional[int]]:
        """
        Проверяет, разрешён ли запрос.
        
        Returns:
            (allowed, wait_seconds): разрешён ли запрос и сколько ждать до следующего
        """
        now = datetime.now()
        
        # Очищаем старые запросы
        self.requests[user_id] = [
            req_time for req_time in self.requests[user_id]
            if now - req_time < self.window
        ]
        
        if len(self.requests[user_id]) >= self.max_requests:
            # Вычисляем время до освобождения слота
            oldest_request = min(self.requests[user_id])
            wait_until = oldest_request + self.window
            wait_seconds = int((wait_until - now).total_seconds())
            return False, max(1, wait_seconds)
        
        self.requests[user_id].append(now)
        return True, None

# =======================
# GLOBALS
# =======================

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)

# =======================
# HELPERS
# =======================

def is_twitter_url(url: str) -> bool:
    """Проверяет, является ли URL ссылкой на твит."""
    patterns = [
        r'https?://(www\.)?(twitter\.com|x\.com)/\w+/status/\d+',
        r'https?://(www\.)?t\.co/\w+',
        r'https?://(www\.)?(twitter\.com|x\.com)/i/web/status/\d+',
    ]
    return any(re.match(pattern, url, re.IGNORECASE) for pattern in patterns)

def check_ffmpeg() -> bool:
    """Проверяет доступность ffmpeg."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=5
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False

# =======================
# DOWNLOAD & PROCESSING
# =======================

def detect_gif(info: dict) -> bool:
    """Определяет, является ли видео GIF-анимацией."""
    formats = info.get("formats", [])
    duration = info.get("duration") or 0
    
    has_audio = any(
        f.get("acodec") not in (None, "none")
        for f in formats
    )
    
    # GIF = короткое видео без звука
    return (not has_audio) and duration <= 15

def download_video(url: str, user_id: int) -> Tuple[str, bool, dict]:
    """
    Скачивает видео из X.com с умным выбором качества.

    Returns:
        (filepath, is_gif, info): путь к файлу, флаг GIF, метаданные
    """
    timestamp = int(time.time())
    outtmpl = f"{DOWNLOAD_DIR}/video_{user_id}_{timestamp}.%(ext)s"

    logger.info(f"[user={user_id}] Загрузка: {url}")

    # Сначала пробуем скачать версию, которая сразу подойдёт
    # Это быстрее, чем качать HD и потом перекодировать
    ydl_opts_smart = {
        "outtmpl": outtmpl,
        "format": "best[filesize<50M]/best[height<=720]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "prefer_ffmpeg": True,
        "ffmpeg_location": "/usr/bin/ffmpeg",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts_smart) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            
            if not os.path.exists(filepath):
                raise FileNotFoundError("Файл не найден после загрузки")
            
            size_mb = os.path.getsize(filepath) / (1024*1024)
            is_gif = detect_gif(info)
            duration = info.get("duration") or 0
            
            logger.info(
                f"[user={user_id}] ✅ Загружено сразу подходящее: "
                f"{os.path.basename(filepath)} ({size_mb:.1f} MB, "
                f"{'GIF' if is_gif else 'видео'}, {duration:.1f}s)"
            )
            
            return filepath, is_gif, info
            
    except Exception as e:
        logger.info(f"[user={user_id}] Не удалось найти <50MB версию: {e}")
    
    # Если не получилось - качаем лучшее качество
    # Потом будем перекодировать если нужно
    ydl_opts_best = ydl_opts_smart.copy()
    ydl_opts_best["format"] = "bestvideo+bestaudio/best"
    
    with yt_dlp.YoutubeDL(ydl_opts_best) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)

    if not os.path.exists(filepath):
        raise FileNotFoundError("Файл не найден после загрузки")

    size_mb = os.path.getsize(filepath) / (1024*1024)
    is_gif = detect_gif(info)
    duration = info.get("duration") or 0

    logger.info(
        f"[user={user_id}] Загружено HD: {os.path.basename(filepath)} "
        f"({size_mb:.1f} MB, {'GIF' if is_gif else 'видео'}, {duration:.1f}s)"
    )

    return filepath, is_gif, info

def remux_video(input_path: str, user_id: int) -> str:
    """
    Быстрая перепаковка видео без перекодирования.
    Используется для оптимизации контейнера (faststart).
    Работает в 100+ раз быстрее полного перекодирования.
    """
    output_path = input_path.replace(".mp4", "_remux.mp4")

    logger.info(f"[user={user_id}] Оптимизация контейнера (remux)...")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-c", "copy",  # Копируем потоки без перекодирования
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=30
    )

    if result.returncode != 0 or not os.path.exists(output_path):
        logger.warning(f"[user={user_id}] Remux не удался, используем оригинал")
        return input_path

    logger.info(f"[user={user_id}] ✅ Remux выполнен за <2 сек")
    return output_path

def recompress_video(input_path: str, user_id: int, target_size: int = MAX_FILE_SIZE) -> str:
    """
    Полное перекодирование видео для уменьшения размера.
    МЕДЛЕННО! Используется только когда файл >50 МБ.
    """
    output_path = input_path.replace(".mp4", "_compressed.mp4")

    logger.info(f"[user={user_id}] ⚠️ Полное перекодирование (медленно)...")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vcodec", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-movflags", "+faststart",
        "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease",
        output_path,
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=300
    )

    if result.returncode != 0 or not os.path.exists(output_path):
        logger.error(f"[user={user_id}] Ошибка ffmpeg: {result.stderr.decode()[:200]}")
        raise RuntimeError("Ошибка перекодирования видео")

    new_size = os.path.getsize(output_path)
    logger.info(f"[user={user_id}] Сжато до {new_size / (1024*1024):.1f} MB")

    return output_path

# =======================
# HANDLERS
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user_id = update.effective_user.id
    logger.info(f"[user={user_id}] /start")
    
    await update.message.reply_text(
        "🤖 **Бот для скачивания видео из X (Twitter)**\n\n"
        "📝 Отправьте ссылку на пост с видео:\n"
        "`https://x.com/username/status/123...`\n\n"
        "✨ Возможности:\n"
        "• Автоматическое определение GIF/видео\n"
        "• Сжатие больших файлов (>50 МБ)\n"
        "• Поддержка различных форматов\n\n"
        f"⏱️ Лимит: {RATE_LIMIT_REQUESTS} запросов в {RATE_LIMIT_WINDOW}с\n"
        "ℹ️ /help — справка",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help."""
    await update.message.reply_text(
        "ℹ️ **Справка**\n\n"
        "**Как использовать:**\n"
        "1. Откройте твит с видео в X.com\n"
        "2. Скопируйте ссылку\n"
        "3. Отправьте её боту\n\n"
        "**Поддерживаемые форматы:**\n"
        "• x.com/user/status/...\n"
        "• twitter.com/user/status/...\n"
        "• t.co/...\n\n"
        "**Ограничения:**\n"
        "• Максимальный размер: 50 МБ\n"
        f"• Rate limit: {RATE_LIMIT_REQUESTS} запросов/{RATE_LIMIT_WINDOW}с\n"
        f"• Параллельно: {MAX_PARALLEL_DOWNLOADS} загрузок\n\n"
        "❓ **Проблемы?**\n"
        "• Твит должен быть публичным\n"
        "• В твите должно быть видео\n"
        "• Твит не должен быть удалён",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик входящих ссылок."""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    url = (update.message.text or "").strip()

    logger.info(f"[user={user_id}/@{username}] Получена ссылка: {url}")

    # Валидация URL
    if not is_twitter_url(url):
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте корректную ссылку на пост из X.com или Twitter.\n\n"
            "Примеры:\n"
            "• https://x.com/user/status/123...\n"
            "• https://twitter.com/user/status/123..."
        )
        return

    # Rate limiting
    allowed, wait_seconds = rate_limiter.is_allowed(user_id)
    if not allowed:
        await update.message.reply_text(
            f"⏳ Слишком много запросов.\n"
            f"Подождите {wait_seconds} секунд."
        )
        return

    status_msg = await update.message.reply_text("⏳ Загружаю видео...")

    filepath: Optional[str] = None

    try:
        # Загрузка с семафором
        async with DOWNLOAD_SEMAPHORE:
            filepath, is_gif, info = await asyncio.to_thread(
                download_video, url, user_id
            )

        file_size = os.path.getsize(filepath)
        original_size_mb = file_size / (1024 * 1024)

        # Обработка больших файлов
        if file_size > MAX_FILE_SIZE:
            await status_msg.edit_text(
                f"🔄 Файл большой ({original_size_mb:.1f} MB), сжимаю...\n"
                "⏱️ Это может занять 1-3 минуты"
            )
            
            filepath_compressed = await asyncio.to_thread(
                recompress_video, filepath, user_id
            )
            
            os.remove(filepath)
            filepath = filepath_compressed
            file_size = os.path.getsize(filepath)
            
            if file_size > MAX_FILE_SIZE:
                raise ValueError(
                    f"Видео слишком большое ({original_size_mb:.1f} MB) "
                    f"и не удалось сжать до 50 МБ"
                )
        
        elif file_size > MAX_FILE_SIZE * 0.9:
            # Файл близок к лимиту (45-50 МБ), делаем быстрый remux
            # для оптимизации контейнера
            await status_msg.edit_text("⚡ Оптимизирую контейнер...")
            
            filepath_remux = await asyncio.to_thread(remux_video, filepath, user_id)
            
            if filepath_remux != filepath:
                os.remove(filepath)
                filepath = filepath_remux
                file_size = os.path.getsize(filepath)

        # Отправка
        await status_msg.edit_text("📤 Отправляю...")
        
        with open(filepath, "rb") as f:
            if is_gif:
                await update.message.reply_animation(f)
            else:
                await update.message.reply_video(
                    f,
                    supports_streaming=True
                )

        await status_msg.delete()
        logger.info(f"[user={user_id}] ✅ Успешно отправлено")

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e).lower()
        if "private" in error_msg or "protected" in error_msg:
            await status_msg.edit_text("🔒 Этот твит из приватного аккаунта")
        elif "not found" in error_msg or "404" in error_msg:
            await status_msg.edit_text("❌ Твит не найден или удалён")
        elif "no video" in error_msg or "no formats" in error_msg:
            await status_msg.edit_text("❌ В этом посте нет видео")
        else:
            await status_msg.edit_text(
                "❌ Не удалось загрузить видео.\n"
                "Возможно, твит недоступен или содержит только изображения."
            )
        logger.error(f"[user={user_id}] DownloadError: {e}")

    except ValueError as e:
        await status_msg.edit_text(f"📦 {str(e)}")
        logger.warning(f"[user={user_id}] ValueError: {e}")

    except subprocess.TimeoutExpired:
        await status_msg.edit_text("⏱️ Превышен таймаут обработки видео")
        logger.error(f"[user={user_id}] Timeout при обработке")

    except Exception as e:
        await status_msg.edit_text(
            "❌ Произошла внутренняя ошибка.\n"
            "Попробуйте позже или используйте другую ссылку."
        )
        logger.exception(f"[user={user_id}] Неожиданная ошибка: {e}")

    finally:
        # Очистка временных файлов
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.debug(f"[user={user_id}] Временный файл удалён")
            except OSError as e:
                logger.warning(f"[user={user_id}] Не удалось удалить файл: {e}")

# =======================
# MAIN
# =======================

def main():
    """Точка входа."""
    logger.info("=" * 50)
    logger.info("🚀 Запуск Telegram-бота для X.com")
    logger.info("=" * 50)
    
    # Проверка ffmpeg
    if not check_ffmpeg():
        logger.error("❌ ffmpeg не найден! Установите ffmpeg.")
        sys.exit(1)
    logger.info("✅ ffmpeg найден")
    
    # Создание приложения
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info(f"✅ Бот запущен (max parallel: {MAX_PARALLEL_DOWNLOADS})")
    logger.info(f"✅ Rate limit: {RATE_LIMIT_REQUESTS} req/{RATE_LIMIT_WINDOW}s")
    
    # Запуск
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n👋 Бот остановлен")
    except Exception as e:
        logger.exception(f"💥 Критическая ошибка: {e}")
        sys.exit(1)