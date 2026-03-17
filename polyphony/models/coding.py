from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class RunType(str, Enum):
    induction = "induction"
    calibration = "calibration"
    independent = "independent"
    revision = "revision"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    complete = "complete"
    error = "error"


class CodingRun(BaseModel):
    id: Optional[int] = None
    project_id: int
    codebook_version_id: int
    agent_id: int
    run_type: RunType
    status: RunStatus = RunStatus.pending
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    segment_count: Optional[int] = None
    error_message: Optional[str] = None


class Assignment(BaseModel):
    id: Optional[int] = None
    coding_run_id: int
    segment_id: int
    code_id: int
    agent_id: int
    confidence: Optional[float] = None
    rationale: Optional[str] = None
    is_primary: bool = True
    created_at: Optional[datetime] = None
