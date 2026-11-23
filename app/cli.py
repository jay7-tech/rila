"""Command line tools for running and testing Rilla without any chat app.

    python -m app.cli doctor                  check that everything Rilla needs is installed
    python -m app.cli make-user "Suresh"      create a web account and print its sign-in link
    python -m app.cli link "Suresh"           the sign-in link for that name (created if missing); the last line is the link
    python -m app.cli status                  counts, queued and failed jobs, settings in use (no secrets)
    python -m app.cli demo "Suresh"           add sample places, offers and reactions so every screen has something (--clear removes them)
    python -m app.cli read <link|file|text>   see what Rilla perceives and extracts (no database writes)
    python -m app.cli capture <document id>   save a processed video as a draft evaluation case
    python -m app.cli batch <links.txt>       read many links, write draft cases and a review sheet
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


def cmd_link(args) -> int:
    from sqlalchemy import func, select

    from .models import User
    from .services import users as users_svc

    with _db() as session:
        user = session.scalars(select(User).where(func.lower(User.name) == args.name.strip().lower()).order_by(User.id)).first()
        if user is None:
            user = users_svc.create_user(session, None, args.name)
            session.commit()
        print(f"{get_settings().public_url}/?t={user.token}")
    return 0


def cmd_status(_args) -> int:
    from sqlalchemy import func, select

    from .models import Document, Event, Item, Job, User

    s = get_settings()
    with _db() as session:
        count = lambda model, *where: session.scalar(select(func.count()).select_from(model).where(*where)) or 0
        print(f"users {count(User)} | items {count(Item)} | documents {count(Document)} | feedback events {count(Event)}")
        print("jobs  " + " | ".join(f"{st} {count(Job, Job.status == st)}" for st in ("queued", "running", "done", "failed")))
        failed = list(session.scalars(select(Job).where(Job.status == "failed").order_by(Job.id.desc()).limit(3)))
        for j in failed:
            print(f"  failed job {j.id}: {(j.error or '')[:110]}")
        print(f"asr {s.asr_engine} ({'groq whisper' if s.asr_engine != 'local' and s.groq_api_key else 'local ' + s.whisper_model}) | ocr {s.ocr_langs} | llm {s.groq_model}")
        print(f"sign-up {'invite code' if s.invite_code else 'open' if s.open_signup else 'closed'} | public url {s.public_url}")
    return 0


def cmd_demo(args) -> int:
    from sqlalchemy import func, select

    from .models import User, utcnow
    from .services import demo as demo_svc

    with _db() as session:
        user = session.scalars(select(User).where(func.lower(User.name) == args.name.strip().lower()).order_by(User.id)).first()
        if user is None:
            print(f"No user called {args.name!r}. Run: python -m app.cli link \"{args.name}\"")
            return 1
        if args.clear:
            print(f"Removed {demo_svc.clear_demo(session, user)} demo items.")
            return 0
        n = demo_svc.seed_demo(session, user, utcnow())
        print(f"Added {n} demo items. Open the app: Places, Plan and You now have data. Remove with: demo \"{args.name}\" --clear" if n else "Demo data is already there.")
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


def cmd_batch(args) -> int:
    from .evalkit import batch, runner
    from .services.extract import _groq_json
    from .sources.safeurl import UrlRejected, validate_url

    if not get_settings().groq_api_key:
        print("Set GROQ_API_KEY first.")
        return 1
    with open(args.links, encoding="utf-8") as f:
        urls = batch.clean_links(f.read())
    good = []
    for u in urls:
        try:
            good.append(validate_url(u, get_settings().allowed_hosts))
        except UrlRejected as e:
            print(f"Skipping {u}: {e}")
    print(f"{len(good)} links. Each takes about a minute; you can stop and re-run, finished ones are skipped.")
    status = batch.run_batch(good, _ingest, runner.cached(_groq_json), runner.CASES_DIR, pause=args.pause, force=args.force)
    sheet = os.path.join(os.path.dirname(runner.CASES_DIR), "review.md")
    with open(sheet, "w", encoding="utf-8") as f:
        f.write(batch.review_sheet(runner.CASES_DIR))
    bad = {u: m for u, m in status.items() if m not in ("ok", "skipped")}
    print(f"\nDone: {sum(m == 'ok' for m in status.values())} new, {sum(m == 'skipped' for m in status.values())} skipped, {len(bad)} failed.")
    for u, m in bad.items():
        print(f"  FAILED {u}: {m}")
    print(f"Review sheet: {sheet}")
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
    a = sub.add_parser("link")
    a.add_argument("name")
    a.set_defaults(fn=cmd_link)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    a = sub.add_parser("demo")
    a.add_argument("name")
    a.add_argument("--clear", action="store_true")
    a.set_defaults(fn=cmd_demo)
    a = sub.add_parser("read")
    a.add_argument("source", help="a video link, a path to a video/audio/image file, or some text")
    a.add_argument("--no-llm", action="store_true", help="only show what was seen and heard")
    a.set_defaults(fn=cmd_read)
    a = sub.add_parser("capture")
    a.add_argument("document_id", type=int)
    a.set_defaults(fn=cmd_capture)
    a = sub.add_parser("batch")
    a.add_argument("links", help="text file with one video link per line")
    a.add_argument("--pause", type=float, default=3.0, help="seconds between links (be gentle with YouTube)")
    a.add_argument("--force", action="store_true", help="redo links that already have a case")
    a.set_defaults(fn=cmd_batch)
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
