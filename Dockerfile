# Lightweight image with Python 3.12
FROM python:3.12-slim

# ffmpeg is required to play audio in the Discord voice channel
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY . .

# Render assigns the port via the $PORT environment variable
ENV PORT=10000
EXPOSE 10000

CMD ["python", "bot.py"]
