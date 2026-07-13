import logging
import os
import re
import asyncio
import uuid
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.pipeline.ingestion import process_reel
from src.pipeline.extraction import extract_place_info
from src.pipeline.geocoding import geocode_place
from src.db.database import SessionLocal, Place, Embedding, compute_embedding, chroma_collection

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_text(
        f"Hi {user.first_name}! I am RILA. Send me an Instagram reel link and I'll extract the location and save it for you."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message if it contains a URL, for now."""
    text = update.message.text
    
    # Simple URL regex
    url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
    urls = url_pattern.findall(text)
    
    if urls:
        url = urls[0]
        await update.message.reply_text(f"Processing reel: {url}")
        
        # Run pipeline in a separate thread so we don't block the async event loop
        caption, transcript = await asyncio.to_thread(process_reel, url)
        if caption is None and transcript is None:
            await update.message.reply_text("Failed to download or transcribe the video.")
            return
            
        extracted_info = await asyncio.to_thread(extract_place_info, caption, transcript)
        
        if not extracted_info or not extracted_info.places:
            await update.message.reply_text("Could not extract any places from this reel.")
            return
            
        db = SessionLocal()
        try:
            for place in extracted_info.places:
                if not place.place_name:
                    logger.debug(f"Skipping place with null/empty place_name: {place}")
                    continue
                lat, lon = None, None
                if place.place_name and place.city:
                    coords = await asyncio.to_thread(geocode_place, place.place_name, place.city)
                    if coords:
                        lat, lon = coords

                # Save to Postgres
                wkt_point = f"SRID=4326;POINT({lon} {lat})" if lat is not None and lon is not None else None
                
                db_place = Place(
                    source_url=url,
                    raw_caption=caption,
                    transcript=transcript,
                    place_name=place.place_name,
                    normalized_name=place.place_name.lower() if place.place_name else None,
                    city=place.city,
                    category=place.category,
                    deal_description=place.deal_description,
                    deal_expiry=place.deal_expiry,
                    price_hint=place.price_hint,
                    location=wkt_point,
                    confidence_score=place.confidence,
                    user_id=update.effective_user.id
                )
                db.add(db_place)
                db.commit()
                db.refresh(db_place)
                
                # Generate Embedding
                vector = await asyncio.to_thread(
                    compute_embedding, 
                    place.place_name, place.city, place.category, place.deal_description, transcript
                )
                vector_id = str(uuid.uuid4())
                
                # Save to Chroma
                chroma_collection.add(
                    ids=[vector_id],
                    embeddings=[vector],
                    metadatas=[{"place_id": db_place.id, "place_name": place.place_name or "", "city": place.city or ""}]
                )
                
                # Link in Postgres
                db_embed = Embedding(place_id=db_place.id, vector_id=vector_id)
                db.add(db_embed)
                db.commit()
                
                # Reply to user
                if lat is not None and lon is not None:
                    reply_text = f"📍 Found: {place.place_name} in {place.city}\nGoogle Maps: https://www.google.com/maps?q={lat},{lon}"
                else:
                    reply_text = f"✅ Saved: {place.place_name or 'Unknown'} in {place.city or 'Unknown'}.\n(No map pin: geocoding failed, but data is saved!)"
                    
                await update.message.reply_text(reply_text)
                
        except Exception as e:
            logger.error(f"Error saving to db: {e}")
            db.rollback()
            await update.message.reply_text("An error occurred while saving the places.")
        finally:
            db.close()
    else:
        await update.message.reply_text("Please send an Instagram reel link.")

def main() -> None:
    """Start the bot."""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN environment variable not set.")
        return

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(bot_token).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
