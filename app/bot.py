"""Telegram bot. A thin layer over the same services the web API uses."""
from __future__ import annotations

import logging
import re
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import formatting as fmt
from .config import get_settings
from .db import SessionLocal
from .models import Item, Job, User, utcnow
from .services import query as q
from .services import users as users_svc
from .services.worker import enqueue

log = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://[^\s]+")
ALLOWED_HOSTS = ("instagram.com", "youtube.com", "youtu.be")
JOINED_KEY = "rilla_joined"


def _gate(session, tg_user, args) -> Optional[User]:
    """Return the user, creating them if allowed. None means access is denied."""
    s = get_settings()
    user = users_svc.get_by_telegram(session, tg_user.id)
    if user:
        return user
    if s.allowed_ids and tg_user.id not in s.allowed_ids:
        return None
    if s.invite_code and (not args or args[0] != s.invite_code):
        return None
    user = users_svc.create_user(session, tg_user.id, tg_user.first_name or "")
    session.commit()
    return user


async def _user_or_deny(update: Update, context: ContextTypes.DEFAULT_TYPE, session) -> Optional[User]:
    user = _gate(session, update.effective_user, context.args if context else None)
    if user is None:
        await update.effective_message.reply_text("Rilla is invite-only for now. Ask for an invite link.")
    return user


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if user:
            await update.message.reply_text(f"Hi {user.name or 'there'}! " + fmt.HELP)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(fmt.HELP)


async def web(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if user:
            await update.message.reply_text(
                f"Your private map link (don't share it):\n{get_settings().public_url}/?t={user.token}"
            )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    urls = [u for u in URL_RE.findall(update.message.text or "") if any(h in u.lower() for h in ALLOWED_HOSTS)]
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if not user:
            return
        if not urls:
            await update.message.reply_text("Send me an Instagram reel or YouTube short link, or share your location.")
            return
        waiting = session.query(Job).filter(Job.user_id == user.id, Job.status.in_(("queued", "running"))).count()
        if waiting >= 5:
            await update.message.reply_text("You have 5 reels in the queue already. Give me a minute.")
            return
        enqueue(session, user, urls[0], chat_id=update.effective_chat.id)
        session.commit()
    await update.message.reply_text("Got it. Reading the reel now, this usually takes under a minute.")


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loc = update.effective_message.location
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if not user:
            return
        users_svc.set_location(user, loc.latitude, loc.longitude)
        session.commit()
        items = q.recommend_for_user(session, user, loc.latitude, loc.longitude, utcnow(), 3000, None, 5)
    # live-location updates arrive as edited messages; only reply to the first share
    if update.edited_message is None:
        await update.effective_message.reply_text(fmt.recs_message(items, 3000))


async def near(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if not user:
            return
        if user.last_lat is None:
            await update.message.reply_text("Share your location first (paperclip, then Location).")
            return
        items = q.recommend_for_user(session, user, user.last_lat, user.last_lon, utcnow(), 3000, None, 5)
    await update.message.reply_text(fmt.recs_message(items, 3000))


async def saved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    category = context.args[0].lower() if context.args else None
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if not user:
            return
        items = q.list_saved(session, user, utcnow(), category, None, user.last_lat, user.last_lon)
    await update.message.reply_text(fmt.saved_message(items))


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Try: /search coffee")
        return
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if not user:
            return
        items = q.search_saved(session, user, " ".join(context.args), utcnow(), user.last_lat, user.last_lon, 8)
    await update.message.reply_text(fmt.saved_message(items) if items else "No saved item matches that.")


async def briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Try: /briefing Gokarna")
        return
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if not user:
            return
        data = q.briefing_for(session, " ".join(context.args), utcnow())
    await update.message.reply_text(data["text"])


async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text("Try: /forget 12 (the number is shown in /saved).")
        return
    with SessionLocal() as session:
        user = await _user_or_deny(update, context, session)
        if not user:
            return
        item = session.get(Item, int(context.args[0].lstrip("#")))
        if item is None or item.user_id != user.id:
            await update.message.reply_text("I couldn't find that item.")
            return
        session.delete(item)
        session.commit()
    await update.message.reply_text("Removed.")


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("web", web))
    app.add_handler(CommandHandler("near", near))
    app.add_handler(CommandHandler("saved", saved))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("briefing", briefing))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def make_notifier(application: Application):
    from .formatting import saved_summary

    async def notify(job, summary: dict) -> None:
        await application.bot.send_message(chat_id=job.chat_id, text=saved_summary(summary))

    return notify
