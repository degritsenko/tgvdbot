import os
import asyncio
import logging
import yt_dlp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

def download_video(url: str) -> dict:
    ydl_opts = {
        'format': 'bv*[filesize_approx<50M]/best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'noplaylist': True,
        'quiet': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = info.get('_filename') or ydl.prepare_filename(info)

        duration = info.get('duration') or 0
        acodec = info.get('acodec')
        is_gif = (acodec in (None, 'none')) and duration <= 15

        return {
            'path': path,
            'title': info.get('title', 'video'),
            'duration': duration,
            'is_gif': is_gif,
        }

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Пришли ссылку на пост из X (Twitter), и я скачаю видео.")

@dp.message(F.text.contains("x.com") | F.text.contains("twitter.com"))
async def handle_link(message: types.Message):
    status_msg = await message.answer("⏳ Загружаю видео...")
    file_path = None

    try:
        data = await asyncio.to_thread(download_video, message.text)
        file_path = data['path']

        media = FSInputFile(file_path)

        if data['is_gif']:
            await message.answer_animation(media)
        else:
            await message.answer_video(
                media,
                caption=data['title'],
                duration=data['duration'],
            )

    except Exception as e:
        logging.error(f"Error: {e}")
        await message.answer(
            "❌ Ошибка загрузки. Видео может быть слишком большим или недоступным."
        )
    finally:
        await status_msg.delete()
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

async def main():
    os.makedirs('downloads', exist_ok=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())