-- Migration 003: Replace MCQ-style simulation sub-questions with real
-- Task-Based-Simulation items (numeric entry, dropdown, journal entry).
-- Run this once in the Supabase SQL editor (after migration_002 has already been applied).

-- Both tables are already empty post-reset; truncate for safety before
-- recreating simulation_questions with the new shape.
TRUNCATE TABLE simulations RESTART IDENTITY CASCADE;
DROP TABLE IF EXISTS simulation_questions;

CREATE TABLE simulation_questions (
    id              SERIAL PRIMARY KEY,
    simulation_id   INTEGER REFERENCES simulations(id) ON DELETE CASCADE,
    seq_no          INTEGER NOT NULL,
    item_type       TEXT NOT NULL,      -- 'numeric' | 'dropdown' | 'journal_entry'
    question        TEXT NOT NULL,      -- prompt/instructions for this item
    payload         TEXT NOT NULL,      -- JSON: type-specific data (choices, correct values, etc.)
    explanation     TEXT
);
