ALTER TABLE searches ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'cohort'
  CHECK (kind IN ('cohort', 'query'));
