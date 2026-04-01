"""
polyphony.io.exporters
=================
Export project data in various formats.

Export targets:
  - codebook (CSV, JSON, YAML)
  - assignments (CSV, JSON)
  - memos (Markdown, JSON)
  - LLM audit log (JSONL)
  - Full replication package (directory)
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console

from ..db import fetchall, fetchone, from_json

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Codebook export
# ─────────────────────────────────────────────────────────────────────────────


def export_codebook(
    conn: sqlite3.Connection,
    project_id: int,
    output_path: Path,
    format: str = "yaml",
    version: Optional[int] = None,
) -> None:
    """Export the codebook (latest or specified version) to file."""
    if version:
        cb = fetchone(
            conn,
            "SELECT * FROM codebook_version WHERE project_id = ? AND version = ?",
            (project_id, version),
        )
    else:
        cb = fetchone(
            conn,
            "SELECT * FROM codebook_version WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        )

    if not cb:
        raise ValueError("No codebook found for this project.")

    codes = fetchall(
        conn,
        "SELECT * FROM code WHERE codebook_version_id = ? ORDER BY level, sort_order",
        (cb["id"],),
    )

    codebook_data = {
        "codebook_version": cb["version"],
        "stage": cb["stage"],
        "rationale": cb["rationale"],
        "created_at": cb["created_at"],
        "codes": [
            {
                "name": c["name"],
                "level": c["level"],
                "description": c["description"],
                "inclusion_criteria": c["inclusion_criteria"],
                "exclusion_criteria": c["exclusion_criteria"],
                "example_quotes": from_json(c["example_quotes"], []),
                "is_active": bool(c["is_active"]),
                "parent_id": c["parent_id"],
            }
            for c in codes
            if c["is_active"]
        ],
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if format == "yaml":
        output_path.write_text(yaml.dump(codebook_data, allow_unicode=True, sort_keys=False))
    elif format == "json":
        output_path.write_text(json.dumps(codebook_data, indent=2, ensure_ascii=False))
    elif format == "csv":
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["name", "level", "description", "inclusion_criteria",
                            "exclusion_criteria", "is_active"],
            )
            writer.writeheader()
            for c in codebook_data["codes"]:
                writer.writerow({k: c[k] for k in writer.fieldnames})
    else:
        raise ValueError(f"Unknown format: {format}")

    console.print(f"[green]Codebook v{cb['version']} exported → {output_path}[/]")


# ─────────────────────────────────────────────────────────────────────────────
# Assignments export
# ─────────────────────────────────────────────────────────────────────────────


def export_assignments(
    conn: sqlite3.Connection,
    project_id: int,
    output_path: Path,
    format: str = "csv",
    agent_filter: Optional[str] = None,  # 'a', 'b', 'supervisor', or None for all
) -> None:
    """Export all coding assignments to CSV or JSON."""
    query = """
        SELECT
            a.id AS assignment_id,
            seg.id AS segment_id,
            seg.segment_index,
            doc.filename,
            c.name AS code_name,
            c.level AS code_level,
            ag.role AS agent_role,
            a.confidence,
            a.rationale,
            a.is_primary,
            r.run_type,
            a.created_at
        FROM assignment a
        JOIN segment seg ON seg.id = a.segment_id
        JOIN document doc ON doc.id = seg.document_id
        JOIN code c ON c.id = a.code_id
        JOIN agent ag ON ag.id = a.agent_id
        JOIN coding_run r ON r.id = a.coding_run_id
        WHERE r.project_id = ?
    """
    params = [project_id]
    if agent_filter == "a":
        query += " AND ag.role = 'coder_a'"
    elif agent_filter == "b":
        query += " AND ag.role = 'coder_b'"
    elif agent_filter == "supervisor":
        query += " AND ag.role = 'supervisor'"
    query += " ORDER BY seg.id, ag.role, c.name"

    rows = fetchall(conn, query, tuple(params))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if format == "csv":
        with output_path.open("w", newline="", encoding="utf-8") as f:
            if not rows:
                f.write("(no assignments)\n")
                return
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    elif format == "json":
        output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))

    console.print(f"[green]{len(rows)} assignments exported → {output_path}[/]")


# ─────────────────────────────────────────────────────────────────────────────
# Memos export
# ─────────────────────────────────────────────────────────────────────────────


def export_memos(
    conn: sqlite3.Connection,
    project_id: int,
    output_dir: Path,
    format: str = "md",
) -> None:
    """Export memos as Markdown files or a single JSON."""
    memos = fetchall(
        conn,
        "SELECT m.*, a.role AS author_role FROM memo m JOIN agent a ON a.id = m.author_id WHERE m.project_id = ? ORDER BY m.created_at",
        (project_id,),
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if format == "md":
        for memo in memos:
            safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in memo["title"])
            fname = output_dir / f"memo_{memo['id']:03d}_{safe_title[:30]}.md"
            content = (
                f"# {memo['title']}\n\n"
                f"**Type:** {memo['memo_type']}  \n"
                f"**Author:** {memo['author_role']}  \n"
                f"**Created:** {memo['created_at']}  \n\n"
                f"---\n\n{memo['content']}\n"
            )
            fname.write_text(content, encoding="utf-8")
        console.print(f"[green]{len(memos)} memos exported → {output_dir}/[/]")
    elif format == "json":
        out = output_dir / "memos.json"
        out.write_text(json.dumps(memos, indent=2, ensure_ascii=False))
        console.print(f"[green]{len(memos)} memos exported → {out}[/]")


# ─────────────────────────────────────────────────────────────────────────────
# LLM audit log export
# ─────────────────────────────────────────────────────────────────────────────


def export_llm_log(
    conn: sqlite3.Connection,
    project_id: int,
    output_path: Path,
    call_type: Optional[str] = None,
    agent_role: Optional[str] = None,
) -> None:
    """Export LLM calls as JSONL (one JSON object per line)."""
    query = """
        SELECT l.*, a.role AS agent_role
        FROM llm_call l
        JOIN agent a ON a.id = l.agent_id
        WHERE l.project_id = ?
    """
    params = [project_id]
    if call_type:
        query += " AND l.call_type = ?"
        params.append(call_type)
    if agent_role:
        query += " AND a.role = ?"
        params.append(agent_role)
    query += " ORDER BY l.called_at"

    rows = fetchall(conn, query, tuple(params))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    console.print(f"[green]{len(rows)} LLM calls exported → {output_path}[/]")


# ─────────────────────────────────────────────────────────────────────────────
# Replication package
# ─────────────────────────────────────────────────────────────────────────────


def export_replication_package(
    conn: sqlite3.Connection,
    project_id: int,
    output_dir: Path,
) -> None:
    """
    Generate a full replication package directory.
    Includes: codebook versions, assignments, IRR, memos, LLM audit log,
    prompt snapshots, agent configs, verify/rerun scripts, and MANIFEST.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    project = fetchone(conn, "SELECT * FROM project WHERE id = ?", (project_id,))
    if not project:
        raise ValueError(f"Project {project_id} not found.")

    console.print(f"\n[bold cyan]Generating replication package → {output_dir}[/]\n")

    # ── 1. data/ ─────────────────────────────────────────────────────────────
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)

    docs = fetchall(conn, "SELECT id, filename, content_hash, char_count, word_count, metadata, media_type, image_path FROM document WHERE project_id = ?", (project_id,))
    _write_csv(data_dir / "documents.csv", docs)

    segs = fetchall(conn, "SELECT id, document_id, segment_index, text, segment_hash, is_calibration, media_type, image_path FROM segment WHERE project_id = ? ORDER BY document_id, segment_index", (project_id,))
    _write_csv(data_dir / "segments.csv", segs)

    # Copy image files into the replication package
    image_segs = [s for s in segs if s.get("media_type") == "image" and s.get("image_path")]
    if image_segs:
        images_dir = output_dir / "images"
        images_dir.mkdir(exist_ok=True)
        for seg in image_segs:
            src = Path(seg["image_path"])
            if src.exists():
                shutil.copy2(src, images_dir / src.name)
        console.print(f"  images/: {len(image_segs)} image(s) copied")

    console.print(f"  data/: {len(docs)} documents, {len(segs)} segments")

    # ── 2. codebook/ ─────────────────────────────────────────────────────────
    cb_dir = output_dir / "codebook"
    cb_dir.mkdir(exist_ok=True)
    cb_versions = fetchall(conn, "SELECT * FROM codebook_version WHERE project_id = ? ORDER BY version", (project_id,))
    for cb in cb_versions:
        fname = cb_dir / f"codebook_v{cb['version']}_{cb['stage']}.yaml"
        export_codebook(conn, project_id, fname, format="yaml", version=cb["version"])
    console.print(f"  codebook/: {len(cb_versions)} version(s)")

    # ── 3. coding/ ────────────────────────────────────────────────────────────
    coding_dir = output_dir / "coding"
    coding_dir.mkdir(exist_ok=True)
    export_assignments(conn, project_id, coding_dir / "assignments_agent_a.csv", format="csv", agent_filter="a")
    export_assignments(conn, project_id, coding_dir / "assignments_agent_b.csv", format="csv", agent_filter="b")
    # Export supervisor assignments if any exist
    sup_check = fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM assignment a JOIN agent ag ON ag.id = a.agent_id "
        "JOIN coding_run r ON r.id = a.coding_run_id WHERE r.project_id = ? AND ag.role = 'supervisor'",
        (project_id,),
    )
    if sup_check and sup_check["n"] > 0:
        export_assignments(conn, project_id, coding_dir / "assignments_supervisor.csv", format="csv", agent_filter="supervisor")
    export_assignments(conn, project_id, coding_dir / "assignments_all.csv", format="csv")

    # ── 4. irr/ ───────────────────────────────────────────────────────────────
    irr_dir = output_dir / "irr"
    irr_dir.mkdir(exist_ok=True)
    irr_runs = fetchall(conn, "SELECT * FROM irr_run WHERE project_id = ? ORDER BY computed_at", (project_id,))
    _write_csv(irr_dir / "irr_summary.csv", irr_runs)
    disagreements = fetchall(
        conn,
        "SELECT d.*, r.scope FROM irr_disagreement d JOIN irr_run r ON r.id = d.irr_run_id WHERE r.project_id = ?",
        (project_id,),
    )
    _write_csv(irr_dir / "irr_disagreements.csv", disagreements)
    console.print(f"  irr/: {len(irr_runs)} IRR run(s), {len(disagreements)} disagreements")

    # ── 5. discussion/ ───────────────────────────────────────────────────────
    disc_dir = output_dir / "discussion"
    disc_dir.mkdir(exist_ok=True)
    flags = fetchall(conn, "SELECT * FROM flag WHERE project_id = ? ORDER BY created_at", (project_id,))
    _write_csv(disc_dir / "flags.csv", flags)
    turns = fetchall(
        conn,
        "SELECT dt.* FROM discussion_turn dt JOIN flag f ON f.id = dt.flag_id WHERE f.project_id = ? ORDER BY dt.flag_id, dt.turn_index",
        (project_id,),
    )
    _write_csv(disc_dir / "discussion_turns.csv", turns)
    console.print(f"  discussion/: {len(flags)} flags, {len(turns)} discussion turns")

    # ── 6. memos/ ─────────────────────────────────────────────────────────────
    export_memos(conn, project_id, output_dir / "memos", format="md")

    # ── 7. llm_audit/ ────────────────────────────────────────────────────────
    audit_dir = output_dir / "llm_audit"
    audit_dir.mkdir(exist_ok=True)
    export_llm_log(conn, project_id, audit_dir / "llm_calls.jsonl")

    agents = fetchall(conn, "SELECT * FROM agent WHERE project_id = ?", (project_id,))
    agent_config = {
        a["role"]: {
            "model_name": a["model_name"],
            "model_version": a["model_version"],
            "temperature": a["temperature"],
            "seed": a["seed"],
        }
        for a in agents
    }
    (audit_dir / "agent_configs.yaml").write_text(
        yaml.dump(agent_config, allow_unicode=True), encoding="utf-8"
    )

    # ── 8. prompts/ (snapshot) ───────────────────────────────────────────────
    prompts_src = Path(__file__).parent.parent / "prompt_templates"
    prompts_dst = output_dir / "prompts"
    if prompts_src.exists():
        shutil.copytree(prompts_src, prompts_dst, dirs_exist_ok=True)
        console.print(f"  prompts/: snapshot copied")

    # ── 9. scripts/ ───────────────────────────────────────────────────────────
    _write_replication_scripts(output_dir / "scripts")

    # ── 10. MANIFEST.json ─────────────────────────────────────────────────────
    from .. import __version__
    n_llm_calls = fetchone(conn, "SELECT COUNT(*) AS n FROM llm_call WHERE project_id = ?", (project_id,))["n"]
    final_irr = irr_runs[-1] if irr_runs else {}
    latest_cb = cb_versions[-1] if cb_versions else {}

    manifest = {
        "polyphony_version": __version__,
        "export_date": datetime.now(timezone.utc).isoformat(),
        "project_name": project["name"],
        "project_slug": project["slug"],
        "methodology": project["methodology"],
        "agents": agent_config,
        "corpus_stats": {
            "document_count": len(docs),
            "segment_count": len(segs),
            "total_words": sum(d.get("word_count", 0) for d in docs),
            "image_count": sum(1 for d in docs if d.get("media_type") == "image"),
            "text_count": sum(1 for d in docs if d.get("media_type", "text") == "text"),
        },
        "final_irr": {
            "krippendorff_alpha": final_irr.get("krippendorff_alpha"),
            "krippendorff_alpha_3way": final_irr.get("krippendorff_alpha_3way"),
            "cohen_kappa": final_irr.get("cohen_kappa"),
            "cohen_kappa_a_sup": final_irr.get("cohen_kappa_a_sup"),
            "cohen_kappa_b_sup": final_irr.get("cohen_kappa_b_sup"),
            "percent_agreement": final_irr.get("percent_agreement"),
        },
        "codebook_final_version": latest_cb.get("version"),
        "code_count": fetchone(
            conn,
            "SELECT COUNT(*) AS n FROM code WHERE codebook_version_id = ? AND is_active = 1",
            (latest_cb.get("id", 0),),
        )["n"] if latest_cb else 0,
        "memo_count": fetchone(conn, "SELECT COUNT(*) AS n FROM memo WHERE project_id = ?", (project_id,))["n"],
        "llm_call_count": n_llm_calls,
        "checksums": _compute_checksums(output_dir),
    }

    (output_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)
    )

    # ── README ────────────────────────────────────────────────────────────────
    _write_replication_readme(output_dir, project, manifest)

    console.print(f"\n[green]✓ Replication package complete → {output_dir}[/]")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _write_csv(path: Path, rows: list) -> None:
    if not rows:
        path.write_text("(empty)\n")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _compute_checksums(directory: Path) -> dict:
    checksums = {}
    for f in sorted(directory.rglob("*")):
        if f.is_file() and f.name != "MANIFEST.json":
            rel = str(f.relative_to(directory))
            checksums[rel] = "sha256:" + hashlib.sha256(f.read_bytes()).hexdigest()
    return checksums


