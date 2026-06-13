-- Migration 006: add notes_url column to audio_episodes for editable markdown notes stored in R2
ALTER TABLE audio_episodes ADD COLUMN IF NOT EXISTS notes_url TEXT;
