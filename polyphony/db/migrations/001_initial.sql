-- polyphony database schema v1
-- Run automatically by polyphony/db/connection.py on first use.
-- Do not edit manually; create 002_*.sql for schema changes.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────────────────────
-- SCHEMA VERSION TRACKING
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema_migration (
    version     INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────────────
-- PROJECT
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS project (
    id                  INTEGER PRIMARY KEY,
    name                TEXT    NOT NULL,
    slug                TEXT    NOT NULL UNIQUE,
    description         TEXT,
    methodology         TEXT    NOT NULL DEFAULT 'grounded_theory',
    -- 'grounded_theory' | 'thematic_analysis' | 'content_analysis'
    research_questions  TEXT,   -- JSON array of strings
    status              TEXT    NOT NULL DEFAULT 'setup',
    -- 'setup' | 'importing' | 'inducing' | 'calibrating'
    -- | 'coding' | 'irr' | 'discussing' | 'analyzing' | 'done'
    config              TEXT    NOT NULL DEFAULT '{}',  -- JSON config overrides
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────────────
-- AGENTS (human + LLM coders)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    role            TEXT    NOT NULL,
    -- 'supervisor' | 'coder_a' | 'coder_b'
    agent_type      TEXT    NOT NULL,
    -- 'human' | 'llm'
    model_name      TEXT,   -- e.g. 'llama3.1:8b'
    model_version   TEXT,   -- Ollama manifest digest (sha256:...)
    temperature     REAL    DEFAULT 0.1,
    seed            INTEGER DEFAULT 42,
    system_prompt   TEXT,   -- snapshot of system prompt at creation
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, role)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- DOCUMENTS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS document (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    filename        TEXT    NOT NULL,
    source_path     TEXT,
    content         TEXT    NOT NULL,
    content_hash    TEXT    NOT NULL,   -- SHA-256 of raw content
    char_count      INTEGER NOT NULL,
    word_count      INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'imported',
    -- 'imported' | 'segmented' | 'coded' | 'reviewed'
    metadata        TEXT    DEFAULT '{}',  -- JSON: author, date, source, etc.
    imported_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────────────
-- SEGMENTS (units of analysis)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS segment (
    id              INTEGER PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES document(id),
    project_id      INTEGER NOT NULL REFERENCES project(id),
    segment_index   INTEGER NOT NULL,  -- ordinal within document
    text            TEXT    NOT NULL,
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL,
    segment_hash    TEXT    NOT NULL,  -- SHA-256 of text; for replication
    is_calibration  INTEGER NOT NULL DEFAULT 0,  -- 1 = part of calibration set
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(document_id, segment_index)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- CODEBOOK
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS codebook_version (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    version         INTEGER NOT NULL,
    stage           TEXT    NOT NULL DEFAULT 'draft',
    -- 'draft' | 'calibrated' | 'final'
    rationale       TEXT,   -- notes on why this version exists
    created_by      INTEGER REFERENCES agent(id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(project_id, version)
);

CREATE TABLE IF NOT EXISTS code (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    codebook_version_id INTEGER NOT NULL REFERENCES codebook_version(id),
    parent_id       INTEGER REFERENCES code(id),  -- NULL = top-level
    level           TEXT    NOT NULL DEFAULT 'open',
    -- 'open' | 'axial' | 'selective'
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    inclusion_criteria  TEXT,
    exclusion_criteria  TEXT,
    example_quotes  TEXT    DEFAULT '[]',   -- JSON array of strings
    is_active       INTEGER NOT NULL DEFAULT 1,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(codebook_version_id, name)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- CODING RUNS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS coding_run (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    codebook_version_id INTEGER NOT NULL REFERENCES codebook_version(id),
    agent_id        INTEGER NOT NULL REFERENCES agent(id),
    run_type        TEXT    NOT NULL,
    -- 'induction' | 'calibration' | 'independent' | 'revision'
    status          TEXT    NOT NULL DEFAULT 'pending',
    -- 'pending' | 'running' | 'complete' | 'error'
    started_at      TEXT,
    completed_at    TEXT,
    segment_count   INTEGER,
    error_message   TEXT
);

-- ─────────────────────────────────────────────────────────────────────────────
-- CODE ASSIGNMENTS (each coder's coding decisions)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS assignment (
    id              INTEGER PRIMARY KEY,
    coding_run_id   INTEGER NOT NULL REFERENCES coding_run(id),
    segment_id      INTEGER NOT NULL REFERENCES segment(id),
    code_id         INTEGER NOT NULL REFERENCES code(id),
    agent_id        INTEGER NOT NULL REFERENCES agent(id),
    confidence      REAL    DEFAULT NULL,   -- 0.0–1.0, self-reported
    rationale       TEXT,                   -- agent's reasoning
    is_primary      INTEGER NOT NULL DEFAULT 1,  -- 0 = secondary code
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(coding_run_id, segment_id, code_id, agent_id)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- LLM CALL LOG (full audit trail for replicability)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS llm_call (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    agent_id        INTEGER NOT NULL REFERENCES agent(id),
    call_type       TEXT    NOT NULL,
    -- 'induction' | 'calibration' | 'coding' | 'discussion' | 'memo' | 'analysis'
    model_name      TEXT    NOT NULL,
    model_version   TEXT    NOT NULL DEFAULT 'unknown',  -- Ollama digest
    temperature     REAL    NOT NULL,
    seed            INTEGER NOT NULL,
    system_prompt   TEXT    NOT NULL,
    user_prompt     TEXT    NOT NULL,
    full_response   TEXT    NOT NULL,
    parsed_output   TEXT    DEFAULT NULL,   -- JSON of structured extraction
    input_tokens    INTEGER DEFAULT NULL,
    output_tokens   INTEGER DEFAULT NULL,
    duration_ms     INTEGER DEFAULT NULL,
    error           TEXT    DEFAULT NULL,   -- NULL if successful
    called_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Nullable links to what this call produced
    assignment_id   INTEGER REFERENCES assignment(id),
    flag_id         INTEGER REFERENCES flag(id),
    memo_id         INTEGER REFERENCES memo(id)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- INTER-RATER RELIABILITY
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS irr_run (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    coding_run_a_id INTEGER NOT NULL REFERENCES coding_run(id),
    coding_run_b_id INTEGER NOT NULL REFERENCES coding_run(id),
    scope           TEXT    NOT NULL DEFAULT 'all',
    -- 'all' | 'calibration' | 'code:<name>'
    krippendorff_alpha  REAL,
    cohen_kappa         REAL,
    percent_agreement   REAL,
    segment_count       INTEGER,
    disagreement_count  INTEGER,
    computed_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS irr_disagreement (
    id              INTEGER PRIMARY KEY,
    irr_run_id      INTEGER NOT NULL REFERENCES irr_run(id),
    segment_id      INTEGER NOT NULL REFERENCES segment(id),
    code_a          TEXT,   -- code name assigned by agent A (or NULL = not coded)
    code_b          TEXT,   -- code name assigned by agent B (or NULL = not coded)
    resolution      TEXT    DEFAULT NULL,
    -- 'flag_raised' | 'accepted_a' | 'accepted_b' | 'merged' | 'supervisor_override'
    resolved_at     TEXT    DEFAULT NULL
);

-- ─────────────────────────────────────────────────────────────────────────────
-- FLAGS (cases raised for discussion)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS flag (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    raised_by       INTEGER NOT NULL REFERENCES agent(id),
    segment_id      INTEGER REFERENCES segment(id),
    code_id         INTEGER REFERENCES code(id),
    flag_type       TEXT    NOT NULL,
    -- 'ambiguous_segment' | 'code_overlap' | 'missing_code'
    -- | 'low_confidence' | 'irr_disagreement' | 'supervisor_query'
    description     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'open',
    -- 'open' | 'in_discussion' | 'resolved' | 'deferred'
    resolution      TEXT    DEFAULT NULL,
    resolved_by     INTEGER REFERENCES agent(id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT    DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS discussion_turn (
    id              INTEGER PRIMARY KEY,
    flag_id         INTEGER NOT NULL REFERENCES flag(id),
    agent_id        INTEGER NOT NULL REFERENCES agent(id),
    turn_index      INTEGER NOT NULL,
    content         TEXT    NOT NULL,
    llm_call_id     INTEGER REFERENCES llm_call(id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────────────
-- MEMOS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS memo (
    id              INTEGER PRIMARY KEY,
    project_id      INTEGER NOT NULL REFERENCES project(id),
    author_id       INTEGER NOT NULL REFERENCES agent(id),
    memo_type       TEXT    NOT NULL,
    -- 'theoretical' | 'methodological' | 'reflexivity'
    -- | 'code_definition' | 'synthesis' | 'analytic'
    title           TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    linked_codes    TEXT    DEFAULT '[]',    -- JSON array of code IDs
    linked_segments TEXT    DEFAULT '[]',    -- JSON array of segment IDs
    linked_flags    TEXT    DEFAULT '[]',    -- JSON array of flag IDs
    tags            TEXT    DEFAULT '[]',    -- JSON array of strings
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────────────
-- INDEXES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_segment_project    ON segment(project_id);
CREATE INDEX IF NOT EXISTS idx_segment_cal        ON segment(project_id, is_calibration);
CREATE INDEX IF NOT EXISTS idx_assignment_run     ON assignment(coding_run_id);
CREATE INDEX IF NOT EXISTS idx_assignment_segment ON assignment(segment_id);
CREATE INDEX IF NOT EXISTS idx_assignment_agent   ON assignment(agent_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_project   ON llm_call(project_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_agent     ON llm_call(agent_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_type      ON llm_call(call_type);
CREATE INDEX IF NOT EXISTS idx_flag_status        ON flag(project_id, status);
CREATE INDEX IF NOT EXISTS idx_memo_project       ON memo(project_id);
CREATE INDEX IF NOT EXISTS idx_code_version       ON code(codebook_version_id);
