# Immagine leggera con Python 3.12
FROM python:3.12-slim

# ffmpeg è necessario per riprodurre audio nel canale vocale Discord
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY . .

# Render assegna la porta tramite la variabile d'ambiente $PORT
ENV PORT=10000
EXPOSE 10000

CMD ["python", "bot.py"]
