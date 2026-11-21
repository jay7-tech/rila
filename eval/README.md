# Evaluation cases

One JSON file per video in `cases/`. Fields:

- `id`, `language`, `now` (the date the video should be read as of), optional `posted_at`
- `caption` and `transcript` (the tagged lines from a real run: `[0:12 speech] ...`, `[0:05-0:09 screen] ...`)
- `expected.items`: what a careful person would save: `kind` (place | offer | info), `venue` or `title`,
  optional `names` (other spellings), `city`
- `labeled: true` once you have checked and corrected `expected` by hand
- `synthetic: true` for hand-written smoke tests (never counted in reported numbers)

Capture a real one: `python -m app.cli capture <document id>` writes a draft with `labeled: false`.
Fix `expected` by watching the video, then set `labeled` to true. Run: `python -m app.cli eval`.
