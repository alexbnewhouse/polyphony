from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class FlagType(str, Enum):
    ambiguous_segment = "ambiguous_segment"
    code_overlap = "code_overlap"
    missing_code = "missing_code"
    low_confidence = "low_confidence"
    irr_disagreement = "irr_disagreement"
    supervisor_query = "supervisor_query"


class FlagStatus(str, Enum):
    open = "open"
    in_discussion = "in_discussion"
    resolved = "resolved"
    deferred = "deferred"


class Flag(BaseModel):
    id: Optional[int] = None
    project_id: int
    raised_by: int  # agent_id
    segment_id: Optional[int] = None
    code_id: Optional[int] = None
    flag_type: FlagType
    description: str
    status: FlagStatus = FlagStatus.open
    resolution: Optional[str] = None
    resolved_by: Optional[int] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class DiscussionTurn(BaseModel):
    id: Optional[int] = None
    flag_id: int
    agent_id: int
    turn_index: int
    content: str
    llm_call_id: Optional[int] = None
    created_at: Optional[datetime] = None
