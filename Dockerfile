FROM python:3.13.2-alpine3.21

WORKDIR /app

RUN apk add --no-cache tzdata

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
RUN mkdir -p /app/downloads /app/cookies

ENV DOWNLOAD_DIR=/app/downloads

CMD ["python", "-u", "bot.py"]
