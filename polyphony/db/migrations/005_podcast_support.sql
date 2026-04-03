-- Migration 005: Podcast and audio timestamp support
--
-- Adds speaker attribution and audio timestamp columns to segments,
-- enabling podcast-specific workflows: diarization, speaker-turn
-- segmentation, and timestamped citations.

-- Audio timestamps (seconds into the source audio file)
ALTER TABLE segment ADD COLUMN audio_start_sec REAL DEFAULT NULL;
ALTER TABLE segment ADD COLUMN audio_end_sec REAL DEFAULT NULL;

-- Speaker attribution from diarization
ALTER TABLE segment ADD COLUMN speaker TEXT DEFAULT NULL;
