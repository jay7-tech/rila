import os
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, Boolean, BigInteger, Text, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from geoalchemy2 import Geography
import chromadb
from sentence_transformers import SentenceTransformer

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://rila:rilapassword@localhost:5432/rila")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Place(Base):
    __tablename__ = "places"
    id = Column(Integer, primary_key=True, index=True)
    source_url = Column(Text, nullable=False)
    source_type = Column(Text, default="instagram_reel")
    raw_caption = Column(Text)
    transcript = Column(Text)
    content_type = Column(Text, default="place")
    title = Column(Text)
    normalized_name = Column(Text)
    city = Column(Text)
    category = Column(Text)
    summary = Column(Text)
    key_details = Column(Text)
    expiry_or_deadline = Column(Date)
    location = Column(Geography(geometry_type='POINT', srid=4326))
    confidence_score = Column(Float)
    saved_at = Column(DateTime)
    visited = Column(Boolean, default=False)
    user_id = Column(BigInteger, nullable=False)

class Embedding(Base):
    __tablename__ = "embeddings"
    place_id = Column(Integer, ForeignKey("places.id"), primary_key=True)
    vector_id = Column(Text)

chroma_client = chromadb.PersistentClient(path="./chroma_db")
chroma_collection = chroma_client.get_or_create_collection(name="rila_places")
embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

def compute_embedding(content_type: str, title: str, city: str, category: str, summary: str, key_details: str, transcript: str):
    c_type = content_type or "place"
    e_title = title or ""
    e_city = city or ""
    e_cat = category or ""
    e_summary = summary or ""
    e_details = key_details or ""
    embed_text = (
        f"Type: {c_type}. Title: {e_title}. City: {e_city}. Category: {e_cat}. "
        f"Summary: {e_summary}. Details: {e_details}. Context: {transcript}"
    )
    vector = embedding_model.encode(embed_text)
    return vector.tolist()
