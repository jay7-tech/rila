"""Command line tools for running and testing Rilla without any chat app.

    python -m app.cli doctor                  check that everything Rilla needs is installed
    python -m app.cli make-user "Suresh"      create a web account and print its sign-in link
    python -m app.cli read <link|file|text>   see what Rilla perceives and extracts (no database writes)
    python -m app.cli capture <document id>   save a processed video as a draft evaluation case
    python -m app.cli eval                    score extraction against the labelled cases
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

from dotenv import load_dotenv

from .config import get_settings


def _db():
    from .db import SessionLocal, init_db

    s = get_settings()
    init_db(s.database_url, migrate=s.run_migrations)
    return SessionLocal()


def cmd_doctor(_args) -> int:
    from .perception import ocr

    s = get_settings()
    ok = True

    def line(name: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"{'OK  ' if good else 'FAIL'} {name}{': ' + detail if detail else ''}")

    for tool in ("ffmpeg", "ffprobe", "tesseract", "node"):
        line(tool, bool(shutil.which(tool)))
    have = ocr.available_langs()
    wanted = [l for l in s.ocr_langs.split("+") if l]
    missing = [l for l in wanted if l not in have]
    line("OCR languages", not missing, f"have {'+'.join(l for l in wanted if l in have) or 'none'}" + (f", missing {'+'.join(missing)}" if missing else ""))
    for mod, label in (("faster_whisper", "faster-whisper"), ("fastembed", "fastembed"), ("yt_dlp", "yt-dlp"), ("groq", "groq")):
        try:
            __import__(mod)
            line(label, True)
        except ImportError:
            line(label, False, "not installed")
    line("GROQ_API_KEY", bool(s.groq_api_key), "set" if s.groq_api_key else "missing: extraction cannot run")
    try:
        from sqlalchemy import text

        with _db() as session:
            session.execute(text("select 1"))
        line("database", True, s.database_url.split("@")[-1].split("://")[0])
    except Exception as e:
        line("database", False, type(e).__name__)
    print("\nReady." if ok else "\nFix the FAIL lines above, then run doctor again.")
    return 0 if ok else 1


def cmd_make_user(args) -> int:
    from .services import users as users_svc

    with _db() as session:
        user = users_svc.create_user(session, None, args.name)
        session.commit()
        print(f"Created {args.name}.\nSign-in link (keep it private):\n{get_settings().public_url}/?t={user.token}")
    return 0


def _ingest(source: str):
    from .services import ingest as ingest_svc
    from .sources import local
    from .sources.safeurl import validate_url

    if os.path.isfile(source):
        return local.ingest_file(source, os.path.basename(source))
    if source.startswith(("http://", "https://")):
        return ingest_svc.ingest(validate_url(source, get_settings().allowed_hosts))
    return local.ingest_text(source)


def cmd_read(args) -> int:
    from .services import extract as extract_svc
    from .services.ingest import IngestError
    from .sources.safeurl import UrlRejected

    try:
        ing = _ingest(args.source)
    except (IngestError, UrlRejected) as e:
        print(f"Could not read that: {e}")
        return 1
    doc = ing.document
    print(f"== Perceived ({doc.kind}, language {doc.language or 'unknown'}, {doc.duration or 0:.0f}s) ==")
    print(json.dumps(doc.meta, ensure_ascii=False))
    if ing.caption:
        print(f"[caption] {ing.caption[:500]}")
    print(ing.transcript or "(no speech or on-screen text)")
    if args.no_llm:
        return 0
    if not get_settings().groq_api_key:
        print("\nSet GROQ_API_KEY to also run extraction (or pass --no-llm).")
        return 1
    res = extract_svc.extract(ing.caption, ing.transcript, datetime.utcnow(), ing.posted_at)
    print(f"\n== Extracted ({len(res.items)} items, {len(res.signals)} signals, "
          f"{res.stats.get('ungrounded_dropped', 0)} dropped as ungrounded) ==")
    print(json.dumps([i.model_dump(exclude_none=True) for i in res.items], ensure_ascii=False, indent=2))
    return 0


def cmd_capture(args) -> int:
    from sqlalchemy import select

    from .evalkit.runner import CASES_DIR
    from .models import Document, Item

    with _db() as session:
        doc = session.get(Document, args.document_id)
        if doc is None:
            print("No such document.")
            return 1
        from .core.document import Segment, VideoDocument

        vd = VideoDocument(kind=doc.kind, segments=[Segment(**sg) for sg in doc.timeline])
        items = list(session.scalars(select(Item).where(Item.document_id == doc.id)))
        case = {
            "id": f"doc{doc.id}", "labeled": False, "language": doc.language, "source": doc.source_url,
            "now": doc.created_at.replace(microsecond=0).isoformat(), "caption": vd.caption,
            "transcript": vd.render_body(),
            "expected": {"items": [{"kind": i.kind, "venue": i.venue or i.title, "names": [n for n in (i.venue_en,) if n],
                                    "city": i.city} for i in items]},
        }
    os.makedirs(CASES_DIR, exist_ok=True)
    path = os.path.join(CASES_DIR, f"doc{args.document_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {path}\nWatch the video, correct 'expected' by hand, then set \"labeled\": true.")
    return 0


def cmd_eval(args) -> int:
    from .evalkit import runner
    from .services.extract import _groq_json

    cases = runner.load_cases(args.cases or runner.CASES_DIR, include_synthetic=args.include_synthetic)
    if not cases:
        print("No labelled cases yet. Capture some with `capture`, label them, and run again.")
        return 1
    if not get_settings().groq_api_key:
        print("Set GROQ_API_KEY first: the evaluation calls the same model the app uses (answers are cached afterwards).")
        return 1
    llm = _groq_json if args.no_cache else runner.cached(_groq_json)
    result = runner.run(cases, llm)
    print(runner.report(result))
    if args.save:
        out_dir = os.path.join(os.path.dirname(runner.CASES_DIR), "runs")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, result["when"].replace(":", "") + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\nSaved {path}")
    return 0


def main(argv=None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    a = sub.add_parser("make-user")
    a.add_argument("name")
    a.set_defaults(fn=cmd_make_user)
    a = sub.add_parser("read")
    a.add_argument("source", help="a video link, a path to a video/audio/image file, or some text")
    a.add_argument("--no-llm", action="store_true", help="only show what was seen and heard")
    a.set_defaults(fn=cmd_read)
    a = sub.add_parser("capture")
    a.add_argument("document_id", type=int)
    a.set_defaults(fn=cmd_capture)
    a = sub.add_parser("eval")
    a.add_argument("--cases")
    a.add_argument("--include-synthetic", action="store_true")
    a.add_argument("--no-cache", action="store_true")
    a.add_argument("--save", action="store_true", help="write the result to eval/runs/")
    a.set_defaults(fn=cmd_eval)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
