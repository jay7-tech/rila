BEGIN;

ALTER TABLE places ADD COLUMN IF NOT EXISTS content_type TEXT DEFAULT 'place';
ALTER TABLE places ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS key_details TEXT;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='places' AND column_name='place_name') THEN
        ALTER TABLE places RENAME COLUMN place_name TO title;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='places' AND column_name='deal_expiry') THEN
        ALTER TABLE places RENAME COLUMN deal_expiry TO expiry_or_deadline;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='places' AND column_name='deal_description') THEN
        UPDATE places
        SET key_details = TRIM(BOTH ' ' FROM
            COALESCE(deal_description, '') ||
            CASE WHEN price_hint IS NOT NULL AND price_hint != '' THEN ' | ' || price_hint ELSE '' END
        )
        WHERE key_details IS NULL
          AND (deal_description IS NOT NULL OR price_hint IS NOT NULL);

        ALTER TABLE places DROP COLUMN IF EXISTS deal_description;
        ALTER TABLE places DROP COLUMN IF EXISTS price_hint;
    END IF;
END $$;

UPDATE places SET content_type = 'place' WHERE content_type IS NULL;

COMMIT;
