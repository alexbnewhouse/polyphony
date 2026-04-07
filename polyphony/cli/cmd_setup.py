"""
polyphony CLI — setup command.

Interactive hardware detection and LLM onboarding wizard for new users.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


# Map DB agent_type back to CLI provider names
_AGENT_TYPE_TO_PROVIDER = {"llm": "ollama", "openai": "openai", "anthropic": "anthropic"}


@click.command()
@click.option(
    "--json-output",
    is_flag=True,
    default=False,
    help="Output hardware profile and recommendations as JSON (for scripting).",
)
@click.pass_context
def setup(ctx, json_output: bool) -> None:
    """Detect your hardware and get LLM setup recommendations.

    Scans your system for RAM, GPU, and Ollama status, then recommends
    the best model configuration for qualitative coding.

    \b
    Examples:
      polyphony setup                 # Interactive setup wizard
      polyphony setup --json-output   # Machine-readable output
    """
    from polyphony.onboarding import run_onboarding

    with console.status("[bold blue]Detecting hardware…"):
        result = run_onboarding()

    hw = result.hardware

    if json_output:
        import json
        output = {
            "hardware": {
                "os": hw.os_name,
                "arch": hw.arch,
                "cpu_cores": hw.cpu_cores,
                "ram_gb": round(hw.ram_gb, 1),
                "gpus": [{"name": g.name, "vram_mb": g.vram_mb} for g in hw.gpus],
                "apple_silicon": hw.apple_silicon,
                "ollama_installed": hw.ollama_installed,
                "ollama_running": hw.ollama_running,
                "ollama_models": hw.ollama_models,
            },
            "tier": result.tier,
            "recommendations": [
                {
                    "provider": r.provider,
                    "model": r.model_name,
                    "label": r.label,
                    "speed": r.estimated_speed,
                    "cost": r.estimated_cost,
                    "vision": r.supports_vision,
                }
                for r in result.recommendations
            ],
            "whisper_recommendations": [
                {
                    "model_size": w.model_size,
                    "label": w.label,
                    "speed": w.estimated_speed,
                    "vram_gb": w.estimated_vram_gb,
                    "local": w.local,
                    "compute_type": w.compute_type,
                }
                for w in result.whisper_recommendations
            ],
            "audio": {
                "faster_whisper_installed": result.faster_whisper_installed,
                "pyannote_installed": result.pyannote_installed,
            },
            "setup_steps": result.setup_steps,
            "warnings": result.warnings,
        }
        console.print_json(json.dumps(output))
        return

    # ── Hardware summary ──────────────────────────────────────────────
    console.print()
    hw_table = Table(title="🖥️  Hardware Profile", show_header=False, border_style="blue")
    hw_table.add_column("Property", style="bold")
    hw_table.add_column("Value")

    hw_table.add_row("OS", f"{hw.os_name} ({hw.arch})")
    hw_table.add_row("CPU Cores", str(hw.cpu_cores))
    hw_table.add_row("RAM", f"{hw.ram_gb:.1f} GB")

    if hw.gpus:
        for i, gpu in enumerate(hw.gpus):
            hw_table.add_row(f"GPU {i}" if len(hw.gpus) > 1 else "GPU",
                             f"{gpu.name} ({gpu.vram_gb:.1f} GB VRAM)")
    elif hw.apple_silicon:
        hw_table.add_row("GPU", "Apple Silicon (unified memory)")
    else:
        hw_table.add_row("GPU", "[dim]None detected[/]")

    ollama_status = "[green]Installed & running[/]" if hw.ollama_running else \
                    "[yellow]Installed, not running[/]" if hw.ollama_installed else \
                    "[red]Not installed[/]"
    hw_table.add_row("Ollama", ollama_status)

    if hw.ollama_models:
        hw_table.add_row("Installed models", ", ".join(hw.ollama_models))

    console.print(hw_table)

    # ── Tier assessment ──────────────────────────────────────────────
    tier_labels = {
        "local_high": (
            "[bold green]High[/] — Can run large "
            "local models (8B+ params)"
        ),
        "local_mid": (
            "[bold yellow]Medium[/] — Can run mid-size "
            "local models (3B params)"
        ),
        "local_low": (
            "[bold yellow]Low[/] — Can run small models, "
            "but slowly (CPU-only)"
        ),
        "cloud_only": (
            "[bold red]Cloud recommended[/] — Local "
            "inference is not practical"
        ),
    }
    console.print()
    console.print(Panel(
        f"Local inference capability: {tier_labels.get(result.tier, result.tier)}",
        title="[bold]Assessment[/]",
        border_style="blue",
    ))

    # ── Warnings ─────────────────────────────────────────────────────
    for w in result.warnings:
        console.print(f"  [yellow]⚠ {w}[/]")

    # ── Recommendations ──────────────────────────────────────────────
    console.print()
    rec_table = Table(title="📋 Recommended Models", border_style="green")
    rec_table.add_column("Provider", style="bold")
    rec_table.add_column("Model")
    rec_table.add_column("Description")
    rec_table.add_column("Speed")
    rec_table.add_column("Cost")

    for r in result.recommendations:
        cost_map = {
            "free": "[green]Free[/]", "$": "[yellow]$[/]",
            "$$": "[yellow]$$[/]", "$$$": "[red]$$$[/]",
        }
        speed_map = {
            "fast": "[green]Fast[/]",
            "moderate": "[yellow]Moderate[/]",
            "slow": "[red]Slow[/]",
        }
        rec_table.add_row(
            r.provider.title(),
            r.model_name,
            r.label,
            speed_map.get(r.estimated_speed, r.estimated_speed),
            cost_map.get(r.estimated_cost, r.estimated_cost),
        )

    console.print(rec_table)

    # ── Multimodal capabilities ──────────────────────────────────────
    vision_recs = [r for r in result.recommendations if r.supports_vision]
    if vision_recs:
        console.print()
        vis_table = Table(title="🖼️  Multimodal (Vision) Models", border_style="magenta")
        vis_table.add_column("Provider", style="bold")
        vis_table.add_column("Model")
        vis_table.add_column("Notes")
        for r in vision_recs:
            vis_table.add_row(r.provider.title(), r.model_name, r.label)
        console.print(vis_table)
        console.print(
            "  [dim]Vision models can code images alongside text. "
            "Install Pillow: pip install 'polyphony\\[images]'[/]"
        )
        if result.tier in ("local_high", "local_mid"):
            console.print(
                "  [dim]Local vision via Ollama: "
                "ollama pull llava:7b  (7B, ~4 GB VRAM) or  "
                "ollama pull llava:13b (13B, ~8 GB VRAM)[/]"
            )

    # ── Audio transcription ──────────────────────────────────────────
    if result.whisper_recommendations:
        console.print()
        wh_table = Table(title="🎙️  Audio Transcription (Whisper)", border_style="cyan")
        wh_table.add_column("Model", style="bold")
        wh_table.add_column("Type")
        wh_table.add_column("Compute")
        wh_table.add_column("Description")
        wh_table.add_column("Speed")
        wh_table.add_column("VRAM / RAM")

        for wr in result.whisper_recommendations:
            speed_map = {
                "fast": "[green]Fast[/]",
                "moderate": "[yellow]Moderate[/]",
                "slow": "[red]Slow[/]",
            }
            ct_map = {
                "float16": "[green]float16 (GPU)[/]",
                "int8": "int8 (CPU)",
                "auto": "auto",
            }
            model_type = "Cloud" if not wr.local else "Local"
            vram_label = "—" if wr.estimated_vram_gb == 0.0 else f"~{wr.estimated_vram_gb:.1f} GB"
            wh_table.add_row(
                wr.model_size,
                model_type,
                ct_map.get(wr.compute_type, wr.compute_type) if wr.local else "—",
                wr.label,
                speed_map.get(wr.estimated_speed, wr.estimated_speed),
                vram_label,
            )

        console.print(wh_table)

        # Whisper install status
        status_parts = []
        if result.faster_whisper_installed:
            status_parts.append("[green]faster-whisper ✓[/]")
        else:
            status_parts.append("[yellow]faster-whisper ✗[/] — pip install 'polyphony\\[audio]'")
        if result.pyannote_installed:
            status_parts.append("[green]pyannote.audio ✓[/]")
        else:
            status_parts.append("[yellow]pyannote.audio ✗[/] — pip install 'polyphony\\[diarize]'")
        console.print(f"  Packages: {' | '.join(status_parts)}")
        console.print(
            "  [dim]Whisper model sizes: tiny (39M) → base (74M) → small (244M) "
            "→ medium (769M) → large-v3 (1.5B params).  "
            "Larger ≈ better accuracy but slower & more VRAM.[/]"
        )
        if hw.has_gpu:
            console.print(
                "  [bold green]GPU detected![/] Transcription will automatically use "
                "[bold]float16[/] on your GPU for significantly faster processing."
            )
            console.print(
                "  [dim]For GPU acceleration, ensure CUDA libraries are available: "
                "pip install 'polyphony\\[audio-gpu]'[/]"
            )
        else:
            console.print(
                "  [dim]No GPU detected — transcription will use int8 on CPU. "
                "Add a CUDA-capable GPU for 10–30× speed improvement.[/]"
            )

    # ── Setup steps ──────────────────────────────────────────────────
    if result.setup_steps:
        console.print()
        console.print("[bold]Next steps:[/]")
        for i, step in enumerate(result.setup_steps, 1):
            console.print(f"  {i}. {escape(step)}")

    # ── Quick-start suggestion ───────────────────────────────────────
    top = result.recommendations[0] if result.recommendations else None
    if top:
        console.print()
        if top.provider == "ollama":
            console.print(Panel(
                f"[bold]Quick start:[/]\n\n"
                f"  polyphony project new --name \"My Study\" "
                f"--model-a {top.model_name} --model-b {top.model_name}\n\n"
                f"This uses [bold]{top.model_name}[/] for both coders with different random seeds.",
                title="[bold green]Ready to go![/]",
                border_style="green",
            ))
        else:
            console.print(Panel(
                f"[bold]Quick start:[/]\n\n"
                f"  polyphony project new --name \"My Study\" "
                f"--provider-a {top.provider} --model-a {top.model_name} "
                f"--provider-b {top.provider} --model-b {top.model_name}\n\n"
                f"For best independence, use different providers for Coder A and Coder B.",
                title="[bold green]Ready to go![/]",
                border_style="green",
            ))

    # ── Current project coder configuration ──────────────────────────
    _show_current_coders(ctx, result)


def _show_current_coders(ctx: click.Context, result) -> None:
    """Display current coder models for the active project, with option to change."""
    from polyphony.db import connect, fetchall, fetchone, find_project_db, update

    # Resolve active project
    db_path = ctx.obj.get("db_path") if ctx.obj else None
    if not db_path:
        try:
            db_path = find_project_db()
        except FileNotFoundError:
            db_path = None

    if not db_path or not db_path.exists():
        console.print()
        console.print("[dim]No active project. Create one with:[/]")
        console.print("  [bold]polyphony project new --name \"My Study\"[/]")
        return

    conn = connect(db_path)
    project = fetchone(conn, "SELECT * FROM project WHERE id = 1")
    coders = fetchall(
        conn,
        "SELECT * FROM agent WHERE project_id = 1 AND role IN ('coder_a', 'coder_b') ORDER BY role",
    )
    if not project or len(coders) < 2:
        conn.close()
        return

    coder_a = next((c for c in coders if c["role"] == "coder_a"), None)
    coder_b = next((c for c in coders if c["role"] == "coder_b"), None)
    if not coder_a or not coder_b:
        conn.close()
        return

    prov_a = _AGENT_TYPE_TO_PROVIDER.get(coder_a["agent_type"], coder_a["agent_type"]) or "ollama"
    prov_b = _AGENT_TYPE_TO_PROVIDER.get(coder_b["agent_type"], coder_b["agent_type"]) or "ollama"

    console.print()
    coder_table = Table(
        title=f"🤖 Current Coder Configuration — [cyan]{project['name']}[/]",
        border_style="cyan",
    )
    coder_table.add_column("", style="bold")
    coder_table.add_column("Coder A", style="green")
    coder_table.add_column("Coder B", style="yellow")

    coder_table.add_row("Provider", prov_a.title(), prov_b.title())
    coder_table.add_row("Model", coder_a["model_name"], coder_b["model_name"])
    coder_table.add_row("Seed", str(coder_a["seed"]), str(coder_b["seed"]))
    coder_table.add_row("Temperature", f"{coder_a['temperature']:.2f}", f"{coder_b['temperature']:.2f}")

    console.print(coder_table)

    # Offer to change
    if not Confirm.ask("\n  Would you like to change the coder models?", default=False):
        conn.close()
        return

    _PROVIDER_TO_AGENT_TYPE = {"ollama": "llm", "openai": "openai", "anthropic": "anthropic"}
    valid_providers = list(_PROVIDER_TO_AGENT_TYPE.keys())

    # Build model suggestions from recommendations
    model_hints = {}
    for r in result.recommendations:
        model_hints.setdefault(r.provider, []).append(r.model_name)

    def _prompt_coder(label: str, current_provider: str, current_model: str) -> tuple[str, str]:
        console.print(f"\n  [bold]{label}[/]  (current: [cyan]{current_model}[/] via [cyan]{current_provider}[/])")

        # Provider
        provider_choices = ", ".join(valid_providers)
        new_provider = Prompt.ask(
            f"    Provider ({provider_choices})",
            default=current_provider,
        ).strip().lower()
        if new_provider not in valid_providers:
            console.print(f"    [yellow]Invalid provider '{new_provider}', keeping '{current_provider}'[/]")
            new_provider = current_provider

        # Model suggestions for selected provider
        hints = model_hints.get(new_provider, [])
        if hints:
            console.print(f"    [dim]Suggested models: {', '.join(hints)}[/]")

        new_model = Prompt.ask(
            "    Model name",
            default=current_model if new_provider == current_provider else (hints[0] if hints else current_model),
        ).strip()

        return new_provider, new_model

    new_prov_a, new_model_a = _prompt_coder("Coder A", prov_a, coder_a["model_name"])
    new_prov_b, new_model_b = _prompt_coder("Coder B", prov_b, coder_b["model_name"])

    # Check if anything changed
    changed_a = new_prov_a != prov_a or new_model_a != coder_a["model_name"]
    changed_b = new_prov_b != prov_b or new_model_b != coder_b["model_name"]

    if not changed_a and not changed_b:
        console.print("\n  [dim]No changes made.[/]")
        conn.close()
        return

    # Apply updates
    if changed_a:
        update(conn, "agent", {
            "agent_type": _PROVIDER_TO_AGENT_TYPE[new_prov_a],
            "model_name": new_model_a,
        }, "id = ?", (coder_a["id"],))

    if changed_b:
        update(conn, "agent", {
            "agent_type": _PROVIDER_TO_AGENT_TYPE[new_prov_b],
            "model_name": new_model_b,
        }, "id = ?", (coder_b["id"],))

    conn.commit()
    conn.close()

    # Show summary
    console.print()
    summary_parts = []
    if changed_a:
        summary_parts.append(f"  Coder A: [green]{new_model_a}[/] via [green]{new_prov_a}[/]")
    if changed_b:
        summary_parts.append(f"  Coder B: [green]{new_model_b}[/] via [green]{new_prov_b}[/]")
    console.print(Panel(
        "[bold]Updated coder configuration:[/]\n\n" + "\n".join(summary_parts),
        title="[bold green]✓ Models Updated[/]",
        border_style="green",
    ))
