### Yet another Telegram bot. It can download and send instagram reels and videos from x.com.

```
docker build -t tgvdbot .

docker run -d \
  --name tgvdbot \
  --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN=XXXX \
  -e TZ=Europe/Moscow \
  -e OWNER_ID=your telegram id
  -v $(pwd)/cookies:/app/cookies:ro \
  gritsenko/tgvdbot
```
