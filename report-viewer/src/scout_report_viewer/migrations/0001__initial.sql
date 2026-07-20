CREATE TABLE IF NOT EXISTS searches (
  id                  TEXT PRIMARY KEY,
  id_column           TEXT NOT NULL,
  sql                 TEXT NOT NULL,
  sql_explanation     TEXT NOT NULL DEFAULT '',
  match_terms         TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  match_diagnoses     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  row_count           INTEGER,
  uploaded_ids        TEXT[],
  owner_sub           TEXT NOT NULL,
  owui_chat_id        TEXT NOT NULL DEFAULT '',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS searches_owner_created_idx
  ON searches (owner_sub, created_at DESC);
