# Python 3.11 matches the README's recommendation (well-supported faster-whisper/
# ctranslate2 wheels; no need for the audioop-lts back-port that 3.13 requires locally).
FROM python:3.11-slim

# ffmpeg is required by pydub and faster-whisper for decoding/exporting/cutting audio.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py .

# Log output should appear immediately in `docker logs`, not sit in Python's stdout buffer.
ENV PYTHONUNBUFFERED=1
# The faster-whisper model download (and its Hugging Face cache) lands inside the
# already-persisted data volume instead of the container's writable layer, so it isn't
# re-downloaded every time the container is recreated.
ENV HF_HOME=/app/data/hf_cache

EXPOSE 8000

# /app/data holds the sqlite DB, downloaded/cut audio, transcripts, cover art and the
# whisper model cache; /app/app/config/ad_jingles holds user-added jingle snippets.
# Both should be mounted as volumes (see docker-compose.yml) so they survive image
# rebuilds, not just container restarts.
VOLUME ["/app/data", "/app/app/config/ad_jingles"]

CMD ["python", "run.py"]
