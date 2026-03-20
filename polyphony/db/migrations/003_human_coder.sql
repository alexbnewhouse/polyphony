-- Migration 003: Human-as-lead-coder support
-- Adds columns for 3-way IRR and supervisor coding

-- irr_run: support for third coder and 3-way metrics
ALTER TABLE irr_run ADD COLUMN coding_run_c_id INTEGER REFERENCES coding_run(id);
ALTER TABLE irr_run ADD COLUMN krippendorff_alpha_3way REAL;
ALTER TABLE irr_run ADD COLUMN cohen_kappa_a_sup REAL;
ALTER TABLE irr_run ADD COLUMN cohen_kappa_b_sup REAL;

-- irr_disagreement: supervisor's codes
ALTER TABLE irr_disagreement ADD COLUMN code_c TEXT;
