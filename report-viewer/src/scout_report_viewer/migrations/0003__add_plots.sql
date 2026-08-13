CREATE TABLE IF NOT EXISTS plots (
  id                  TEXT PRIMARY KEY,
  sql                 TEXT NOT NULL,
  spec                JSONB NOT NULL,
  owner_sub           TEXT NOT NULL,
  owui_chat_id        TEXT NOT NULL DEFAULT '',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS plots_owner_created_idx
  ON plots (owner_sub, created_at DESC);
