import os
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, Boolean, BigInteger, Text, ForeignKey
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
    place_name = Column(Text)
    normalized_name = Column(Text)
    city = Column(Text)
    category = Column(Text)
    deal_description = Column(Text)
    deal_expiry = Column(Date)
    price_hint = Column(Text)
    location = Column(Geography(geometry_type='POINT', srid=4326))
    confidence_score = Column(Float)
    visited = Column(Boolean, default=False)
    user_id = Column(BigInteger, nullable=False)

class Embedding(Base):
    __tablename__ = "embeddings"
    # To keep it simple in SQLAlchemy without a primary key defined in schema.sql, 
    # we can define place_id as primary_key just for ORM mapping.
    place_id = Column(Integer, ForeignKey("places.id"), primary_key=True)
    vector_id = Column(Text)

# Initialize ChromaDB locally
chroma_client = chromadb.PersistentClient(path="./chroma_db")
chroma_collection = chroma_client.get_or_create_collection(name="rila_places")

# Load sentence transformer model globally to avoid reloading
embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

def compute_embedding(place_name: str, city: str, category: str, deal_description: str, transcript: str):
    """
    Computes embedding text heavily weighted towards specific place data 
    rather than just the generic reel transcript.
    """
    p_name = place_name or ""
    p_city = city or ""
    p_cat = category or ""
    p_deal = deal_description or ""
    
    # Weight specific place details at the front
    embed_text = f"Place: {p_name}. City: {p_city}. Category: {p_cat}. Deal: {p_deal}. Context: {transcript}"
    
    vector = embedding_model.encode(embed_text)
    return vector.tolist()
