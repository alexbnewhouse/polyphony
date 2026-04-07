"""
polyphony.pipeline.coding
====================
Independent coding pipeline.

Each agent codes every segment using the active codebook version.
Agents may run in parallel (each with its own DB connection) when
launched from the calibration or induction pipelines.

Independence is enforced: during a run, each agent's prompt contains only
the codebook and the target segment — never the other agent's assignments.
"""

from __future__ import annotations

import functools
import json
import sqlite3
from typing import List, Optional

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from ..db import fetchall, fetchone, insert, json_col
from ..prompts import library as prompt_lib, format_codebook

console = Console()

# Simple cache for formatted codebook text keyed by frozen code IDs.
_codebook_cache: dict[frozenset, str] = {}


def _cached_format_codebook(codes: list[dict]) -> str:
    """Return format_codebook(codes) with caching by code IDs."""
    key = frozenset((c["id"], c.get("name", "")) for c in codes)
    cached = _codebook_cache.get(key)
    if cached is not None:
        return cached
    result = format_codebook(codes)
    _codebook_cache[key] = result
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Core segment coding function
# ─────────────────────────────────────────────────────────────────────────────


def code_segment(
    agent,
    segment: dict,
    codes: List[dict],
    project: dict,
    coding_run_id: int,
    conn: sqlite3.Connection,
    document_name: str = "unknown",
    total_segments: int = 1,
    prompt_key: str = "open_coding",
) -> List[dict]:
    """
    Have an agent code a single segment.
    Returns a list of assignment dicts with confidence, rationale, flags.
    Saves assignments to DB.
    """
    tmpl = prompt_lib[prompt_key]
    codebook_text = _cached_format_codebook(codes)

    research_questions = json.loads(project.get("research_questions") or "[]")
    rq_text = "\n".join(f"  - {q}" for q in research_questions) or "(not specified)"

    run_row = fetchone(conn, "SELECT codebook_version_id FROM coding_run WHERE id = ?",
                       (coding_run_id,))
    if not run_row or not run_row["codebook_version_id"]:
        raise ValueError(f"Coding run {coding_run_id} has no associated codebook version.")
    cb_row = fetchone(conn, "SELECT version FROM codebook_version WHERE id = ?",
                      (run_row["codebook_version_id"],))
    if not cb_row:
        raise ValueError(f"Codebook version {run_row['codebook_version_id']} not found.")

    # Handle image vs text segments
    is_image = segment.get("media_type") == "image"
    image_path = segment.get("image_path")

    if is_image:
        segment_content = (
            f"[This segment is an image. The image is provided as an attachment. "
            f"Filename: {document_name}]\n"
            f"Analyze the visual content of the attached image and apply codes."
        )
    else:
        segment_content = segment["text"]

    system, user = tmpl.render(
        methodology=project["methodology"],
        research_question=rq_text,
        codebook_version=cb_row["version"],
        codebook_formatted=codebook_text,
        document_filename=document_name,
        segment_index=segment["segment_index"],
        total_segments=total_segments,
        segment_text=segment_content,
    )

    images = [image_path] if is_image and image_path else None
    
    if getattr(agent, "model_name", "") == "human" and hasattr(agent, "code_segment"):
        import time
        start = time.time()
        assignments_temp = agent.code_segment(
            segment_text=segment_content,
            codes=codes,
            document_name=document_name,
            segment_idx=segment["segment_index"],
            total_segments=total_segments,
            image_path=image_path if is_image else None,
        )
        assignments_raw = []
        flags_raw = []
        for asgn in assignments_temp:
            if asgn.get("flag"):
                flags_raw.append({
                    "flag_type": "human_flag",
                    "description": asgn.get("reason", "")
                })
            else:
                assignments_raw.append(asgn)
        parsed = {"assignments": assignments_raw, "flags": flags_raw}
        duration_ms = int((time.time() - start) * 1000)
        
        logged_user_prompt = user
        if images:
            logged_user_prompt += "\n\n[Images: " + ", ".join(images) + "]"
            
        call_id = agent._log_call(
            call_type="coding",
            system_prompt=system,
            user_prompt=logged_user_prompt,
            full_response=json.dumps(parsed),
            parsed_output=parsed,
            duration_ms=duration_ms,
            error=None,
        )
    else:
        raw, parsed, call_id = agent.call("coding", system, user, images=images)
        # Parse assignments from response
        assignments_raw = parsed.get("assignments", [])
        flags_raw = parsed.get("flags", [])
    saved_assignments = []

    code_name_to_id = {c["name"]: c["id"] for c in codes}

    for asgn in assignments_raw:
        code_name = asgn.get("code_name", "")
        if code_name == "UNCODED" or not code_name:
            continue
        code_id = code_name_to_id.get(code_name)
        if code_id is None:
            # Agent invented a code not in the codebook — flag it
            _raise_flag(conn, project["id"], agent.agent_id, segment["id"],
                        "missing_code", f"Agent used unknown code '{code_name}'")
            continue

        asgn_id = insert(conn, "assignment", {
            "coding_run_id": coding_run_id,
            "segment_id": segment["id"],
            "code_id": code_id,
            "agent_id": agent.agent_id,
            "confidence": asgn.get("confidence"),
            "rationale": asgn.get("rationale", ""),
            "is_primary": 1 if asgn.get("is_primary", True) else 0,
        })
        saved_assignments.append({"assignment_id": asgn_id, "code_name": code_name})

        # Link llm_call → assignment
        agent.update_call_link(call_id, assignment_id=asgn_id)

    # Handle flags raised by the agent
    for flag in flags_raw:
        _raise_flag(
            conn, project["id"], agent.agent_id, segment["id"],
            flag.get("flag_type", "ambiguous_segment"),
            flag.get("description", "Agent raised flag"),
        )

    conn.commit()
    return saved_assignments


