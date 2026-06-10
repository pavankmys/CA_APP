-- Migration 002: Replace old MCQ-only Mock Test with CPA-Style Simulation Exam
-- Run this once in the Supabase SQL editor (after schema.sql has already been applied).

DROP TABLE IF EXISTS mock_test_answers;
DROP TABLE IF EXISTS mock_tests;

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
