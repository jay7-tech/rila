CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS places (
    id SERIAL PRIMARY KEY,
    source_url TEXT NOT NULL,
    source_type TEXT DEFAULT 'instagram_reel',
    raw_caption TEXT,
    transcript TEXT,
    content_type TEXT DEFAULT 'place',
    title TEXT,
    normalized_name TEXT,
    city TEXT,
    category TEXT,
    summary TEXT,
    key_details TEXT,
    expiry_or_deadline DATE,
    location GEOGRAPHY(Point, 4326),
    confidence_score FLOAT,
    saved_at TIMESTAMP DEFAULT now(),
    visited BOOLEAN DEFAULT false,
    user_id BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_places_location ON places USING GIST (location);
CREATE INDEX IF NOT EXISTS idx_places_expiry ON places (expiry_or_deadline);
CREATE INDEX IF NOT EXISTS idx_places_content_type ON places (content_type);

CREATE TABLE IF NOT EXISTS embeddings (
    place_id INT REFERENCES places(id),
    vector_id TEXT
);
