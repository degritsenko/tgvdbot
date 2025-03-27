FROM python:3.13.2-alpine3.21

WORKDIR /app

#RUN apk add --no-cache \
#    ffmpeg \
#    && rm -rf /var/cache/apk/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .


CMD ["python", "bot.py"]
