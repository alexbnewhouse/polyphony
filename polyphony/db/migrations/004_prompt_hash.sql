-- Migration 004: Add prompt hash for prompt sensitivity tracking
-- Stores a SHA-256 hash of the system+user prompt pair so researchers
-- can detect when prompt changes affect coding outcomes.

ALTER TABLE llm_call ADD COLUMN prompt_hash TEXT DEFAULT NULL;
