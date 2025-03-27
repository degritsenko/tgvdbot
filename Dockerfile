# Сборочный этап
FROM python:3.13.2-alpine3.21 AS builder

WORKDIR /app

# Устанавливаем только необходимое для сборки
RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    ffmpeg-dev \
    && pip install --no-cache-dir --prefix=/install python-telegram-bot yt-dlp \
    && apk del .build-deps

# Финальный образ
FROM python:3.13.2-alpine3.21

# Устанавливаем только необходимые runtime зависимости
RUN apk add --no-cache ffmpeg \
    && rm -rf /var/cache/apk/*

WORKDIR /app

# Копируем только необходимые файлы из builder
COPY --from=builder /install /usr/local
COPY bot.py .

# Очистка лишних файлов
RUN find /usr/local/lib/python3.13 -name '__pycache__' -exec rm -rf {} + \
    && find /usr/local/lib/python3.13 -name '*.pyc' -exec rm -f {} + \
    && rm -rf /root/.cache

CMD ["python", "bot.py"]