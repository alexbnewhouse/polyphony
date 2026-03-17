from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class MemoType(str, Enum):
    theoretical = "theoretical"
    methodological = "methodological"
    reflexivity = "reflexivity"
    code_definition = "code_definition"
    synthesis = "synthesis"
    analytic = "analytic"


class Memo(BaseModel):
    id: Optional[int] = None
    project_id: int
    author_id: int
    memo_type: MemoType
    title: str
    content: str
    linked_codes: List[int] = Field(default_factory=list)
    linked_segments: List[int] = Field(default_factory=list)
    linked_flags: List[int] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
