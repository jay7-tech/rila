<p align="center"><img src="web/logo.svg" alt="Rila" width="180"></p>

# Rila

Rila turns the videos you watch into a map of offers, places and live local conditions.

Give it anything: a reel or short link, an uploaded video or audio clip, a screenshot, or pasted text, in any language. Rila listens (language detected automatically), reads the words on screen, extracts what is on offer (venue, price, start and end time), checks every fact against a quote from the source, finds it on the map, and brings it back when you are near or when it is about to end.

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

Open the link. Add a video link, upload a file, paste a screenshot (Ctrl+V anywhere on the page) or paste text. On Android, open the app in Chrome, choose Install, and "Share to Rila" appears in other apps' share sheets (iPhone does not support this yet; paste the link instead).

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

The full architecture and roadmap is the "Rila: Architecture and Build Roadmap" document.

## Speech and OCR engines
- `ASR_ENGINE=auto` (default) uses Groq Whisper large-v3 when `GROQ_API_KEY` is set, otherwise local faster-whisper. If Groq fails it falls back to local. **Audio is sent to Groq unless `ASR_ENGINE=local`.**
- `OCR_LANGS` defaults to `eng+hin+kan+tel`; `OCR_MIN_CONF_NONLATIN` filters junk in non-Latin scripts.
- Items whose venue name does not appear in the caption, speech or on-screen text are dropped (counted as `ungrounded_dropped`).

## What you can do with your saved reels
- **Near me / All saved:** ranked by distance, time left, your learned tastes and how reliable the creator is. Each card has *Like*, *I went* and *Not for me*; hidden items disappear from Near me and from plans.
- **Places:** the same venue seen in several reels is merged into one card (reels, creators, prices, live offers, best proof). `GET /api/places`.
- **Plan:** `GET /api/plan?lat=&lon=&minutes=` picks the best nearby saved places and orders them into a route that fits your time, respecting offer start and end times. Times are estimates from straight-line distance.
- **You:** shows what Rila has learned (interest per category) and how reliable each creator is, with the numbers behind it. `GET /api/me/interests`, `GET /api/creators`.
- Feedback is stored in a new `events` table (migration 0003). Run `docker compose up -d migrate` or restart the stack to apply it.
- UI smoke test: `npm i jsdom && node web/test/ui_smoke.js`.

## Run everything with one command (Windows)
From the project folder in PowerShell or Command Prompt:
- `.\rila up` starts the stack, checks it, and opens your browser already signed in.
- `.\rila demo` adds sample places and offers so the Places, Plan and You tabs have something to show; `.\rila demo-clear` removes them.
- `.\rila status`, `.\rila logs`, `.\rila restart`, `.\rila test`, `.\rila batch`, `.\rila eval`, `.\rila public`, `.\rila down`. Run `.\rila help` for the list.

## Search, taste and reliability (ML)
- **Search** (`app/recs/retrieval.py`): BM25F over weighted fields (venue and title count more than the summary) with typo tolerance, fused with dense-vector search by Reciprocal Rank Fusion (k=60), then diversified with Maximal Marginal Relevance so one venue does not fill the page. Falls back to words only if the embedding model is unavailable. Press `/` or Ctrl+K in the app.
- **Taste model** (`app/recs/taste.py`): online logistic regression over hashed item features (category, creator, area, price band, content words), trained by SGD with L2 and 30-day recency decay from your likes, visits, opens and hides. Retrained on demand from the event log, nothing stored. `GET /api/foryou` blends p(like), the distance/urgency rank score and an exploration bonus, then diversifies, and returns the reasons.
- **Reliability** (`app/recs/reliability.py`): every saved item carries a transparent 0-1 score (quote found in the video, how it was seen, map pin, creator track record, reels that mention it). Tap the pips on a card to see the parts. `python -m app.cli eval` now also reports Brier score and ECE, so you can check that "high" really means mostly right once you have labelled cases.

## The app
Seven views: For you (taste-ranked, top pick as a signboard), Near me, Plan (drawn as a metro line with a time budget), Places, Saved, Live conditions, You (taste radar and what the model learned). Light and dark themes, bottom tab bar on phones. UI check: `npm i jsdom && node web/test/ui_smoke.js`.

## Multilingual search and measuring the taste model
- **Hindi / Hinglish queries:** set `EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` in `.env` (same 384 size, about 220 MB), run `.\rila restart`, then `.\rila reembed` so every saved item gets a vector from the new model (vectors from different models cannot be compared). It covers Hindi, Marathi, Gujarati and Urdu but not Kannada, Tamil or Telugu; a model that does (multilingual-e5-large, 1024 numbers) needs a migration to widen the column first, and Rila refuses to start it with a clear message until then.
- **`.\rila recs-eval`** replays your likes and visits in time order and reports hit@k, MRR and NDCG@k for the taste model against a category-count baseline and chance. Needs a few dozen real actions to mean anything.

## Labelling the test videos
`.\rila label` walks through each unlabelled eval case: it shows the caption, transcript and what the model extracted, and you press Enter if it is right, or fix it with one short command (`d 2` delete, `k 2 offer` change kind, `v 2 New name`, `a place | Name | City` add a missed one, `n` nothing to save, `?` help). Answers are saved to `eval/cases/` after each case. Then `.\rila eval` gives precision, recall, F1 and the calibration numbers.