def _write_replication_scripts(scripts_dir: Path) -> None:
    scripts_dir.mkdir(exist_ok=True)

    verify = '''#!/usr/bin/env python3
"""Verify segment checksums against data/segments.csv."""
import csv, hashlib, sys
from pathlib import Path

def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()

ok = errors = 0
with open(Path(__file__).parent.parent / "data" / "segments.csv") as f:
    for row in csv.DictReader(f):
        expected = row.get("segment_hash", "")
        actual = sha256(row.get("text", ""))
        if expected == actual:
            ok += 1
        else:
            print(f"MISMATCH segment {row['id']}: expected {expected[:8]}... got {actual[:8]}...")
            errors += 1

print(f"\\n{ok} OK, {errors} errors.")
sys.exit(1 if errors else 0)
'''
    (scripts_dir / "verify_checksums.py").write_text(verify)

    rerun = '''#!/usr/bin/env python3
"""Re-run a single LLM call by ID from llm_calls.jsonl.
Usage: python rerun_single_call.py <call_id> [--model-override <model>]

Requires Ollama running locally.
"""
import argparse, json, sys
try:
    import ollama
except ImportError:
    print("pip install ollama")
    sys.exit(1)

parser = argparse.ArgumentParser()
parser.add_argument("call_id", type=int)
parser.add_argument("--model-override", default=None)
args = parser.parse_args()

calls_file = __file__.replace("scripts/rerun_single_call.py", "llm_audit/llm_calls.jsonl")
call = None
with open(calls_file) as f:
    for line in f:
        obj = json.loads(line)
        if obj["id"] == args.call_id:
            call = obj
            break

if not call:
    print(f"Call {args.call_id} not found.")
    sys.exit(1)

model = args.model_override or call["model_name"]
client = ollama.Client()
resp = client.chat(
    model=model,
    messages=[
        {"role": "system", "content": call["system_prompt"]},
        {"role": "user", "content": call["user_prompt"]},
    ],
    options={"temperature": call["temperature"], "seed": call["seed"]},
)
print("=== ORIGINAL RESPONSE ===")
print(call["full_response"])
print("\\n=== RERUN RESPONSE ===")
print(resp.message.content)
'''
    (scripts_dir / "rerun_single_call.py").write_text(rerun)

    irr_script = '''#!/usr/bin/env python3
"""Recompute IRR from assignments CSVs (no database needed).
Usage: python compute_irr.py
"""
import csv, json
from pathlib import Path

base = Path(__file__).parent.parent / "coding"

def load_assignments(path):
    result = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            sid = int(row["segment_id"])
            result.setdefault(sid, set()).add(row["code_name"])
    return result

a = load_assignments(base / "assignments_agent_a.csv")
b = load_assignments(base / "assignments_agent_b.csv")
all_segs = sorted(set(a).intersection(set(b)))
agree = sum(1 for s in all_segs if a.get(s, set()) == b.get(s, set()))
pct = agree / len(all_segs) if all_segs else 0
print(f"Segments: {len(all_segs)}")
print(f"Agreement: {agree}/{len(all_segs)} = {pct:.1%}")
print("(Install krippendorff + scikit-learn for alpha/kappa)")
try:
    import numpy as np, krippendorff
    from sklearn.metrics import cohen_kappa_score
    all_codes = sorted(set(c for s in a.values() for c in s) |
                       set(c for s in b.values() for c in s))
    units_a = [1 if c in a.get(s, set()) else 0 for c in all_codes for s in all_segs]
    units_b = [1 if c in b.get(s, set()) else 0 for c in all_codes for s in all_segs]
    alpha = krippendorff.alpha(np.array([units_a, units_b], dtype=float), level_of_measurement="nominal")
    kappas = []
    for c in all_codes:
        ya = [1 if c in a.get(s, set()) else 0 for s in all_segs]
        yb = [1 if c in b.get(s, set()) else 0 for s in all_segs]
        if sum(ya) + sum(yb) > 0:
            kappas.append(cohen_kappa_score(ya, yb))
    kappa = sum(kappas) / len(kappas) if kappas else float("nan")
    print(f"Krippendorff alpha: {alpha:.3f}")
    print(f"Cohen kappa (avg):  {kappa:.3f}")
except ImportError:
    pass
'''
    (scripts_dir / "compute_irr.py").write_text(irr_script)
    console.print(f"  scripts/: 3 utility scripts written")


