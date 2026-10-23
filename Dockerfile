FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libpq5 nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY web ./web

# Model caches live on the /data volume so they download once.
ENV PYTHONUNBUFFERED=1 HF_HOME=/data/hf FASTEMBED_CACHE_PATH=/data/fastembed
RUN useradd -m rilla && mkdir -p /data && chown rilla /data
USER rilla

EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.main"]
CMD ["all"]
