# Сборочный этап
FROM python:3.13.2-alpine3.21 AS builder

WORKDIR /app

# Устанавливаем только необходимые зависимости
RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    ffmpeg-dev \
    && pip install --no-cache-dir --prefix=/install python-telegram-bot yt-dlp

# Финальный образ
FROM python:3.13.2-alpine3.21

# Устанавливаем только runtime зависимости
RUN apk add --no-cache \
    ffmpeg \
    && rm -rf /var/cache/apk/*

WORKDIR /app

# Копируем только необходимые файлы из сборочного образа
COPY --from=builder /install /usr/local
COPY bot.py .

CMD ["python", "bot.py"]