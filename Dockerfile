FROM python:3.11-slim

# ffmpeg: audio and frames. tesseract + language packs: on-screen text in English, Hindi, Kannada,
# Tamil, Telugu and Malayalam (add more tesseract-ocr-<code> packages and OCR_LANGS to extend).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libpq5 nodejs tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin tesseract-ocr-kan \
        tesseract-ocr-tam tesseract-ocr-tel tesseract-ocr-mal \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY web ./web

# Model caches live on the /data volume so they download once.
ENV PYTHONUNBUFFERED=1 HF_HOME=/data/hf FASTEMBED_CACHE_PATH=/data/fastembed UPLOADS_DIR=/data/uploads
RUN useradd -m rilla && mkdir -p /data && chown rilla /data
USER rilla

EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.main"]
CMD ["all"]
