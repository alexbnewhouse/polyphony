from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class AgentRole(str, Enum):
    supervisor = "supervisor"
    coder_a = "coder_a"
    coder_b = "coder_b"


class AgentType(str, Enum):
    human = "human"
    llm = "llm"


class Agent(BaseModel):
    id: Optional[int] = None
    project_id: int
    role: AgentRole
    agent_type: AgentType
    model_name: Optional[str] = None
    model_version: Optional[str] = None  # Ollama digest
    temperature: float = 0.1
    seed: int = 42
    system_prompt: Optional[str] = None
    created_at: Optional[datetime] = None
