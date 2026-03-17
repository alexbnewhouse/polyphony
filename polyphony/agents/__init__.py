"""polyphony agent implementations."""

from .base import BaseAgent
from .ollama_agent import OllamaAgent
from .human import HumanAgent

__all__ = ["BaseAgent", "OllamaAgent", "HumanAgent"]
