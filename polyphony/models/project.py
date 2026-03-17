from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class Methodology(str, Enum):
    grounded_theory = "grounded_theory"
    thematic_analysis = "thematic_analysis"
    content_analysis = "content_analysis"


class ProjectStatus(str, Enum):
    setup = "setup"
    importing = "importing"
    inducing = "inducing"
    calibrating = "calibrating"
    coding = "coding"
    irr = "irr"
    discussing = "discussing"
    analyzing = "analyzing"
    done = "done"


# Pipeline order for status progression
STATUS_ORDER = list(ProjectStatus)


class Project(BaseModel):
    id: Optional[int] = None
    name: str
    slug: str
    description: Optional[str] = None
    methodology: Methodology = Methodology.grounded_theory
    research_questions: List[str] = Field(default_factory=list)
    status: ProjectStatus = ProjectStatus.setup
    config: dict = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