def _raise_flag(
    conn: sqlite3.Connection,
    project_id: int,
    agent_id: int,
    segment_id: int,
    flag_type: str,
    description: str,
) -> int:
    return insert(conn, "flag", {
        "project_id": project_id,
        "raised_by": agent_id,
        "segment_id": segment_id,
        "flag_type": flag_type,
        "description": description,
        "status": "open",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Token estimation & batching
# ─────────────────────────────────────────────────────────────────────────────

# Rough chars-per-token ratio (conservative, works across models).
_CHARS_PER_TOKEN = 3.5

# Default context windows by model family (input tokens).
# These are conservative estimates; actual limits may be higher.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
    # Anthropic
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4-20250514": 200_000,
    "claude-3-7-sonnet-20250219": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
    # Ollama / local defaults
    "llama3": 8_192,
    "llama3.1": 131_072,
    "llama3.2": 131_072,
    "llama3.3": 131_072,
    "llama3:8b": 8_192,
    "llama3.1:8b": 131_072,
    "llama3.2:3b": 131_072,
    "llama3.3:70b": 131_072,
    "gemma2": 8_192,
    "gemma2:9b": 8_192,
    "gemma2:27b": 8_192,
    "gemma3": 131_072,
    "mistral": 32_768,
    "mixtral": 32_768,
    "phi3": 128_000,
    "phi4": 16_384,
    "qwen2.5": 131_072,
    "qwen3": 131_072,
    "deepseek-r1": 131_072,
    "command-r": 128_000,
}


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length using a conservative ratio."""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


@functools.lru_cache(maxsize=256)
def get_model_context_window(model_name: str) -> int:
    """
    Look up the context window for a model.
    Falls back to 8192 (safe minimum) if unknown.
    """
    if not model_name:
        return 8_192

    # Exact match
    if model_name in _MODEL_CONTEXT_WINDOWS:
        return _MODEL_CONTEXT_WINDOWS[model_name]

    # Try prefix match (e.g. "gpt-4o-2024-08-06" → "gpt-4o")
    for prefix, ctx in sorted(_MODEL_CONTEXT_WINDOWS.items(), key=lambda x: -len(x[0])):
        if model_name.startswith(prefix):
            return ctx

    # Default: conservative 8K
    return 8_192


def _build_segments_block(segments: List[dict], doc_map: dict) -> str:
    """Build the $segments_block text for the batch coding prompt."""
    parts = []
    for seg in segments:
        doc_name = doc_map.get(seg["document_id"], "unknown")
        seg_key = f"seg_{seg['id']}"
        parts.append(
            f"### [{seg_key}] Document: {doc_name}, "
            f"Segment {seg['segment_index']}\n"
            f"---\n{seg['text']}\n---"
        )
    return "\n\n".join(parts)


def create_batches(
    segments: List[dict],
    codes: List[dict],
    project: dict,
    model_name: str,
    doc_map: dict,
    prompt_key: str = "open_coding",
    codebook_version: str = "1",
) -> List[List[dict]]:
    """
    Split segments into batches that fit within the model's context window.

    Reserves space for the system prompt, codebook, and response.
    Image segments are always placed in their own single-segment batch.
    """
    context_window = get_model_context_window(model_name)
    # Reserve 30% for system prompt + codebook + response overhead
    available_tokens = int(context_window * 0.70)

    # Estimate fixed overhead: system prompt + codebook
    codebook_text = _cached_format_codebook(codes)
    research_questions = json.loads(project.get("research_questions") or "[]")
    rq_text = "\n".join(f"  - {q}" for q in research_questions) or "(not specified)"

    fixed_overhead = estimate_tokens(codebook_text) + estimate_tokens(rq_text) + 500  # prompt boilerplate

    budget = max(500, available_tokens - fixed_overhead)

    batches: List[List[dict]] = []
    current_batch: List[dict] = []
    current_tokens = 0

    for seg in segments:
        # Image segments must go alone (can't batch multimodal)
        if seg.get("media_type") == "image":
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            batches.append([seg])
            continue

        seg_tokens = estimate_tokens(seg.get("text", ""))
        seg_tokens += 50  # per-segment formatting overhead

        if current_batch and (current_tokens + seg_tokens) > budget:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(seg)
        current_tokens += seg_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def code_batch(
    agent,
    batch: List[dict],
    codes: List[dict],
    project: dict,
    coding_run_id: int,
    conn: sqlite3.Connection,
    doc_map: dict,
    total_segments: int = 1,
    prompt_key: str = "open_coding",
) -> List[dict]:
    """
    Have an agent code a batch of segments in a single LLM call.
    Returns a list of assignment dicts across all segments in the batch.
    Saves assignments to DB.
    """
    # If batch has a single image segment, fall through to single-segment coding
    if len(batch) == 1 and batch[0].get("media_type") == "image":
        document_name = doc_map.get(batch[0]["document_id"], "unknown")
        return code_segment(
            agent=agent,
            segment=batch[0],
            codes=codes,
            project=project,
            coding_run_id=coding_run_id,
            conn=conn,
            document_name=document_name,
            total_segments=total_segments,
            prompt_key=prompt_key,
        )

    # If batch has a single text segment, use original single-segment path
    if len(batch) == 1:
        document_name = doc_map.get(batch[0]["document_id"], "unknown")
        return code_segment(
            agent=agent,
            segment=batch[0],
            codes=codes,
            project=project,
            coding_run_id=coding_run_id,
            conn=conn,
            document_name=document_name,
            total_segments=total_segments,
            prompt_key=prompt_key,
        )

    # Multi-segment batch coding
    _BATCH_PROMPT_MAP = {
        "open_coding": "batch_coding",
        "deductive_coding": "batch_deductive_coding",
    }
    batch_prompt_key = _BATCH_PROMPT_MAP.get(prompt_key, f"batch_{prompt_key}")
    if prompt_key.startswith("batch_"):
        batch_prompt_key = prompt_key
    tmpl = prompt_lib[batch_prompt_key]
    codebook_text = _cached_format_codebook(codes)

    research_questions = json.loads(project.get("research_questions") or "[]")
    rq_text = "\n".join(f"  - {q}" for q in research_questions) or "(not specified)"

    run_row = fetchone(conn, "SELECT codebook_version_id FROM coding_run WHERE id = ?",
                       (coding_run_id,))
    if not run_row or not run_row["codebook_version_id"]:
        raise ValueError(f"Coding run {coding_run_id} has no associated codebook version.")
    cb_row = fetchone(conn, "SELECT version FROM codebook_version WHERE id = ?",
                      (run_row["codebook_version_id"],))
    if not cb_row:
        raise ValueError(f"Codebook version {run_row['codebook_version_id']} not found.")

    segments_block = _build_segments_block(batch, doc_map)

    system, user = tmpl.render(
        methodology=project["methodology"],
        research_question=rq_text,
        codebook_version=cb_row["version"],
        codebook_formatted=codebook_text,
        batch_size=str(len(batch)),
        segments_block=segments_block,
    )

    raw, parsed, call_id = agent.call("coding", system, user)

    # Parse per-segment results from the batch response
    seg_results = parsed.get("segments", {})
    all_saved: List[dict] = []
    code_name_to_id = {c["name"]: c["id"] for c in codes}

    for seg in batch:
        seg_key = f"seg_{seg['id']}"
        seg_data = seg_results.get(seg_key, {})
        assignments_raw = seg_data.get("assignments", [])
        flags_raw = seg_data.get("flags", [])

        for asgn in assignments_raw:
            code_name = asgn.get("code_name", "")
            if code_name == "UNCODED" or not code_name:
                continue
            code_id = code_name_to_id.get(code_name)
            if code_id is None:
                _raise_flag(conn, project["id"], agent.agent_id, seg["id"],
                            "missing_code", f"Agent used unknown code '{code_name}'")
                continue

            asgn_id = insert(conn, "assignment", {
                "coding_run_id": coding_run_id,
                "segment_id": seg["id"],
                "code_id": code_id,
                "agent_id": agent.agent_id,
                "confidence": asgn.get("confidence"),
                "rationale": asgn.get("rationale", ""),
                "is_primary": 1 if asgn.get("is_primary", True) else 0,
            })
            all_saved.append({"assignment_id": asgn_id, "code_name": code_name})
            agent.update_call_link(call_id, assignment_id=asgn_id)

        for flag in flags_raw:
            _raise_flag(
                conn, project["id"], agent.agent_id, seg["id"],
                flag.get("flag_type", "ambiguous_segment"),
                flag.get("description", "Agent raised flag"),
            )

    conn.commit()
    return all_saved


# ─────────────────────────────────────────────────────────────────────────────
# Run a full coding session for one agent
# ─────────────────────────────────────────────────────────────────────────────


def run_coding_session(
    conn: sqlite3.Connection,
    project: dict,
    agent,
    codebook_version_id: int,
    run_type: str = "independent",
    segments: Optional[List[dict]] = None,
    resume: bool = False,
    prompt_key: str = "open_coding",
    batch: bool = False,
) -> int:
    """
    Run a full coding session (or resume an interrupted one).
    Returns the coding_run_id.

    If `segments` is None, all project segments are coded.
    If `resume` is True, already-coded segments are skipped.
    If `batch` is True, segments are grouped into batches that fit the
    model's context window and coded in fewer, larger LLM calls.
    """
    project_id = project["id"]

    # Get or create coding_run
    # Check for any existing incomplete run first
    existing_run = fetchone(
        conn,
        """SELECT * FROM coding_run
           WHERE project_id = ? AND agent_id = ? AND run_type = ?
             AND codebook_version_id = ? AND status = 'running'
           ORDER BY id DESC LIMIT 1""",
        (project_id, agent.agent_id, run_type, codebook_version_id),
    )

    if resume:
        run = fetchone(
            conn,
            """SELECT * FROM coding_run
               WHERE project_id = ? AND agent_id = ? AND run_type = ?
                 AND codebook_version_id = ? AND status != 'complete'
               ORDER BY id DESC LIMIT 1""",
            (project_id, agent.agent_id, run_type, codebook_version_id),
        )
        if run:
            run_id = run["id"]
            console.print(f"[yellow]Resuming coding run {run_id}...[/]")
        else:
            run_id = _create_run(conn, project_id, agent, codebook_version_id, run_type)
    elif existing_run:
        console.print(
            f"[yellow]⚠ An incomplete coding run (id={existing_run['id']}) already exists "
            f"for {agent.role}. Use [bold]--resume[/bold] to continue it, or it will be "
            f"marked as cancelled and a new run started.[/]"
        )
        conn.execute(
            "UPDATE coding_run SET status='error', error_message='Superseded by new run' WHERE id=?",
            (existing_run["id"],),
        )
        conn.commit()
        run_id = _create_run(conn, project_id, agent, codebook_version_id, run_type)
    else:
        run_id = _create_run(conn, project_id, agent, codebook_version_id, run_type)

    # Load segments
    if segments is None:
        if run_type == "calibration":
            segments = fetchall(
                conn,
                "SELECT * FROM segment WHERE project_id = ? AND is_calibration = 1 ORDER BY id",
                (project_id,),
            )
        else:
            segments = fetchall(
                conn,
                "SELECT * FROM segment WHERE project_id = ? ORDER BY document_id, segment_index",
                (project_id,),
            )

    # Load codes
    codes = fetchall(
        conn,
        "SELECT * FROM code WHERE codebook_version_id = ? AND is_active = 1 ORDER BY sort_order",
        (codebook_version_id,),
    )
    if not codes:
        raise ValueError(f"No active codes in codebook version {codebook_version_id}.")

    # Skip already-coded segments if resuming
    if resume:
        already_coded = {
            row["segment_id"]
            for row in fetchall(
                conn,
                "SELECT DISTINCT segment_id FROM assignment WHERE coding_run_id = ?",
                (run_id,),
            )
        }
        segments = [s for s in segments if s["id"] not in already_coded]
        console.print(f"  {len(already_coded)} segments already coded, "
                      f"{len(segments)} remaining.")

    if not segments:
        console.print("[green]All segments already coded.[/]")
        conn.execute(
            "UPDATE coding_run SET status='complete', completed_at=datetime('now') WHERE id=?",
            (run_id,),
        )
        conn.commit()
        return run_id

    # Load document names for display
    doc_map = {
        row["id"]: row["filename"]
        for row in fetchall(conn, "SELECT id, filename FROM document WHERE project_id = ?",
                            (project_id,))
    }

    console.print(f"\n[bold cyan]Coding {len(segments)} segments with {agent.info}[/]")

    if batch:
        # Batch mode: group segments and code multiple per LLM call
        batches = create_batches(
            segments=segments,
            codes=codes,
            project=project,
            model_name=getattr(agent, "model_name", ""),
            doc_map=doc_map,
            prompt_key=prompt_key,
        )
        n_batches = len(batches)
        batch_sizes = [len(b) for b in batches]
        console.print(
            f"  [dim]Batch mode: {len(segments)} segments → {n_batches} batch(es) "
            f"(sizes: {', '.join(str(s) for s in batch_sizes[:10])}"
            f"{'…' if n_batches > 10 else ''})[/]"
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"[{agent.role}]", total=len(segments))

            for b in batches:
                code_batch(
                    agent=agent,
                    batch=b,
                    codes=codes,
                    project=project,
                    coding_run_id=run_id,
                    conn=conn,
                    doc_map=doc_map,
                    total_segments=len(segments),
                    prompt_key=prompt_key,
                )
                progress.advance(task, advance=len(b))
    else:
        # Standard mode: one LLM call per segment
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"[{agent.role}]", total=len(segments))

            for seg in segments:
                doc_name = doc_map.get(seg["document_id"], "unknown")
                code_segment(
                    agent=agent,
                    segment=seg,
                    codes=codes,
                    project=project,
                    coding_run_id=run_id,
                    conn=conn,
                    document_name=doc_name,
                    total_segments=len(segments),
                    prompt_key=prompt_key,
                )
                progress.advance(task)

    conn.execute(
        "UPDATE coding_run SET status='complete', completed_at=datetime('now') WHERE id=?",
        (run_id,),
    )
    conn.commit()
    console.print(f"[green]✓ Coding complete. Run id={run_id}[/]")
    return run_id


def _create_run(conn, project_id, agent, codebook_version_id, run_type):
    run_id = insert(conn, "coding_run", {
        "project_id": project_id,
        "codebook_version_id": codebook_version_id,
        "agent_id": agent.agent_id,
        "run_type": run_type,
        "status": "running",
        "started_at": None,  # DB default
    })
    conn.commit()
    return run_id
