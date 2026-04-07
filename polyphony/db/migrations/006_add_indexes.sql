-- Performance indexes on frequently-queried foreign keys.
CREATE INDEX IF NOT EXISTS idx_assignment_segment_id ON assignment(segment_id);
CREATE INDEX IF NOT EXISTS idx_assignment_code_id ON assignment(code_id);
CREATE INDEX IF NOT EXISTS idx_assignment_coding_run_id ON assignment(coding_run_id);
CREATE INDEX IF NOT EXISTS idx_segment_document_id ON segment(document_id);
CREATE INDEX IF NOT EXISTS idx_segment_project_calibration ON segment(project_id, is_calibration);
CREATE INDEX IF NOT EXISTS idx_code_codebook_version_id ON code(codebook_version_id);
CREATE INDEX IF NOT EXISTS idx_flag_project_status ON flag(project_id, status);
CREATE INDEX IF NOT EXISTS idx_coding_run_project_id ON coding_run(project_id);
CREATE INDEX IF NOT EXISTS idx_memo_project_id ON memo(project_id);
