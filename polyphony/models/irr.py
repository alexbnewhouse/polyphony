from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class IRRRun(BaseModel):
    id: Optional[int] = None
    project_id: int
    coding_run_a_id: int
    coding_run_b_id: int
    scope: str = "all"
    krippendorff_alpha: Optional[float] = None
    cohen_kappa: Optional[float] = None
    percent_agreement: Optional[float] = None
    segment_count: Optional[int] = None
    disagreement_count: Optional[int] = None
    computed_at: Optional[datetime] = None
    notes: Optional[str] = None


class IRRDisagreement(BaseModel):
    id: Optional[int] = None
    irr_run_id: int
    segment_id: int
    code_a: Optional[str] = None
    code_b: Optional[str] = None
    resolution: Optional[str] = None
    resolved_at: Optional[datetime] = None
