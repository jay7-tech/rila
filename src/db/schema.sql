CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS places (
    id SERIAL PRIMARY KEY,
    source_url TEXT NOT NULL,
    source_type TEXT DEFAULT 'instagram_reel',
    raw_caption TEXT,
    transcript TEXT,
    place_name TEXT,
    normalized_name TEXT,          -- lowercased, fuzzy-match key
    city TEXT,
    category TEXT,                  -- food | attraction | store | event | other
    deal_description TEXT,
    deal_expiry DATE,
    price_hint TEXT,
    location GEOGRAPHY(Point, 4326),  -- PostGIS point
    confidence_score FLOAT,          -- LLM's own confidence in extraction
    saved_at TIMESTAMP DEFAULT now(),
    visited BOOLEAN DEFAULT false,
    user_id BIGINT NOT NULL          -- telegram user id, supports multi-user later
);

CREATE INDEX IF NOT EXISTS idx_places_location ON places USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_places_expiry ON places (deal_expiry);

CREATE TABLE IF NOT EXISTS embeddings (
    place_id INT REFERENCES places(id),
    vector_id TEXT  -- Chroma document id
);
