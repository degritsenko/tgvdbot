import os
import logging
import time
import asyncio
import sys
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def download_video(url: str, user_id: int) -> str:
    logger.info(f"Начало загрузки видео: {url} для пользователя {user_id}")
    timestamp = int(time.time())
    output_dir = "downloads"
    logger.debug(f"Создание директории для загрузок: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)
    
ydl_opts = {
    'outtmpl': f"{output_dir}/video_{user_id}_{timestamp}.%(ext)s",
    'format': 'best',
    "quiet": True,
    'noplaylist': True,
    'merge_output_format': None,  # Отключает необходимость объединения
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            prepared_filename = ydl.prepare_filename(info)
            
            base_name = os.path.splitext(prepared_filename)[0]
            possible_exts = ['.mp4', '.mkv', '.mov', '.avi']
            
            for ext in possible_exts:
                real_file = f"{base_name}{ext}"
                if os.path.exists(real_file):
                    logger.info(f"Видео успешно загружено: {real_file}")
                    return real_file
            
            if os.path.exists(prepared_filename):
                return prepared_filename
            
            raise FileNotFoundError(f"Файл не найден: {prepared_filename}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        raise

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Пользователь {update.effective_user.id} отправил команду /start")
    await update.message.reply_text("Отправь ссылку на видео из X.com")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} отправил сообщение: {url}")
    
    try:
        await update.message.reply_text("Начинаю загрузку...")
        filepath = await asyncio.to_thread(download_video, url, user_id)
        
        if os.path.getsize(filepath) > 50 * 1024 * 1024:
            raise ValueError("Файл больше 50 МБ")
            
        with open(filepath, 'rb') as video:
            await update.message.reply_video(video)
            
        os.remove(filepath)
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}")
        await update.message.reply_text(f"Ошибка: {str(e)}")

def main():
    logger.info("Запуск Telegram-бота")
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == "__main__":
    main()