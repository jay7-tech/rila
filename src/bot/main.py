import logging
import os
import re
import asyncio
import uuid
import traceback
import math
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from rapidfuzz import fuzz
from yt_dlp.utils import DownloadError
import groq

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.pipeline.ingestion import process_reel
from src.pipeline.extraction import extract_place_info
from src.pipeline.geocoding import geocode_place
from src.db.database import SessionLocal, Place, Embedding, compute_embedding, chroma_collection, embedding_model
from sqlalchemy import func

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # radius of Earth in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

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
        
        url_lower = url.lower()
        if "youtube.com" not in url_lower and "youtu.be" not in url_lower and "instagram.com" not in url_lower:
            await update.message.reply_text("This doesn't look like a YouTube or Instagram video link.")
            return
            
        await update.message.reply_text(f"Processing reel: {url}")
        
        # Run pipeline in a separate thread so we don't block the async event loop
        try:
            caption, transcript = await asyncio.to_thread(process_reel, url)
        except DownloadError as e:
            err_str = str(e).lower()
            if "unsupported" in err_str:
                await update.message.reply_text("This doesn't look like a YouTube or Instagram video link.")
            else:
                await update.message.reply_text("Couldn't access this video — it may be private, deleted, or restricted in your region.")
            return
            
        if caption is None and transcript is None:
            await update.message.reply_text("Failed to download or transcribe the video.")
            return
            
        try:
            extracted_info = await asyncio.to_thread(extract_place_info, caption, transcript)
        except groq.RateLimitError:
            await update.message.reply_text("Hit a rate limit processing this — please try again in a minute")
            return
        
        if not extracted_info or not extracted_info.places:
            await update.message.reply_text("Could not extract any places from this reel.")
            return
            
        db = SessionLocal()
        try:
            saved_count = 0
            existing_places = db.query(
                Place, 
                func.ST_AsText(Place.location).label("wkt")
            ).filter(Place.user_id == update.effective_user.id).all()
            
            for place in extracted_info.places:
                if not place.place_name:
                    logger.debug(f"Skipping place with null/empty place_name: {place}")
                    continue
                
                normalized_new = place.place_name.lower().strip()
                
                lat, lon = None, None
                if place.place_name and place.city:
                    coords = await asyncio.to_thread(geocode_place, place.place_name, place.city)
                    if coords:
                        lat, lon = coords
                        
                is_duplicate = False
                for ex_place, wkt in existing_places:
                    if not ex_place.normalized_name: continue
                    ratio = fuzz.ratio(ex_place.normalized_name, normalized_new)
                    if ratio > 85:
                        if wkt and lat is not None and lon is not None:
                            lon_lat = wkt.replace('POINT(', '').replace(')', '').split()
                            if len(lon_lat) == 2:
                                p_lon, p_lat = map(float, lon_lat)
                                dist = haversine(lat, lon, p_lat, p_lon)
                                if dist < 500:
                                    is_duplicate = True
                        else:
                            is_duplicate = True
                            
                        if is_duplicate:
                            old_date = ex_place.saved_at.strftime('%Y-%m-%d') if ex_place.saved_at else "an earlier date"
                            ex_place.saved_at = datetime.now()
                            db.commit()
                            await update.message.reply_text(f"You already saved this — {place.place_name} ({place.city}), first saved on {old_date}.")
                            break
                            
                if is_duplicate:
                    continue

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
                saved_count += 1
                
            if saved_count == 0:
                await update.message.reply_text("Could not extract any valid places with names from this reel.")
                
                
        except Exception as e:
            logger.error(f"Error saving to db: {e}")
            logger.error(traceback.format_exc())
            db.rollback()
            await update.message.reply_text("An error occurred while saving the places.")
        finally:
            db.close()
    else:
        await update.message.reply_text("Please send an Instagram reel link.")

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle shared location pins for /nearby functionality."""
    user_location = update.message.location
    lat = user_location.latitude
    lon = user_location.longitude
    
    db = SessionLocal()
    try:
        if db.query(Place).count() == 0:
            await update.message.reply_text("You haven't saved anything yet.")
            return

        point_wkt = f"SRID=4326;POINT({lon} {lat})"
        
        results = db.query(
            Place,
            func.ST_Distance(Place.location, point_wkt).label("distance"),
            func.ST_AsText(Place.location).label("wkt")
        ).filter(
            Place.location.is_not(None)
        ).order_by("distance").limit(5).all()
        
        if not results:
            await update.message.reply_text("No saved places with known locations found.")
            return
            
        closest_dist = results[0][1]
        if closest_dist > 5000:
            reply_lines = ["Nothing nearby, but here are your closest saved places for reference:"]
        else:
            reply_lines = ["Nearest saved places:"]
            
        for place, dist, wkt in results:
            dist_text = f"{dist:.0f}m away" if dist < 1000 else f"{(dist/1000):.1f}km away"
            marker = "📍 " if dist <= 2000 else ""
            link = "no pin available"
            if wkt:
                lon_lat = wkt.replace('POINT(', '').replace(')', '').split()
                if len(lon_lat) == 2:
                    p_lon, p_lat = lon_lat
                    link = f"https://www.google.com/maps?q={p_lat},{p_lon}"
                    
            reply_lines.append(f"{marker}- {place.place_name} ({place.city}, {place.category}) - {dist_text}\n  {link}")
            
        await update.message.reply_text("\n\n".join(reply_lines))
    except Exception as e:
        logger.error(f"Error in handle_location: {e}")
        await update.message.reply_text("An error occurred while finding nearby places.")
    finally:
        db.close()

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /search command for semantic search."""
    if not context.args:
        await update.message.reply_text("Please provide a search query. Example: /search sushi")
        return
        
    query_text = " ".join(context.args)
    
    db = SessionLocal()
    try:
        if db.query(Place).count() == 0:
            await update.message.reply_text("You haven't saved anything yet.")
            return
            
        vector = await asyncio.to_thread(embedding_model.encode, query_text)
        
        chroma_results = chroma_collection.query(
            query_embeddings=[vector.tolist()],
            n_results=5
        )
        
        if not chroma_results['ids'] or not chroma_results['ids'][0]:
            await update.message.reply_text("No matching places found.")
            return
            
        vector_ids = chroma_results['ids'][0]
        
        embeddings_records = db.query(Embedding).filter(Embedding.vector_id.in_(vector_ids)).all()
        place_ids = [e.place_id for e in embeddings_records]
        
        if not place_ids:
            await update.message.reply_text("No matching places found in database.")
            return
            
        places = db.query(
            Place, 
            func.ST_AsText(Place.location).label("wkt")
        ).filter(Place.id.in_(place_ids)).all()
        
        place_dict = {p.Place.id: p for p in places}
        
        reply_lines = [f"🔍 Top results for '{query_text}':"]
        for p_id in place_ids:
            if p_id in place_dict:
                row = place_dict[p_id]
                p = row.Place
                wkt = row.wkt
                
                deal_text = f"\n  Deal: {p.deal_description}" if p.deal_description else ""
                
                maps_link = "no pin available"
                if wkt:
                    lon_lat = wkt.replace('POINT(', '').replace(')', '').split()
                    if len(lon_lat) == 2:
                        p_lon, p_lat = lon_lat
                        maps_link = f"https://www.google.com/maps?q={p_lat},{p_lon}"
                        
                reply_lines.append(f"- {p.place_name} ({p.city}, {p.category}){deal_text}\n  Map: {maps_link}")
                
        await update.message.reply_text("\n\n".join(reply_lines))
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        await update.message.reply_text("An error occurred during search.")
    finally:
        db.close()

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
    application.add_handler(CommandHandler("search", handle_search))
    
    # Handle shared location pins for nearby
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