def _write_replication_readme(output_dir: Path, project: dict, manifest: dict) -> None:
    readme = f"""# Replication Package: {project['name']}

Generated by polyphony v{manifest['polyphony_version']} on {manifest['export_date'][:10]}.

## Project Overview

- **Methodology**: {project['methodology'].replace('_', ' ').title()}
- **Documents**: {manifest['corpus_stats']['document_count']}
- **Segments**: {manifest['corpus_stats']['segment_count']}
- **Codes**: {manifest['code_count']}

## Inter-rater Reliability (Final)

| Metric | Value |
|--------|-------|
| Krippendorff's α | {manifest['final_irr'].get('krippendorff_alpha', 'N/A')} |
| Cohen's κ | {manifest['final_irr'].get('cohen_kappa', 'N/A')} |
| % Agreement | {manifest['final_irr'].get('percent_agreement', 'N/A')} |

## Contents

| Directory | Contents |
|-----------|---------|
| `data/` | Document and segment lists with hashes |
| `images/` | Image files used in the corpus (if any) |
| `codebook/` | All codebook versions (YAML) |
| `coding/` | Code assignments for each agent and merged |
| `irr/` | IRR results and disagreements |
| `discussion/` | Flags raised and discussion transcripts |
| `memos/` | Analytical memos |
| `llm_audit/` | Full LLM call log (JSONL) with prompts and responses |
| `prompts/` | Prompt templates used |
| `scripts/` | Verification and rerun utilities |

## Agents

```yaml
{yaml.dump(manifest['agents'], allow_unicode=True, sort_keys=False).strip()}
```

## How to Verify

```bash
python scripts/verify_checksums.py
```

## How to Re-run a Single LLM Call

```bash
python scripts/rerun_single_call.py <call_id>
```

## How to Recompute IRR

```bash
python scripts/compute_irr.py
```

## Software Requirements

```
pip install polyphony  # or: pip install ollama krippendorff scikit-learn
```
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
