# tgvdbot
A minimal Telegram bot for downloading videos from **X (Twitter)** and **Instagram Reels**.

Built on top of `yt-dlp`.  
No re-encoding. No aspect ratio fixes. No magic.


## Features

- Download videos from:
  - X (twitter.com / x.com)
  - Instagram Reels
- Telegram limit aware (50 MB)
- Optional Instagram cookies support
- `/stats` command (owner only)
- Rate limiting per user
- Async downloads



### docker run

```
docker run -d \
  --name tgvdbot \
  --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN=XXXX \
  -e TZ=Europe/Moscow \
  -e OWNER_ID=XXXX
  -v $(pwd)/cookies:/app/cookies:ro \
  gritsenko/tgvdbot
```

## Instagram Setup (Cookies)

Instagram may block anonymous downloads.  
For stable Instagram Reels support, cookies are **strongly recommended**.

### Steps

1. Install a browser extension:
   - **Get cookies.txt** (Chrome / Firefox)

2. Log in to **instagram.com** in your browser

3. Export cookies for `instagram.com`

4. Save the file as: instagram.txt