-- Migration 004: Audio Notes — podcast-style audio episodes per chapter.
-- Run this once in the Supabase SQL editor (after schema.sql has already been applied).

CREATE TABLE IF NOT EXISTS audio_episodes (
    id               SERIAL PRIMARY KEY,
    chapter_id       INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
    episode_num      INTEGER NOT NULL,
    title            TEXT NOT NULL,
    audio_url        TEXT NOT NULL,
    duration_seconds INTEGER,
    word_count       INTEGER,
    file_size_bytes  INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
