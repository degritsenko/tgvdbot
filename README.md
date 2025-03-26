### Yet another Telegram bot. At this moment it can download and send videos from x.com.

```
docker build -t tgvdbot .

docker run -d --name tgvdbot \
  -e TELEGRAM_BOT_TOKEN="your token" \
  tgvdbot
```
