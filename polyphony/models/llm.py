from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel


class LLMCall(BaseModel):
    id: Optional[int] = None
    project_id: int
    agent_id: int
    call_type: str
    model_name: str
    model_version: str = "unknown"
    temperature: float
    seed: int
    system_prompt: str
    user_prompt: str
    full_response: str
    parsed_output: Optional[Dict[str, Any]] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    called_at: Optional[datetime] = None
    assignment_id: Optional[int] = None
    flag_id: Optional[int] = None
    memo_id: Optional[int] = None
