"""
polyphony.agents.human
=================
Human coder agent. Presents segments and codebook via the terminal (Rich)
and collects assignments interactively.

Human responses are also logged to llm_call with model_name='human' so the
audit trail is consistent regardless of who did the coding.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .base import BaseAgent

console = Console()


class HumanAgent(BaseAgent):
    """
    Interactive terminal-based coding agent for the human supervisor.
    """

    def __init__(
        self,
        agent_id: int,
        project_id: int,
        conn: sqlite3.Connection,
        role: str = "supervisor",
        name: str = "supervisor",
    ):
        super().__init__(
            agent_id=agent_id,
            project_id=project_id,
            role=role,
            model_name="human",
            model_version="human",
            temperature=0.0,
            seed=0,
            conn=conn,
        )
        self.name = name

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        images: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Display the user_prompt and collect human input.
        Returns the typed response as both raw text and a minimal parsed dict.

        For image segments, displays the image file path for the human to review.
        """
        if images:
            for img_path in images:
                console.print(f"[bold yellow]Image:[/] {img_path}")
        console.print(Panel(user_prompt, title="[bold cyan]Coding Task[/]", expand=False))
        raw = Prompt.ask("[bold green]Your response[/]")
        return raw, {"response": raw}

    def code_segment(
        self,
        segment_text: str,
        codes: List[dict],
        document_name: str,
        segment_idx: int,
        total_segments: int,
        image_path: Optional[str] = None,
    ) -> List[dict]:
        """
        Present a segment and collect code assignments interactively.
        Returns a list of assignment dicts:
            [{"code_name": str, "confidence": float, "rationale": str, "is_primary": bool}]
        """
        console.rule(f"[bold]Segment {segment_idx}/{total_segments} — {document_name}[/]")
        if image_path:
            console.print(f"[bold yellow]Image:[/] {image_path}")
            console.print(Panel(segment_text, title="[cyan]Image Segment[/]", border_style="dim"))
        else:
            console.print(Panel(segment_text, title="[cyan]Text[/]", border_style="dim"))

        # Show codebook
        table = Table(title="Available Codes", show_header=True, header_style="bold magenta")
        table.add_column("#", width=4)
        table.add_column("Code", style="bold")
        table.add_column("Description")
        for i, code in enumerate(codes, 1):
            table.add_row(str(i), code["name"], code.get("description", ""))
        console.print(table)

        console.print(
            "\nEnter code numbers separated by commas (e.g. 1,3), "
            "or 'u' for uncoded, or 'f' to flag this segment."
        )

        assignments = []
        while True:
            raw = Prompt.ask("Codes").strip().lower()

            if raw == "u":
                break
            elif raw == "f":
                reason = Prompt.ask("Flag reason")
                assignments.append({"flag": True, "reason": reason})
                break
            else:
                # Parse comma-separated numbers
                try:
                    indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
                    selected = [codes[i - 1] for i in indices if 1 <= i <= len(codes)]
                    if not selected:
                        console.print("[red]No valid codes selected. Try again.[/]")
                        continue
                    for j, code in enumerate(selected):
                        rationale = Prompt.ask(f"  Rationale for '{code['name']}'", default="")
                        assignments.append(
                            {
                                "code_name": code["name"],
                                "confidence": 1.0,
                                "rationale": rationale,
                                "is_primary": j == 0,
                            }
                        )
                    break
                except (ValueError, IndexError):
                    console.print("[red]Invalid input. Enter numbers, 'u', or 'f'.[/]")
                    continue

        return assignments

    def propose_codes(
        self,
        segments: List[dict],
    ) -> List[dict]:
        """
        Interactive code proposal for human-led induction.
        Display sample segments and let the human propose codes inductively.
        Returns a list of candidate code dicts (same format as run_agent_induction).
        """
        console.print(
            Panel(
                f"[bold]{len(segments)} sample segments[/] are shown below.\n"
                "Read through them and propose codes that capture meaningful patterns.\n"
                "Type [green]done[/] when finished proposing codes.",
                title="[bold cyan]Human-Led Codebook Induction[/]",
            )
        )

        # Display segments
        for i, seg in enumerate(segments, 1):
            if seg.get("media_type") == "image":
                console.print(Panel(
                    f"[bold yellow]Image:[/] {seg.get('image_path', 'unknown')}",
                    title=f"[dim]Segment {i}/{len(segments)}[/]",
                    border_style="dim",
                ))
            else:
                console.print(Panel(
                    seg.get("text", ""),
                    title=f"[dim]Segment {i}/{len(segments)}[/]",
                    border_style="dim",
                ))

        # Interactive code proposal loop
        candidates: List[dict] = []
        console.print("\n[bold cyan]Propose codes based on what you observed:[/]")
        while True:
            name = Prompt.ask(
                "\nCode name (or 'done' to finish)",
                default="done",
            ).strip()
            if name.lower() == "done":
                break

            description = Prompt.ask("  Description")
            inclusion = Prompt.ask("  Inclusion criteria (optional)", default="")
            exclusion = Prompt.ask("  Exclusion criteria (optional)", default="")

            candidates.append({
                "name": name,
                "description": description,
                "inclusion_criteria": inclusion or "",
                "exclusion_criteria": exclusion or "",
                "example_quotes": [],
                "level": "open",
            })
            console.print(f"  [green]Added: {name}[/]")

        console.print(f"\n[green]{len(candidates)} codes proposed.[/]")
        return candidates
