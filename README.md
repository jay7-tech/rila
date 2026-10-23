# Rilla

Rilla turns the reels you watch into a map of offers, places and live local conditions.

Forward an Instagram reel or YouTube short to the Telegram bot (or paste the link in the web app). Rilla reads the caption and speech, extracts what is on offer (venue, price, start and end time), finds it on the map, and brings it back when you are near or when it is about to end. Same-day reports about crowds, weather and closures are checked against each other and shown only when independent sources agree.

## How it works

```
Telegram bot ─┐                       ┌─ Postgres + PostGIS + pgvector (SQLite fallback)
Web app (PWA) ┼─ FastAPI ─ job queue ─┤   users, items, signals, jobs, geocache
              ┘            worker ────┘
                             │  yt-dlp → captions or Whisper → Groq LLM → Nominatim → dedupe → save
```

- `app/services/` ingest, extract, geocode, process, query, worker
- `app/recs/` offer windows and the "near me" ranking (distance 45%, time left 30%, interests 20%, confidence 5%)
- `app/signals/` freshness decay, multi-source confirmation, briefings
- `app/api.py` REST API and web app hosting, `app/bot.py` Telegram bot
- `web/` the installable web app (Leaflet map on OpenStreetMap)

## Run with Docker (recommended)

```
copy .env.example .env      # fill in BOT_TOKEN, GROQ_API_KEY, POSTGRES_PASSWORD
docker compose up --build -d
docker compose logs -f
```

This starts Postgres (PostGIS + pgvector), applies migrations once, then runs the API/web app, the Telegram bot and a reel worker as separate containers. Add throughput with `docker compose up -d --scale worker=3`. Data lives in Docker volumes and survives restarts.

Message your bot `/start`, forward a reel, then `/web` for your map link.

## Quick run without Docker (SQLite fallback)

Double-click `scripts\run_local.bat`. It runs everything in one process with a local SQLite file. Good for a quick try; use Docker for real testing.

## Let testers in

1. Set `INVITE_CODE=somesecret` in `.env`. Testers join with `https://t.me/<your_bot>?start=somesecret`.
2. Give the web app a public address: `docker compose --profile public up -d`, then read the `https://...trycloudflare.com` address from `docker compose logs tunnel`. Put it in `PUBLIC_URL` and restart the app so `/web` links use it. The address changes each time the tunnel restarts.

## Tests

```
pip install -r requirements-dev.txt
pytest
```

Set `TEST_DATABASE_URL` to a PostGIS + pgvector database to also run the Postgres tests (CI does this).

## Known limits of this prototype

- `/search` is semantic (embeddings, first use downloads a ~130 MB model) plus keywords.
- The Instagram downloader can be blocked by Instagram. Public YouTube shorts work without any login. Cookies from your own account help but can expire; never use an account you cannot lose.
- Geocoding uses the public Nominatim service (1 request per second, cached). Heavy use needs a self-hosted instance.
- Live conditions are shared across all users; saved items are private to each user.
- Each worker processes one reel at a time; run more workers for more throughput.
