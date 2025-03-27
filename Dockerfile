# Сборочный этап
FROM python:3.13.2-alpine3.21 AS builder

WORKDIR /app

# Устанавливаем только необходимое для сборки
RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    ffmpeg-dev \
    && pip install --no-cache-dir --prefix=/install python-telegram-bot yt-dlp \
    && apk del --purge .build-deps

# Финальный образ
FROM alpine:3.21

# Устанавливаем минимально необходимые зависимости
RUN apk add --no-cache \
    python3 \
    ffmpeg \
    && rm -rf /var/cache/apk/*

WORKDIR /app

# Копируем только необходимые файлы из builder
COPY --from=builder /install /usr/local
COPY bot.py .

# Удаляем все лишнее
RUN find /usr/local/lib/python3.13 -name '__pycache__' -exec rm -rf {} + \
    && find /usr/local/lib/python3.13 -name '*.pyc' -exec rm -f {} + \
    && rm -rf /root/.cache \
    && rm -rf /usr/include \
    && rm -rf /usr/lib/pkgconfig \
    && rm -rf /usr/share/man \
    && rm -rf /var/cache/apk/*

CMD ["python3", "bot.py"]