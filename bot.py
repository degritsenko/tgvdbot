import os
import logging
import time
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def download_video(url: str, user_id: int) -> str:
    timestamp = int(time.time())
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    
    ydl_opts = {
        'outtmpl': f"{output_dir}/video_{user_id}_{timestamp}.%(ext)s",
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'prefer_ffmpeg': True,
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'postprocessor_args': {
            'ffmpeg': ['-c', 'copy']
        }
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
                    return real_file
            
            if os.path.exists(prepared_filename):
                return prepared_filename
            
            raise FileNotFoundError(f"Файл не найден: {prepared_filename}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        raise

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправь ссылку на видео из X.com")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    user_id = update.effective_user.id
    
    try:
        await update.message.reply_text("Начинаю загрузку...")
        filepath = await asyncio.to_thread(download_video, url, user_id)
        
        if os.path.getsize(filepath) > 50 * 1024 * 1024:
            raise ValueError("Файл больше 50 МБ")
            
        with open(filepath, 'rb') as video:
            await update.message.reply_video(video)
            
        os.remove(filepath)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")

def main():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == "__main__":
    main()