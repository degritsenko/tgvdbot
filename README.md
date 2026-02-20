# tgvdbot
A minimal Telegram bot for downloading videos from **X (Twitter)** and **Instagram Reels**.

Built on top of `yt-dlp`.

## Features
- Download videos from:
  - X (`twitter.com` / `x.com` / `t.co`)
  - Instagram Reels
- Telegram limit aware (50 MB by default)
- Optional Instagram cookies support
- `/stats` command (owner only)
- Rate limiting per user
- Async downloads with parallel limit

## Environment variables
- `TELEGRAM_BOT_TOKEN` (required)
- `OWNER_ID` (optional, default `0`)
- `DOWNLOAD_DIR` (optional, default `downloads`)
- `INSTAGRAM_COOKIES` (optional, default `/app/cookies/instagram.txt`)
- `MAX_FILE_SIZE` (optional, bytes, default `52428800`)
- `MAX_PARALLEL_DOWNLOADS` (optional, default `3`)
- `RATE_LIMIT_REQUESTS` (optional, default `5`)
- `RATE_LIMIT_WINDOW` (optional, seconds, default `60`)

## docker run
```bash
docker run -d \
  --name tgvdbot \
  --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN=XXXX \
  -e OWNER_ID=XXXX \
  -e TZ=Europe/Moscow \
  -v "$(pwd)/cookies:/app/cookies:ro" \
  -v "$(pwd)/downloads:/app/downloads" \
  gritsenko/tgvdbot
```

## Instagram setup (cookies)
Instagram may block anonymous downloads.
For stable Reels support, cookies are strongly recommended.

1. Install browser extension `Get cookies.txt`.
2. Log in to `instagram.com`.
3. Export cookies for `instagram.com`.
4. Save file to `cookies/instagram.txt`.

## docker-compose
Use `docker-compose.yaml` and set `TELEGRAM_BOT_TOKEN` / `OWNER_ID` in your shell or `.env`.
