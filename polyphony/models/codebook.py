from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class CodeLevel(str, Enum):
    open = "open"
    axial = "axial"
    selective = "selective"


class CodebookStage(str, Enum):
    draft = "draft"
    calibrated = "calibrated"
    final = "final"


class CodebookVersion(BaseModel):
    id: Optional[int] = None
    project_id: int
    version: int
    stage: CodebookStage = CodebookStage.draft
    rationale: Optional[str] = None
    created_by: Optional[int] = None  # agent_id
    created_at: Optional[datetime] = None


class Code(BaseModel):
    id: Optional[int] = None
    project_id: int
    codebook_version_id: int
    parent_id: Optional[int] = None
    level: CodeLevel = CodeLevel.open
    name: str
    description: str
    inclusion_criteria: Optional[str] = None
    exclusion_criteria: Optional[str] = None
    example_quotes: List[str] = Field(default_factory=list)
    is_active: bool = True
    sort_order: int = 0
    created_at: Optional[datetime] = None
