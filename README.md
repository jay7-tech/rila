# Rilla

Rilla turns the videos you watch into a map of offers, places and live local conditions.

Give it anything: a reel or short link, an uploaded video or audio clip, a screenshot, or pasted text, in any language. Rilla listens (language detected automatically), reads the words on screen, extracts what is on offer (venue, price, start and end time), checks every fact against a quote from the source, finds it on the map, and brings it back when you are near or when it is about to end.

## How it works

```
 link / video / audio / screenshot / text            Part 2 (in progress): map, remember, retrieve, recommend
          |
   sources/  safe link check, upload check, text
          |
   perception/  speech (Whisper, auto language) + frames -> de-duplicate -> OCR (Tesseract)
          |
   core/  VideoDocument: one timeline of caption, speech, on-screen text (with timestamps)
          |
   services/extract  LLM -> items + signals, each with a verbatim quote -> grounding check
          |
   services/process  geocode (English name) -> de-duplicate -> save item + evidence + document
```

- `app/core/` the timeline model, Unicode-safe text helpers (Indic scripts), chunking, grounding
- `app/perception/` ffprobe/ffmpeg, frame de-duplication, OCR, speech
- `app/sources/` link validation (public hosts only), uploads and pasted text
- `app/services/` ingest, extract, geocode, process, query, worker
- `app/recs/` offer windows and the "near me" ranking
- `app/signals/` freshness decay and multi-source confirmation
- `app/evalkit/` and `eval/` labelled cases, precision/recall/F1, grounding rate, cached LLM runs
- `app/cli.py` command line for running and testing without any chat app
- `app/api.py` REST API and web app hosting, `app/bot.py` optional Telegram bot, `web/` the installable web app

## Run with Docker

```
copy .env.example .env        # set GROQ_API_KEY and POSTGRES_PASSWORD (BOT_TOKEN is optional)
docker compose build
docker compose up -d
docker compose exec api python -m app.cli doctor        # checks every dependency
docker compose exec api python -m app.cli make-user "Your Name"   # prints your sign-in link
```

Open the link. Add a video link, upload a file, paste a screenshot (Ctrl+V anywhere on the page) or paste text. On Android, open the app in Chrome, choose Install, and "Share to Rilla" appears in other apps' share sheets (iPhone does not support this yet; paste the link instead).

After the first build, code, page and migration changes need only `docker compose restart api worker` (the override file mounts them). Rebuild only when `requirements.txt` or the `Dockerfile` change.

## Try it from the command line

```
docker compose exec api python -m app.cli read "https://www.youtube.com/shorts/..." 
docker compose exec api python -m app.cli read /data/some_video.mp4 --no-llm     # perception only, no API key needed
docker compose exec api python -m app.cli read "Free dosa at MTR this Sunday near Lalbagh"
```

`read` prints what was heard and seen (with timestamps) and what was extracted, and writes nothing to the database.

## Measure it

```
docker compose exec api python -m app.cli capture 12     # save processed document 12 as a draft case in eval/cases/
# watch the video, correct "expected" by hand, set "labeled": true
docker compose exec api python -m app.cli eval --save   # precision, recall, F1, grounded rate; answers cached
```

See `eval/README.md`. Synthetic cases are included only to prove the harness works; they are never counted.

## Let testers in

1. Set `INVITE_CODE=somesecret` in `.env`, then `docker compose up -d`. Testers sign up in the web app with that code.
2. Give the web app a public address: `docker compose --profile public up -d`, read the `https://...trycloudflare.com` address from `docker compose logs tunnel`, and put it in `PUBLIC_URL`. The address changes each time the tunnel restarts.

## Tests

Quick, no database server (SQLite):

```
pip install -r requirements-dev.txt
pytest
```

Against the real database (PostGIS + pgvector), inside Docker, on a throwaway database:

```
docker compose exec db psql -U rilla -d postgres -c "CREATE DATABASE rilla_test"
docker compose exec db psql -U rilla -d rilla_test -c "CREATE EXTENSION IF NOT EXISTS postgis" -c "CREATE EXTENSION IF NOT EXISTS vector"
docker compose --profile test run --rm tests
```

## Security notes

- Links must be public addresses on the allowed video sites; private and internal addresses are refused.
- Uploads are identified with ffprobe (not the file name), stored under random names, size-limited, and deleted as soon as they are read. Raw video is never kept; only the derived timeline and facts are.
- Web sign-up is closed unless you set an invite code (or `OPEN_SIGNUP=1`); sign-up and submissions are rate limited.
- Never commit `.env` or cookie files.

## Known limits

- Speech quality on Kannada and other Indian languages depends on the Whisper model size; `small` is fast but weaker. Measure with `eval` before changing `WHISPER_MODEL`.
- Tesseract reads clean overlay text well but struggles with stylised fonts; a vision-model option is planned.
- `/search` embeddings currently use an English model (bge-small-en-v1.5); a multilingual comparison is a planned stage.
- Instagram can block the downloader. Public YouTube shorts work without a login. Never use a cookie account you cannot lose.
- Geocoding uses public Nominatim (1 request per second, cached); an own place database (Overture) is planned.
- Each worker processes one video at a time; run more workers for more throughput.

The full architecture and roadmap is the "Rilla: Architecture and Build Roadmap" document.
