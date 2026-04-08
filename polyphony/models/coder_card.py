"""
polyphony.models.coder_card
========================
Coder Card model — a standardized document describing each coder
(human or LLM) for inclusion in replication packages.

Coder Cards make the epistemological asymmetry between human and
LLM coders explicit and legible to reviewers.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class CoderCard(BaseModel):
    """A standardized description of a coder (human or LLM) for replication."""

    # ── Identity ──────────────────────────────────────────────────────────
    agent_id: Optional[int] = None
    role: str                          # "supervisor", "coder_a", "coder_b"
    agent_type: str                    # "human", "llm", "openai", "anthropic"

    # ── LLM-specific fields ──────────────────────────────────────────────
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    provider: Optional[str] = None     # "ollama", "openai", "anthropic"
    temperature: Optional[float] = None
    seed: Optional[int] = None
    system_prompt_used: Optional[str] = None
    persona_description: Optional[str] = None
    training_cutoff: Optional[str] = None
    known_limitations: Optional[str] = None

    # ── Human-specific fields ────────────────────────────────────────────
    positionality_statement: Optional[str] = None
    disciplinary_background: Optional[str] = None
    relationship_to_data: Optional[str] = None
    project_role_description: Optional[str] = None

    # ── Shared fields ────────────────────────────────────────────────────
    pipeline_roles: List[str] = Field(default_factory=list)
    # e.g. ["induction", "calibration", "coding"]

    generated_at: Optional[datetime] = None
