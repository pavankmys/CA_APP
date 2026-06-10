-- CA Inter Practice App — Supabase (Postgres) schema
-- Run this once in the Supabase SQL editor before using the app.

CREATE TABLE IF NOT EXISTS subjects (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS chapters (
    id         SERIAL PRIMARY KEY,
    subject_id INTEGER REFERENCES subjects(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    UNIQUE(subject_id, name)
);

CREATE TABLE IF NOT EXISTS mcqs (
    id              SERIAL PRIMARY KEY,
    chapter_id      INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    options         TEXT NOT NULL,
    correct_option  TEXT NOT NULL,
    explanation     TEXT,
    difficulty      TEXT DEFAULT 'medium',
    is_retired      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS srs_states (
    id          SERIAL PRIMARY KEY,
    mcq_id      INTEGER UNIQUE REFERENCES mcqs(id) ON DELETE CASCADE,
    repetitions INTEGER DEFAULT 0,
    interval    INTEGER DEFAULT 0,
    ease_factor NUMERIC(5,2) DEFAULT 2.5,
    next_review DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS mock_mcqs (
    id              SERIAL PRIMARY KEY,
    chapter_id      INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    options         TEXT NOT NULL,
    correct_option  TEXT NOT NULL,
    explanation     TEXT,
    difficulty      TEXT DEFAULT 'hard',
    is_retired      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS simulations (
    id          SERIAL PRIMARY KEY,
    chapter_id  INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    scenario    TEXT NOT NULL,
    difficulty  TEXT DEFAULT 'hard',
    is_retired  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS simulation_questions (
    id              SERIAL PRIMARY KEY,
    simulation_id   INTEGER REFERENCES simulations(id) ON DELETE CASCADE,
    seq_no          INTEGER NOT NULL,
    question        TEXT NOT NULL,
    options         TEXT NOT NULL,
    correct_option  TEXT NOT NULL,
    explanation     TEXT
);

CREATE TABLE IF NOT EXISTS cpa_exams (
    id                 SERIAL PRIMARY KEY,
    subject_id         INTEGER REFERENCES subjects(id),
    taken_at           DATE NOT NULL,
    mcq_total          INTEGER,
    mcq_correct        INTEGER,
    mcq_pct            NUMERIC,
    sim_total          INTEGER,
    sim_correct        INTEGER,
    sim_pct            NUMERIC,
    overall_pct        NUMERIC,
    is_proficient      INTEGER,
    time_taken_seconds INTEGER
);

CREATE TABLE IF NOT EXISTS cpa_exam_answers (
    id              SERIAL PRIMARY KEY,
    exam_id         INTEGER NOT NULL REFERENCES cpa_exams(id) ON DELETE CASCADE,
    item_type       TEXT NOT NULL,
    item_id         INTEGER,
    selected_option TEXT,
    is_correct      INTEGER,
    difficulty      TEXT
);

CREATE TABLE IF NOT EXISTS practice_log (
    id                 SERIAL PRIMARY KEY,
    mcq_id             INTEGER REFERENCES mcqs(id) ON DELETE CASCADE,
    reviewed_at        DATE NOT NULL,
    time_spent_seconds NUMERIC DEFAULT 0,
    srs_rating         INTEGER,
    attempts           INTEGER DEFAULT 1
);
