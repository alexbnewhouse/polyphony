-- Migration 007: Human authority & transparency features
--
-- Adds columns for:
--   1. supervisor_blind_code on flag (commit-then-reveal gut-check)
--   2. review_stats on codebook_version (track human decision types during induction)
--   3. metadata on agent (Coder Card fields: persona, known_limitations, etc.)

ALTER TABLE flag ADD COLUMN supervisor_blind_code TEXT;

ALTER TABLE codebook_version ADD COLUMN review_stats TEXT;

ALTER TABLE agent ADD COLUMN metadata TEXT DEFAULT '{}';
