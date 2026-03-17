from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class Document(BaseModel):
    id: Optional[int] = None
    project_id: int
    filename: str
    source_path: Optional[str] = None
    content: str
    content_hash: str
    char_count: int
    word_count: int
    status: str = "imported"
    metadata: dict = {}
    imported_at: Optional[datetime] = None


class Segment(BaseModel):
    id: Optional[int] = None
    document_id: int
    project_id: int
    segment_index: int
    text: str
    char_start: int
    char_end: int
    segment_hash: str
    is_calibration: bool = False
    created_at: Optional[datetime] = None
