"""polyphony data import commands."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ..db import connect, fetchall, fetchone
from ..generators import generate_llm_data, generate_template_data, get_domains
from ..io.fetchers import fetch_images_from_csv
from ..io.importers import import_documents
from ..io.transcribers import transcribe_audio_file
from ..utils import build_agent_objects, get_active_codebook

console = Console()


def _safe_transcript_basename(audio_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", audio_path.stem)
    stem = stem.strip("._")
    return stem or "transcript"


def _next_transcript_path(transcripts_dir: Path, audio_path: Path) -> Path:
    base = _safe_transcript_basename(audio_path)
    candidate = transcripts_dir / f"{base}.txt"
    if not candidate.exists():
        return candidate

    i = 2
    while True:
        candidate = transcripts_dir / f"{base}_{i}.txt"
        if not candidate.exists():
            return candidate
        i += 1


@click.group()
def data():
    """Import and inspect corpus documents.

    Documents are split into segments — the units that agents code.
    Each segment is a chunk of text (e.g. a paragraph, a few sentences,
    or a fixed word window) that gets assigned one or more codes.
    """


@data.command("import")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--segment-by",
    default="paragraph",
    show_default=True,
    help="Segmentation strategy: paragraph | sentence | fixed:<n_words> | manual",
)
@click.option(
    "--content-col",
    default="content",
    show_default=True,
    help="Column name for text content (CSV only)",
)
@click.option(
    "--min-length",
    default=20,
    show_default=True,
    help="Minimum segment length in characters",
)
@click.pass_context
def import_cmd(ctx, files, segment_by, content_col, min_length):
    """
    Import documents into the active project.

    Supported formats: .txt, .md, .docx, .csv, .json,
    .png, .jpg, .jpeg, .gif, .webp, .bmp, .tiff

    Text documents are split into segments using the --segment-by strategy:
      paragraph  — split on blank lines (good for interview transcripts)
      sentence   — groups of ~3 sentences (good for survey responses)
      fixed:<n>  — fixed word-count windows, e.g. fixed:150
      manual     — one segment per document (good for pre-segmented data)

    Image files are always imported as one segment per image (no text
    segmentation). Use a vision-capable model (e.g. llava, llama3.2-vision)
    to code image segments.

    \b
    Examples:
        polyphony data import interviews/*.txt --segment-by paragraph
        polyphony data import survey.csv --segment-by sentence --content-col response
        polyphony data import transcript.docx --segment-by fixed:150
        polyphony data import photos/*.jpg
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project. Run `polyphony project new` first.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

    # Derive project directory for image storage
    project_dir = db_path.parent if db_path else None

    result = import_documents(
        conn=conn,
        project_id=project["id"],
        paths=[Path(f) for f in files],
        segment_strategy=segment_by,
        content_col=content_col,
        min_segment_length=min_length,
        project_dir=project_dir,
    )
    conn.close()

    console.print(
        f"\n[green]Import complete:[/] "
        f"{result['documents_imported']} document(s), "
        f"{result['segments_created']} segment(s)."
    )
    if result["skipped"]:
        console.print(f"[yellow]Skipped {len(result['skipped'])} file(s).[/]")


@data.command("transcribe")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--provider",
    type=click.Choice(["local_whisper", "openai"]),
    default="local_whisper",
    show_default=True,
    help="Transcription backend.",
)
@click.option(
    "--model",
    default=None,
    help="Transcription model name (provider-specific).",
)
@click.option(
    "--language",
    default=None,
    help="Optional language hint, e.g. en, es, pt-BR.",
)
@click.option(
    "--prompt",
    default=None,
    help="Optional context prompt for transcription.",
)
@click.option(
    "--segment-by",
    default="paragraph",
    show_default=True,
    help="Segmentation strategy for imported transcripts.",
)
@click.option(
    "--min-length",
    default=20,
    show_default=True,
    help="Minimum transcript segment length in characters.",
)
@click.option(
    "--max-size-mb",
    default=500,
    show_default=True,
    type=click.IntRange(1, 2048),
    help="Maximum allowed source audio file size.",
)
@click.option(
    "--auto-induce",
    is_flag=True,
    help="Run codebook induction after transcript import.",
)
@click.option(
    "--auto-code",
    is_flag=True,
    help="Run independent coding (A+B) after import/induction.",
)
@click.option(
    "--induction-sample-size",
    default=20,
    show_default=True,
    type=click.IntRange(1, 500),
    help="Segments to sample when --auto-induce is used.",
)
@click.option(
    "--induction-seed",
    default=42,
    show_default=True,
    type=int,
    help="Random seed for --auto-induce sampling.",
)
@click.option(
    "--skip-agent-b-induction",
    is_flag=True,
    help="Use only Coder A for --auto-induce.",
)
@click.option(
    "--auto-approve-codes",
    is_flag=True,
    help="Accept all merged induction candidates without interactive review.",
)
@click.pass_context
def transcribe_cmd(
    ctx,
    files,
    provider,
    model,
    language,
    prompt,
    segment_by,
    min_length,
    max_size_mb,
    auto_induce,
    auto_code,
    induction_sample_size,
    induction_seed,
    skip_agent_b_induction,
    auto_approve_codes,
):
    """
    Transcribe audio files and import transcripts into the active project.

    Audio is copied into the project `audio/` directory for provenance. Each
    transcript is saved in `transcripts/` and imported with metadata linking
    it back to the source audio and transcription settings.

    Examples:
        polyphony data transcribe interviews/*.wav
        polyphony data transcribe interview.mp3 --provider openai --model whisper-1
        polyphony data transcribe focus_group.m4a --auto-induce --auto-code
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project. Run `polyphony project new` first.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    project_dir = db_path.parent if db_path else None
    if project is None or project_dir is None:
        conn.close()
        console.print("[red]Failed to resolve active project.[/]")
        sys.exit(1)

    audio_dir = project_dir / "audio"
    transcripts_dir = project_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    max_audio_bytes = max_size_mb * 1024 * 1024

    imported_docs = 0
    imported_segments = 0
    skipped_files: list[str] = []
    failed_files: list[tuple[Path, str]] = []

    console.print(f"[bold]Transcribing {len(files)} audio file(s) with {provider}...[/]")

    for source_path in files:
        try:
            transcribed = transcribe_audio_file(
                source_path,
                project_audio_dir=audio_dir,
                provider=provider,
                model=model,
                language=language,
                prompt=prompt,
                max_audio_bytes=max_audio_bytes,
            )
        except Exception as exc:
            failed_files.append((source_path, str(exc)))
            console.print(f"  [red]x[/] {source_path.name}: {exc}")
            continue

        transcript_path = _next_transcript_path(transcripts_dir, source_path)
        transcript_path.write_text(transcribed["text"], encoding="utf-8")

        result = import_documents(
            conn=conn,
            project_id=project["id"],
            paths=[transcript_path],
            segment_strategy=segment_by,
            min_segment_length=min_length,
            metadata_override=transcribed["metadata"],
            project_dir=project_dir,
        )
        imported_docs += result["documents_imported"]
        imported_segments += result["segments_created"]
        skipped_files.extend(result["skipped"])

        if result["documents_imported"] == 0:
            console.print(
                f"  [yellow]-[/] {source_path.name}: transcript imported as duplicate or below thresholds"
            )
        else:
            console.print(
                f"  [green]✓[/] {source_path.name} -> {transcript_path.name} "
                f"({result['segments_created']} segment(s))"
            )

    if imported_docs == 0:
        conn.close()
        console.print("[red]No transcripts were imported.[/]")
        for source_path, error in failed_files:
            console.print(f"  [red]-[/] {source_path}: {error}")
        sys.exit(1)

    console.print(
        f"\n[green]Transcription import complete:[/] "
        f"{imported_docs} document(s), {imported_segments} segment(s)."
    )
    if skipped_files:
        console.print(f"[yellow]Skipped {len(skipped_files)} import(s).[/]")
    if failed_files:
        console.print(f"[yellow]{len(failed_files)} audio file(s) failed transcription.[/]")

    from ..pipeline.coding import run_coding_session
    from ..pipeline.induction import run_induction

    agent_a = agent_b = supervisor = None
    if auto_induce or auto_code:
        agent_a, agent_b, supervisor = build_agent_objects(conn, project["id"])

    active_cb = get_active_codebook(conn, project["id"])
    if auto_induce:
        if agent_a is None:
            conn.close()
            console.print("[red]Coder A is not configured. Cannot run --auto-induce.[/]")
            sys.exit(1)

        if not skip_agent_b_induction and agent_b is None:
            conn.close()
            console.print("[red]Coder B is not configured. Use --skip-agent-b-induction or reconfigure agents.[/]")
            sys.exit(1)

        for label, agent_obj in [("A", agent_a), ("B", agent_b)]:
            if agent_obj is None:
                continue
            if label == "B" and skip_agent_b_induction:
                continue
            if hasattr(agent_obj, "is_available") and not agent_obj.is_available():
                conn.close()
                console.print(
                    f"[red]Model '{agent_obj.model_name}' (Coder {label}) is unavailable.[/]"
                )
                sys.exit(1)

        cb_id = run_induction(
            conn=conn,
            project=project,
            agent_a=agent_a,
            agent_b=agent_b,
            sample_size=induction_sample_size,
            sample_seed=induction_seed,
            skip_agent_b=skip_agent_b_induction,
            human_leads=False,
            supervisor_agent=supervisor,
            auto_accept_all=auto_approve_codes,
        )
        active_cb = fetchone(conn, "SELECT * FROM codebook_version WHERE id = ?", (cb_id,))

    if auto_code:
        if agent_a is None or agent_b is None:
            conn.close()
            console.print("[red]Both Coder A and Coder B are required for --auto-code.[/]")
            sys.exit(1)

        if active_cb is None:
            conn.close()
            console.print(
                "[red]No active codebook. Run --auto-induce first or use `polyphony codebook induce`.[/]"
            )
            sys.exit(1)

        for label, agent_obj in [("A", agent_a), ("B", agent_b)]:
            if hasattr(agent_obj, "is_available") and not agent_obj.is_available():
                conn.close()
                console.print(
                    f"[red]Model '{agent_obj.model_name}' (Coder {label}) is unavailable.[/]"
                )
                sys.exit(1)

        console.print("\n[bold]Running independent coding for Coder A and Coder B...[/]")
        run_coding_session(
            conn=conn,
            project=project,
            agent=agent_a,
            codebook_version_id=active_cb["id"],
            run_type="independent",
            resume=False,
            prompt_key="open_coding",
        )
        run_coding_session(
            conn=conn,
            project=project,
            agent=agent_b,
            codebook_version_id=active_cb["id"],
            run_type="independent",
            resume=False,
            prompt_key="open_coding",
        )
        conn.execute(
            "UPDATE project SET status='irr', updated_at=datetime('now') WHERE id=?",
            (project["id"],),
        )
        conn.commit()
        console.print("[green]Auto-coding complete.[/]")

    conn.close()

    console.print("\n[bold]Suggested next steps[/]")
    if not auto_induce:
        console.print("  1. polyphony codebook induce")
    if not auto_code:
        console.print("  2. polyphony code run")
    console.print("  3. polyphony irr compute")


@data.command("list")
@click.pass_context
def list_docs(ctx):
    """List imported documents."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    docs = fetchall(
        conn,
        "SELECT id, filename, word_count, status, imported_at, media_type FROM document WHERE project_id = ?",
        (project["id"],),
    )
    conn.close()

    if not docs:
        console.print("[dim]No documents imported yet.[/]")
        return

    table = Table(title="Documents", show_header=True)
    table.add_column("ID", width=5)
    table.add_column("Filename")
    table.add_column("Type", width=6)
    table.add_column("Words", justify="right")
    table.add_column("Status")
    table.add_column("Imported")
    for d in docs:
        media = d.get("media_type", "text")
        table.add_row(
            str(d["id"]), d["filename"], media,
            str(d["word_count"]) if media == "text" else "-",
            d["status"], (d["imported_at"] or "")[:10]
        )
    console.print(table)


@data.command("show")
@click.argument("doc_id", type=int)
@click.option("--segments", is_flag=True, help="Show segments instead of full text")
@click.pass_context
def show_doc(ctx, doc_id, segments):
    """Show a document's text or its segments."""
    db_path = ctx.obj.get("db_path")
    if not db_path:
        sys.exit(1)

    conn = connect(db_path)
    doc = fetchone(conn, "SELECT * FROM document WHERE id = ?", (doc_id,))
    if not doc:
        console.print(f"[red]Document {doc_id} not found.[/]")
        conn.close()
        return

    if segments:
        segs = fetchall(
            conn,
            "SELECT segment_index, text, is_calibration, media_type, image_path FROM segment WHERE document_id = ? ORDER BY segment_index",
            (doc_id,),
        )
        from rich.panel import Panel
        for s in segs:
            cal_tag = " [yellow][CAL][/]" if s["is_calibration"] else ""
            if s.get("media_type") == "image":
                console.print(Panel(
                    f"[Image: {s.get('image_path', 'unknown')}]",
                    title=f"[cyan]Segment {s['segment_index']}{cal_tag} (image)[/]",
                    border_style="dim",
                ))
            else:
                console.print(Panel(
                    s["text"],
                    title=f"[cyan]Segment {s['segment_index']}{cal_tag}[/]",
                    border_style="dim",
                ))
    else:
        if doc.get("media_type") == "image":
            console.print(f"[Image document: {doc.get('image_path', doc['filename'])}]")
        else:
            console.print(doc["content"])

    conn.close()


@data.command("fetch-images")
@click.argument("csv_path", type=click.Path(exists=True))
@click.option(
    "--url-column",
    default="url",
    show_default=True,
    help="CSV column containing image URLs",
)
@click.option(
    "--metadata-columns",
    default=None,
    help="Comma-separated list of CSV columns to keep as metadata (default: all non-URL columns)",
)
@click.option(
    "--timeout",
    default=30,
    show_default=True,
    help="Download timeout in seconds per image",
)
@click.option(
    "--max-concurrent",
    default=5,
    show_default=True,
    help="Maximum number of concurrent downloads",
)
@click.pass_context
def fetch_images(ctx, csv_path, url_column, metadata_columns, timeout, max_concurrent):
    """
    Fetch images from URLs in a CSV file and import them.

    Downloads images listed in CSV_PATH, saves them locally, and imports
    them into the active project. Each image becomes one document with
    one segment (manual segmentation).

    \b
    Examples:
        polyphony data fetch-images urls.csv
        polyphony data fetch-images urls.csv --url-column image_url
        polyphony data fetch-images urls.csv --metadata-columns "label,source" --max-concurrent 10
    """
    db_path = ctx.obj.get("db_path")
    if not db_path:
        console.print("[red]No active project. Run `polyphony project new` first.[/]")
        sys.exit(1)

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
    project_dir = db_path.parent if db_path else None

    if project_dir is None:
        console.print("[red]Cannot determine project directory for image storage.[/]")
        conn.close()
        sys.exit(1)

    images_dir = Path(project_dir) / "images"

    # Parse metadata columns if provided
    meta_cols = None
    if metadata_columns:
        meta_cols = [c.strip() for c in metadata_columns.split(",")]

    # Fetch images from CSV
    console.print(f"[bold]Fetching images from[/] {csv_path}")
    fetch_result = fetch_images_from_csv(
        csv_path=Path(csv_path),
        images_dir=images_dir,
        url_column=url_column,
        metadata_columns=meta_cols,
        timeout=timeout,
        max_concurrent=max_concurrent,
    )

    downloaded = fetch_result["downloaded"]
    skipped = fetch_result["skipped"]
    failed = fetch_result["failed"]

    console.print(
        f"\n[bold]Fetch complete:[/] "
        f"[green]{len(downloaded)} downloaded[/], "
        f"[yellow]{len(skipped)} skipped[/], "
        f"[red]{len(failed)} failed[/]"
    )

    if failed:
        for entry in failed:
            console.print(f"  [red]x[/] {entry['url']}: {entry.get('error', 'unknown error')}")

    # Import downloaded images into the project
    if downloaded:
        image_paths = [entry["path"] for entry in downloaded]
        console.print(f"\n[bold]Importing {len(image_paths)} image(s) into project...[/]")

        result = import_documents(
            conn=conn,
            project_id=project["id"],
            paths=image_paths,
            segment_strategy="manual",
            project_dir=project_dir,
        )

        console.print(
            f"\n[green]Import complete:[/] "
            f"{result['documents_imported']} document(s), "
            f"{result['segments_created']} segment(s)."
        )
        if result["skipped"]:
            console.print(f"[yellow]Skipped {len(result['skipped'])} file(s) during import.[/]")
    else:
        console.print("\n[dim]No new images to import.[/]")

    conn.close()


@data.command("generate")
@click.option("--domain", default=None, help="Pre-built domain name (e.g. housing, healthcare, education)")
@click.option("--topic", default=None, help="Custom topic for LLM-based generation (requires Ollama)")
@click.option("--model", default="llama3.2", show_default=True, help="Ollama model for custom topic generation")
@click.option("--segments", default=20, show_default=True, help="Number of segments to generate")
@click.option("--list-domains", is_flag=True, help="Show available pre-built domains and exit")
@click.option("--output", default=None, type=click.Path(), help="Export to CSV file instead of importing")
@click.option("--seed", default=None, type=int, help="Random seed for reproducibility")
@click.pass_context
def generate(ctx, domain, topic, model, segments, list_domains, output, seed):
    """Generate synthetic qualitative data for training and practice.

    Uses pre-built domains with realistic interview excerpt templates, or
    generates custom data via a local Ollama LLM.

    \b
    Examples:
        polyphony data generate --list-domains
        polyphony data generate --domain housing --segments 30
        polyphony data generate --domain healthcare --output training.csv
        polyphony data generate --topic "climate anxiety" --segments 25
    """
    # --- List domains mode ---
    if list_domains:
        domains = get_domains()
        table = Table(title="Available Domains", show_header=True)
        table.add_column("Key", style="cyan")
        table.add_column("Description")
        for key, desc in domains.items():
            table.add_row(key, desc)
        console.print(table)
        return

    # --- Validate options ---
    if domain and topic:
        console.print("[red]Cannot specify both --domain and --topic. Pick one.[/]")
        sys.exit(1)
    if not domain and not topic:
        console.print("[red]Specify --domain or --topic. Use --list-domains to see pre-built options.[/]")
        sys.exit(1)

    # --- Generate data ---
    if domain:
        console.print(f"[bold]Generating {segments} segments from domain:[/] {domain}")
        try:
            result = generate_template_data(domain=domain, n_segments=segments, seed=seed)
        except ValueError as e:
            console.print(f"[red]{e}[/]")
            sys.exit(1)
    else:
        console.print(f"[bold]Generating {segments} segments via LLM on topic:[/] {topic}")
        try:
            result = generate_llm_data(
                topic=topic, n_segments=segments, model=model, seed=seed,
            )
        except RuntimeError as e:
            console.print(f"[red]{e}[/]")
            sys.exit(1)

    gen_segments = result["segments"]
    gen_codes = result["codes"]

    if not gen_segments:
        console.print("[yellow]No segments were generated. Try a different topic or check Ollama.[/]")
        return

    console.print(f"[green]Generated {len(gen_segments)} segment(s).[/]")

    # --- Output mode: CSV export ---
    if output:
        import csv

        out_path = Path(output)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["text", "participant", "domain", "generated"])
            writer.writeheader()
            for seg in gen_segments:
                writer.writerow(
                    {
                        "text": seg["text"],
                        "participant": seg["metadata"].get("participant", ""),
                        "domain": seg["metadata"].get("domain", ""),
                        "generated": seg["metadata"].get("generated", True),
                    }
                )
        console.print(f"[green]Exported to {out_path}[/]")

    # --- Output mode: import into project ---
    else:
        db_path = ctx.obj.get("db_path")
        if not db_path:
            console.print("[red]No active project. Run `polyphony project new` first.[/]")
            console.print("[dim]Tip: use --output to export to CSV without a project.[/]")
            sys.exit(1)

        import json
        import tempfile

        conn = connect(db_path)
        project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")

        # Write segments to a temp JSON file in the format the importer expects
        json_data = [{"content": seg["text"], "metadata": seg["metadata"]} for seg in gen_segments]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
        ) as tmp:
            json.dump(json_data, tmp, ensure_ascii=False, indent=2)
            temp_path = Path(tmp.name)

        try:
            import_result = import_documents(
                conn=conn,
                project_id=project["id"],
                paths=[temp_path],
                segment_strategy="manual",
            )
            console.print(
                f"[green]Import complete:[/] "
                f"{import_result['documents_imported']} document(s), "
                f"{import_result['segments_created']} segment(s)."
            )
            if import_result["skipped"]:
                console.print(f"[yellow]Skipped {len(import_result['skipped'])} file(s).[/]")
        finally:
            temp_path.unlink(missing_ok=True)
            conn.close()

    # --- Display suggested codebook ---
    if gen_codes:
        console.print("\n[bold]Suggested codebook:[/]")
        code_table = Table(show_header=True)
        code_table.add_column("Code", style="cyan")
        code_table.add_column("Description")
        code_table.add_column("Include when...")
        code_table.add_column("Exclude when...")
        for code in gen_codes:
            code_table.add_row(
                code.get("name", ""),
                code.get("description", ""),
                code.get("inclusion_criteria", ""),
                code.get("exclusion_criteria", ""),
            )
        console.print(code_table)
