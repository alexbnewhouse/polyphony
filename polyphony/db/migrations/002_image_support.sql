-- Migration 002: Add multimodal (image) support
--
-- Adds media_type and image_path columns to document and segment tables
-- to support image-based qualitative data analysis alongside text.

ALTER TABLE document ADD COLUMN media_type TEXT NOT NULL DEFAULT 'text';
ALTER TABLE document ADD COLUMN image_path TEXT DEFAULT NULL;

ALTER TABLE segment ADD COLUMN media_type TEXT NOT NULL DEFAULT 'text';
ALTER TABLE segment ADD COLUMN image_path TEXT DEFAULT NULL;
