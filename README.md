### Yet another Telegram bot. It can download and send instagram reels and videos from x.com.

```
docker build -t tgvdbot .

docker run -d --name tgvdbot \
  -e TELEGRAM_BOT_TOKEN="your token" \
  tgvdbot
```
