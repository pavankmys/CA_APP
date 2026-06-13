-- Migration 005: add syllabus_section column to chapters for exam-weightage-based priority ranking
ALTER TABLE chapters ADD COLUMN IF NOT EXISTS syllabus_section TEXT;
