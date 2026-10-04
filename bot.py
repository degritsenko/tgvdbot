import asyncio
import html
import http.cookiejar
import json
import logging
import os
import re
import struct
import sys
import shutil
import tempfile
import time
import urllib.request
from collections import defaultdict
from typing import Optional
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

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
MAX_FILE_SIZE = get_env_int("MAX_FILE_SIZE", 50 * 1024 * 1024)
MAX_PARALLEL_DOWNLOADS = get_env_int("MAX_PARALLEL_DOWNLOADS", 3)
RATE_LIMIT_REQUESTS = get_env_int("RATE_LIMIT_REQUESTS", 5)
RATE_LIMIT_WINDOW = get_env_int("RATE_LIMIT_WINDOW", 60)
INSTAGRAM_COOKIES = os.getenv("INSTAGRAM_COOKIES", "/app/cookies/instagram.txt")
THREADS_COOKIES = os.getenv("THREADS_COOKIES", "/app/cookies/threads.txt")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

X_HOSTS = {"twitter.com", "www.twitter.com", "x.com", "www.x.com", "t.co"}
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
THREADS_HOSTS = {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}

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


class UserFacingError(Exception):
    pass


# =======================
# HELPERS
# =======================


URL_RE = re.compile(r"https?://[^\s)\]]+")
MP4_URL_RE = re.compile(r"https?://[^\s\\\"'<>]+?\.mp4(?:\?[^\s\\\"'<>]*)?")
THREADS_JSON_RE = re.compile(
    r'<script type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)
THREADS_POST_CODE_RE = re.compile(r"/post/([\w-]+)")
THREADS_CRAWLER_USER_AGENT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def extract_url(text: str) -> Optional[str]:
    match = URL_RE.search(text.strip())
    if not match:
        return None
    return match.group(0)


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
    if host in THREADS_HOSTS:
        return "threads"
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


def classify_download_error(exc: Exception, platform: str) -> Optional[UserFacingError]:
    message = str(exc)
    if platform == "threads" and (
        "HTTP Error 429" in message or "Too Many Requests" in message
    ):
        return UserFacingError(
            "Threads временно ограничил запросы с сервера. Попробуй позже."
        )
    if platform == "threads" and "has no downloadable video" in message:
        return UserFacingError("В этом Threads-посте нет видео для скачивания.")
    return None


def is_threads_no_video_error(exc: Exception) -> bool:
    return "has no downloadable video" in str(exc)


def is_threads_media_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "static.cdninstagram.com":
        return False
    return "cdninstagram.com" in host or "fbcdn.net" in host


def extract_threads_video_candidates(page: str) -> list[str]:
    normalized = html.unescape(page)
    normalized = normalized.replace(r"\/", "/").replace(r"\u0026", "&").replace(r"\&", "&")

    candidates: list[str] = []
    seen: set[str] = set()
    for match in MP4_URL_RE.finditer(normalized):
        url = match.group(0)
        if url not in seen and is_threads_media_url(url):
            seen.add(url)
            candidates.append(url)
    return candidates


def extract_threads_nested_media(page: str, target_code: str) -> Optional[tuple[str, str]]:
    def find_nested_media(value) -> Optional[tuple[str, str]]:
        if isinstance(value, dict):
            if value.get("code") == target_code:
                text_info = value.get("text_post_app_info") or {}
                share_info = text_info.get("share_info") or {}
                quoted_post = share_info.get("quoted_attachment_post") or {}
                permalink = quoted_post.get("permalink")
                if isinstance(permalink, str) and parse_platform(permalink) == "threads":
                    return "post", permalink

                linked_media = text_info.get("linked_inline_media") or {}
                for version in linked_media.get("video_versions") or []:
                    video_url = version.get("url")
                    if isinstance(video_url, str) and is_threads_media_url(video_url):
                        return "video", video_url

                if linked_media.get("media_type") == 2:
                    preview = text_info.get("link_preview_attachment") or {}
                    instagram_url = preview.get("url")
                    if isinstance(instagram_url, str) and parse_platform(instagram_url) == "instagram":
                        return "post", instagram_url
                    code = linked_media.get("code")
                    if isinstance(code, str) and re.fullmatch(r"[\w-]+", code):
                        return "post", f"https://www.instagram.com/reel/{code}/"

            for nested in value.values():
                result = find_nested_media(nested)
                if result:
                    return result
        elif isinstance(value, list):
            for nested in value:
                result = find_nested_media(nested)
                if result:
                    return result
        return None

    for block in THREADS_JSON_RE.findall(page):
        try:
            data = json.loads(block)
        except (TypeError, ValueError):
            continue
        result = find_nested_media(data)
        if result:
            return result
    return None


def fetch_threads_page(url: str, crawler: bool = False) -> tuple[str, str]:
    cookies = http.cookiejar.MozillaCookieJar()
    if os.path.isfile(THREADS_COOKIES):
        cookies.load(THREADS_COOKIES, ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        if cookies and not crawler else THREADS_CRAWLER_USER_AGENT
    )
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with opener.open(request, timeout=30) as response:
        final_url = response.geturl()
        page = response.read().decode("utf-8", errors="ignore")
    return final_url, page


def resolve_threads_nested_media(url: str) -> Optional[tuple[str, str]]:
    final_url, page = fetch_threads_page(url)
    match = THREADS_POST_CODE_RE.search(final_url)
    result = extract_threads_nested_media(page, match.group(1)) if match else None
    if result is not None:
        return result
    # The browser page can be a login shell even for a public post.
    if os.path.isfile(THREADS_COOKIES):
        final_url, page = fetch_threads_page(url, crawler=True)
        match = THREADS_POST_CODE_RE.search(final_url)
        if match:
            return extract_threads_nested_media(page, match.group(1))
    return None


def download_direct_url(url: str, filepath: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response, open(filepath, "wb") as file_obj:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            file_obj.write(chunk)
            if file_obj.tell() > MAX_FILE_SIZE:
                raise UserFacingError("Видео больше лимита Telegram (50 МБ)")
    return filepath


def download_threads_fallback(url: str, filepath: str) -> Optional[str]:
    _, page = fetch_threads_page(url)

    candidates = extract_threads_video_candidates(page)
    if len(candidates) != 1:
        logger.info("threads fallback skipped: candidates=%s", len(candidates))
        return None

    return download_direct_url(candidates[0], filepath)


# =======================
# DOWNLOAD
# =======================


def build_ydl_opts(outtmpl: str, platform: str, format_selector: str) -> dict:
    ydl_opts: dict = {
        "outtmpl": outtmpl,
        "format": format_selector,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    cookies_path = {
        "instagram": INSTAGRAM_COOKIES,
        "threads": THREADS_COOKIES,
    }.get(platform)
    if cookies_path and os.path.exists(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    return ydl_opts


def download_with_format(url: str, outtmpl: str, platform: str, format_selector: str) -> str:
    ydl_opts = build_ydl_opts(outtmpl, platform, format_selector)
    # yt-dlp saves cookies on close, including after failed extraction.
    with tempfile.TemporaryDirectory(prefix="tgvdbot-cookies-") as temp_dir:
        if "cookiefile" in ydl_opts:
            cookie_copy = os.path.join(temp_dir, "cookies.txt")
            shutil.copyfile(ydl_opts["cookiefile"], cookie_copy)
            ydl_opts["cookiefile"] = cookie_copy
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info.get("_filename") or ydl.prepare_filename(info)


def download_video(url: str, user_id: int, platform: str) -> str:
    unique_id = uuid4().hex

    logger.info("[user=%s] download start platform=%s url=%s", user_id, platform, url)

    format_attempts = [
        (
            f"best[ext=mp4][filesize<={MAX_FILE_SIZE}]"
            f"/best[ext=mp4][filesize_approx<={MAX_FILE_SIZE}]"
            f"/best[filesize<={MAX_FILE_SIZE}]"
            f"/best[filesize_approx<={MAX_FILE_SIZE}]"
            "/best[ext=mp4]"
        ),
        "best[height<=1080][ext=mp4]/best[height<=1080]",
        "best[height<=720][ext=mp4]/best[height<=720]",
        "best[height<=540][ext=mp4]/best[height<=540]",
    ]

    last_error: Optional[Exception] = None
    oversize_detected = False
    nested_post_url: Optional[str] = None
    nested_media_checked = False
    for attempt_index, format_selector in enumerate(format_attempts, start=1):
        outtmpl = f"{DOWNLOAD_DIR}/video_{user_id}_{unique_id}_a{attempt_index}.%(ext)s"
        filepath: Optional[str] = None
        attempt_url = nested_post_url or url

        try:
            attempt_platform = parse_platform(attempt_url) or platform
            filepath = download_with_format(attempt_url, outtmpl, attempt_platform, format_selector)
            size = os.path.getsize(filepath)
            logger.info(
                "[user=%s] attempt=%s downloaded %.1f MB",
                user_id,
                attempt_index,
                size / 1024 / 1024,
            )

            if size <= MAX_FILE_SIZE:
                return filepath

            oversize_detected = True
            os.remove(filepath)
        except Exception as exc:
            if (
                platform == "threads"
                and attempt_url == url
                and is_threads_no_video_error(exc)
                and not nested_media_checked
            ):
                nested_media_checked = True
                nested_media: Optional[tuple[str, str]] = None
                try:
                    nested_media = resolve_threads_nested_media(url)
                except Exception as resolve_exc:
                    user_error = classify_download_error(resolve_exc, platform)
                    if user_error is not None:
                        raise user_error from resolve_exc
                    logger.info("[user=%s] threads nested media lookup failed: %s", user_id, resolve_exc)

                if nested_media and nested_media[0] == "post":
                    nested_post_url = nested_media[1]
                    logger.info("[user=%s] threads nested post found: %s", user_id, nested_post_url)
                    try:
                        filepath = download_with_format(
                            nested_post_url,
                            outtmpl,
                            parse_platform(nested_post_url) or platform,
                            format_selector,
                        )
                        size = os.path.getsize(filepath)
                        logger.info(
                            "[user=%s] attempt=%s quoted post downloaded %.1f MB",
                            user_id,
                            attempt_index,
                            size / 1024 / 1024,
                        )
                        if size <= MAX_FILE_SIZE:
                            return filepath

                        oversize_detected = True
                        os.remove(filepath)
                        continue
                    except Exception as quoted_exc:
                        exc = quoted_exc
                elif nested_media and nested_media[0] == "video":
                    linked_path = f"{DOWNLOAD_DIR}/video_{user_id}_{unique_id}_linked.mp4"
                    logger.info("[user=%s] threads linked inline video found", user_id)
                    try:
                        filepath = download_direct_url(nested_media[1], linked_path)
                        size = os.path.getsize(filepath)
                        logger.info(
                            "[user=%s] linked inline video downloaded %.1f MB",
                            user_id,
                            size / 1024 / 1024,
                        )
                        return filepath
                    except UserFacingError:
                        if os.path.exists(linked_path):
                            os.remove(linked_path)
                        raise
                    except Exception as linked_exc:
                        exc = linked_exc
                        if os.path.exists(linked_path):
                            os.remove(linked_path)

            if platform == "threads" and is_threads_no_video_error(exc):
                fallback_path = f"{DOWNLOAD_DIR}/video_{user_id}_{unique_id}_fallback.mp4"
                try:
                    filepath = download_threads_fallback(url, fallback_path)
                    if filepath:
                        size = os.path.getsize(filepath)
                        logger.info("[user=%s] threads fallback downloaded %.1f MB", user_id, size / 1024 / 1024)
                        return filepath
                except UserFacingError:
                    if os.path.exists(fallback_path):
                        os.remove(fallback_path)
                    raise
                except Exception as fallback_exc:
                    user_error = classify_download_error(fallback_exc, platform)
                    if user_error is not None:
                        raise user_error from fallback_exc
                    logger.info("[user=%s] threads fallback failed: %s", user_id, fallback_exc)
                    if os.path.exists(fallback_path):
                        os.remove(fallback_path)

            user_error = classify_download_error(exc, platform)
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
            if user_error is not None:
                logger.info("[user=%s] attempt=%s stopped: %s", user_id, attempt_index, user_error)
                raise user_error

            last_error = exc
            logger.info("[user=%s] attempt=%s failed", user_id, attempt_index)

    if oversize_detected:
        raise UserFacingError("Видео больше лимита Telegram (50 МБ)")

    if last_error is not None:
        logger.info("[user=%s] all attempts failed: %s", user_id, last_error)
        raise last_error

    raise UserFacingError("Не удалось скачать видео.")


# =======================
# HANDLERS
# =======================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.message:
        return

    await update.message.reply_text(
        "Пришли ссылку на X (Twitter), Instagram Reel или Threads, пришлю видео.\n"
        "Видео больше 50 МБ не поддерживаются."
    )


async def safe_edit_status(status_message, text: str):
    try:
        await status_message.edit_text(text)
    except Exception:
        logger.warning("Failed to edit status message")


def read_mp4_dimensions(filepath: str) -> Optional[tuple[int, int]]:
    def boxes(file_obj, start, end):
        position = start
        while position + 8 <= end:
            file_obj.seek(position)
            header = file_obj.read(8)
            if len(header) != 8:
                return
            size, kind = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                extended = file_obj.read(8)
                if len(extended) != 8:
                    return
                size = struct.unpack(">Q", extended)[0]
                header_size = 16
            elif size == 0:
                size = end - position
            if size < header_size or position + size > end:
                return
            yield kind, position + header_size, position + size
            position += size

    try:
        with open(filepath, "rb") as file_obj:
            for kind, start, end in boxes(file_obj, 0, os.fstat(file_obj.fileno()).st_size):
                if kind != b"moov":
                    continue
                for kind, start, end in boxes(file_obj, start, end):
                    if kind != b"trak":
                        continue
                    track_header = None
                    is_video = False
                    for kind, start, end in boxes(file_obj, start, end):
                        if kind == b"tkhd" and end - start in (84, 96):
                            file_obj.seek(start)
                            track_header = file_obj.read(end - start)
                        elif kind == b"mdia":
                            for child, child_start, child_end in boxes(file_obj, start, end):
                                if child == b"hdlr" and child_end - child_start >= 12:
                                    file_obj.seek(child_start + 8)
                                    is_video = file_obj.read(4) == b"vide"
                    if not is_video or track_header is None:
                        continue
                    version = track_header[0]
                    if version not in (0, 1):
                        continue
                    width, height = (round(value / 65536) for value in struct.unpack(">II", track_header[-8:]))
                    # Track display dimensions include pixel aspect ratio; the matrix carries rotation.
                    offset = 40 if version == 0 else 52
                    a, b, _, c, d, *_ = struct.unpack(">9i", track_header[offset:offset + 36])
                    if a == d == 0 and b != 0 and c != 0:
                        width, height = height, width
                    if 0 < width <= 65535 and 0 < height <= 65535:
                        return width, height
    except (OSError, ValueError, struct.error):
        logger.warning("Could not read MP4 dimensions")
    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    url = extract_url(update.message.text or "")
    if url is None:
        return

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
        dimensions = await asyncio.to_thread(read_mp4_dimensions, filepath)
        video_options = {"supports_streaming": True}
        if dimensions:
            video_options.update(width=dimensions[0], height=dimensions[1])
            logger.info("[user=%s] sending video dimensions=%sx%s", user_id, *dimensions)
        with open(filepath, "rb") as file_obj:
            await update.message.reply_video(file_obj, **video_options)

        logger.info("[user=%s] sent", user_id)

    except UserFacingError as exc:
        logger.info("[user=%s] user-facing error: %s", user_id, exc)
        await safe_edit_status(status, str(exc))
    except Exception:
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
