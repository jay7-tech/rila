import os
import sys
import uuid
import time
from sqlalchemy import func

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.db.database import SessionLocal, Place, Embedding, chroma_collection, embedding_model

def run_test():
    print("==================================================")
    print("Testing Retrieval (Phase 4)")
    print("==================================================")
    
    db = SessionLocal()
    try:
        # Central test point: Times Square (40.7580, -73.9855)
        test_lat, test_lon = 40.7580, -73.9855
        test_wkt = f"SRID=4326;POINT({test_lon} {test_lat})"
        
        # Insert fake places
        fake_places = [
            Place(
                source_url="test_url_1", transcript="test1", category="food",
                title="Fake Pizza (1km away)", city="New York",
                location=f"SRID=4326;POINT(-73.9800 40.7500)", # ~1km away
                user_id=1
            ),
            Place(
                source_url="test_url_2", transcript="test2", category="food",
                title="Fake Sushi (500m away)", city="New York",
                location=f"SRID=4326;POINT(-73.9820 40.7540)", # ~500m away
                user_id=1
            ),
            Place(
                source_url="test_url_3", transcript="test3", category="food",
                title="Fake Burger (3km away)", city="New York",
                location=f"SRID=4326;POINT(-73.9900 40.7200)", # >2km away
                user_id=1
            )
        ]
        
        for p in fake_places:
            db.add(p)
        db.commit()
        
        # Insert into Chroma DB
        for p in fake_places:
            text_to_embed = f"{p.title} {p.city} {p.category} {p.transcript}"
            vector = embedding_model.encode(text_to_embed)
            vector_id = str(uuid.uuid4())
            chroma_collection.add(
                ids=[vector_id],
                embeddings=[vector.tolist()],
                metadatas=[{"place_id": p.id, "title": p.title, "city": p.city}]
            )
            db.add(Embedding(place_id=p.id, vector_id=vector_id))
        db.commit()
        
        print("\n--- TEST: /nearby (PostGIS ST_DWithin) ---")
        print(f"Test Location: Times Square ({test_lat}, {test_lon})")
        print("Querying places within 2km...")
        
        results = db.query(
            Place,
            func.ST_Distance(Place.location, test_wkt).label("distance"),
            func.ST_AsText(Place.location).label("wkt")
        ).filter(
            func.ST_DWithin(Place.location, test_wkt, 2000)
        ).order_by("distance").limit(10).all()
        
        if not results:
            print("No saved places within 2km of your location.")
        else:
            for place, dist, wkt in results:
                dist_text = f"{dist:.0f}m away" if dist < 1000 else f"{(dist/1000):.1f}km away"
                print(f" - {place.title} ({place.city}) - {dist_text}")
                
        print("\n--- TEST: /search (ChromaDB + Postgres) ---")
        query_text = "sushi"
        print(f"Querying for: '{query_text}'...")
        
        vector = embedding_model.encode(query_text)
        chroma_results = chroma_collection.query(
            query_embeddings=[vector.tolist()],
            n_results=3
        )
        
        if not chroma_results['ids'] or not chroma_results['ids'][0]:
            print("No matching places found in Chroma.")
        else:
            vector_ids = chroma_results['ids'][0]
            embeddings_records = db.query(Embedding).filter(Embedding.vector_id.in_(vector_ids)).all()
            place_ids = [e.place_id for e in embeddings_records]
            
            if not place_ids:
                print("No matching places found in database.")
            else:
                places = db.query(Place).filter(Place.id.in_(place_ids)).all()
                place_dict = {p.id: p for p in places}
                
                for p_id in place_ids:
                    if p_id in place_dict:
                        p = place_dict[p_id]
                        print(f" - {p.title} ({p.city}, {p.category})")
                        
    finally:
        # Cleanup fake places
        fake_ids = [p.id for p in fake_places if p.id]
        if fake_ids:
            embeddings_records = db.query(Embedding).filter(Embedding.place_id.in_(fake_ids)).all()
            vector_ids = [e.vector_id for e in embeddings_records]
            if vector_ids:
                chroma_collection.delete(ids=vector_ids)
            db.query(Embedding).filter(Embedding.place_id.in_(fake_ids)).delete(synchronize_session=False)
        
        db.query(Place).filter(Place.source_url.like("test_url_%")).delete(synchronize_session=False)
        db.commit()
        db.close()

if __name__ == "__main__":
    run_test()
