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
    allowed_id_str = os.environ.get("ALLOWED_USER_ID")
    if allowed_id_str and update.effective_user.id != int(allowed_id_str):
        await update.message.reply_text("This bot is private.")
        return

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
        
        if not extracted_info or not extracted_info.entities:
            await update.message.reply_text("Could not extract any entities from this reel.")
            return
            
        db = SessionLocal()
        try:
            saved_count = 0
            existing_places = db.query(
                Place, 
                func.ST_AsText(Place.location).label("wkt")
            ).filter(Place.user_id == update.effective_user.id).all()
            
            for entity in extracted_info.entities:
                if not entity.title:
                    logger.debug(f"Skipping entity with null/empty title: {entity}")
                    continue
                
                normalized_new = entity.title.lower().strip()
                
                lat, lon = None, None
                geocode_status = "error"
                if entity.content_type == "place" and entity.title and entity.city:
                    lat, lon, geocode_status = await asyncio.to_thread(geocode_place, entity.title, entity.city)

                if entity.content_type == "place" and geocode_status == "rejected":
                    logger.warning(
                        f"Rejected likely-hallucinated place '{entity.title}' ({entity.city}) "
                        f"— no confident geocoding match found."
                    )
                    continue  # skip this entity entirely, do not save, do not notify — try the next extracted place
                        
                is_duplicate = False
                for ex_entity, wkt in existing_places:
                    if not ex_entity.normalized_name: continue
                    ratio = fuzz.ratio(ex_entity.normalized_name, normalized_new)
                    if ratio > 85:
                        if entity.content_type == "place":
                            if wkt and lat is not None and lon is not None:
                                lon_lat = wkt.replace('POINT(', '').replace(')', '').split()
                                if len(lon_lat) == 2:
                                    p_lon, p_lat = map(float, lon_lat)
                                    dist = haversine(lat, lon, p_lat, p_lon)
                                    if dist < 500:
                                        is_duplicate = True
                            else:
                                is_duplicate = True
                        else:
                            is_duplicate = True
                            
                        if is_duplicate:
                            old_date = ex_entity.saved_at.strftime('%Y-%m-%d') if ex_entity.saved_at else "an earlier date"
                            ex_entity.saved_at = datetime.now()
                            db.commit()
                            city_str = f" in {entity.city}" if entity.city else ""
                            await update.message.reply_text(f"You already saved this — {entity.title}{city_str}, first saved on {old_date}.")
                            break
                            
                if is_duplicate:
                    continue

                # Save to Postgres
                wkt_point = f"SRID=4326;POINT({lon} {lat})" if lat is not None and lon is not None else None
                
                db_place = Place(
                    source_url=url,
                    raw_caption=caption,
                    transcript=transcript,
                    content_type=entity.content_type,
                    title=entity.title,
                    normalized_name=entity.title.lower() if entity.title else None,
                    city=entity.city,
                    category=entity.category,
                    summary=entity.summary,
                    key_details=entity.key_details,
                    expiry_or_deadline=entity.expiry_or_deadline,
                    location=wkt_point,
                    confidence_score=entity.confidence,
                    user_id=update.effective_user.id
                )
                db.add(db_place)
                db.commit()
                db.refresh(db_place)
                
                # Generate Embedding
                vector = await asyncio.to_thread(
                    compute_embedding, 
                    entity.content_type, entity.title, entity.city, entity.category, entity.summary, entity.key_details, transcript
                )
                vector_id = str(uuid.uuid4())
                
                # Save to Chroma
                chroma_collection.add(
                    ids=[vector_id],
                    embeddings=[vector],
                    metadatas=[{"place_id": db_place.id, "place_name": entity.title or "", "city": entity.city or ""}]
                )
                
                # Link in Postgres
                db_embed = Embedding(place_id=db_place.id, vector_id=vector_id)
                db.add(db_embed)
                db.commit()
                
                # Reply to user
                if entity.content_type == "place":
                    if lat is not None and lon is not None:
                        reply_text = f"📍 Found: {entity.title} in {entity.city}\nGoogle Maps: https://www.google.com/maps?q={lat},{lon}"
                    else:
                        reply_text = f"✅ Saved: {entity.title or 'Unknown'} in {entity.city or 'Unknown'}.\n(No map pin: not in OpenStreetMap yet, but data is saved!)"
                else:
                    details = ""
                    if entity.summary: details += f"\nSummary: {entity.summary}"
                    if entity.key_details: details += f"\nDetails: {entity.key_details}"
                    if entity.expiry_or_deadline: details += f"\nDeadline: {entity.expiry_or_deadline}"
                    reply_text = f"📄 Saved: {entity.title or 'Unknown'}{details}"
                    
                await update.message.reply_text(reply_text)
                saved_count += 1
                
            if saved_count == 0:
                await update.message.reply_text("Could not extract any valid entities with titles from this reel.")
                
                
        except Exception as e:
            logger.error(f"Error saving to db: {e}")
            logger.error(traceback.format_exc())
            db.rollback()
            await update.message.reply_text("An error occurred while saving the entities.")
        finally:
            db.close()
    else:
        await update.message.reply_text("Please send an Instagram reel link.")

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle shared location pins for /nearby functionality."""
    allowed_id_str = os.environ.get("ALLOWED_USER_ID")
    if allowed_id_str and update.effective_user.id != int(allowed_id_str):
        await update.message.reply_text("This bot is private.")
        return

    user_location = update.message.location
    lat = user_location.latitude
    lon = user_location.longitude
    
    db = SessionLocal()
    try:
        if db.query(Place).filter(Place.user_id == update.effective_user.id).count() == 0:
            await update.message.reply_text("You haven't saved anything yet.")
            return

        point_wkt = f"SRID=4326;POINT({lon} {lat})"
        
        results = db.query(
            Place,
            func.ST_Distance(Place.location, point_wkt).label("distance"),
            func.ST_AsText(Place.location).label("wkt")
        ).filter(
            Place.location.is_not(None),
            Place.user_id == update.effective_user.id
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
                    
            reply_lines.append(f"{marker}- {place.title} ({place.city}, {place.category}) - {dist_text}\n  {link}")
            
        await update.message.reply_text("\n\n".join(reply_lines))
    except Exception as e:
        logger.error(f"Error in handle_location: {e}")
        await update.message.reply_text("An error occurred while finding nearby places.")
    finally:
        db.close()

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /search command for semantic search."""
    allowed_id_str = os.environ.get("ALLOWED_USER_ID")
    if allowed_id_str and update.effective_user.id != int(allowed_id_str):
        await update.message.reply_text("This bot is private.")
        return

    if not context.args:
        await update.message.reply_text("Please provide a search query. Example: /search sushi")
        return
        
    query_text = " ".join(context.args)
    
    db = SessionLocal()
    try:
        if db.query(Place).filter(Place.user_id == update.effective_user.id).count() == 0:
            await update.message.reply_text("You haven't saved anything yet.")
            return
            
        vector = await asyncio.to_thread(embedding_model.encode, query_text)
        
        chroma_results = chroma_collection.query(
            query_embeddings=[vector.tolist()],
            n_results=5
        )
        
        if not chroma_results['ids'] or not chroma_results['ids'][0]:
            await update.message.reply_text("No matching results found.")
            return
            
        vector_ids = chroma_results['ids'][0]
        
        embeddings_records = db.query(Embedding).filter(Embedding.vector_id.in_(vector_ids)).all()
        place_ids = [e.place_id for e in embeddings_records]
        
        if not place_ids:
            await update.message.reply_text("No matching results found in database.")
            return
            
        places = db.query(
            Place, 
            func.ST_AsText(Place.location).label("wkt")
        ).filter(
            Place.id.in_(place_ids),
            Place.user_id == update.effective_user.id
        ).all()
        
        place_dict = {p.Place.id: p for p in places}
        
        reply_lines = [f"🔍 Top results for '{query_text}':"]
        for p_id in place_ids:
            if p_id in place_dict:
                row = place_dict[p_id]
                p = row.Place
                wkt = row.wkt
                
                details = ""
                if p.summary: details += f"\n  Summary: {p.summary}"
                if p.key_details: details += f"\n  Details: {p.key_details}"
                
                maps_link = ""
                if p.content_type == "place":
                    link = "no pin available"
                    if wkt:
                        lon_lat = wkt.replace('POINT(', '').replace(')', '').split()
                        if len(lon_lat) == 2:
                            p_lon, p_lat = lon_lat
                            link = f"https://www.google.com/maps?q={p_lat},{p_lon}"
                    maps_link = f"\n  Map: {link}"
                        
                city_str = f" in {p.city}" if p.city else ""
                reply_lines.append(f"- {p.title}{city_str} ({p.category}){details}{maps_link}")
                
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
