# tgvdbot

docker build -t tgvdbot .

docker run -d --name tgvdbot \
  -e TELEGRAM_BOT_TOKEN="your token" \
  tgvdbot
