"""
polyphony.prompts
============
Load and render YAML prompt templates.

Templates use Python's string.Template ($variable syntax) — intentionally
simpler than Jinja2 so social scientists can edit them without learning a
templating language.

Prompt YAML structure:
    meta:
        name: <str>
        version: <str>
        description: <str>
    system: |
        <system prompt text with $variable placeholders>
    user: |
        <user prompt text with $variable placeholders>
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template
from typing import Any, Dict, Optional, Tuple

import yaml

# Default prompts directory: alongside this file's package root
_DEFAULT_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class PromptTemplate:
    """A loaded, renderable prompt template."""

    def __init__(self, data: dict, source_path: Path):
        self.meta = data.get("meta", {})
        self.name = self.meta.get("name", source_path.stem)
        self.version = self.meta.get("version", "1.0")
        self.description = self.meta.get("description", "")
        self._system_raw = data.get("system", "")
        self._user_raw = data.get("user", "")
        self.source_path = source_path

    def render(self, **kwargs) -> Tuple[str, str]:
        """
        Render the template with the given variables.
        Returns (system_prompt, user_prompt).

        Uses safe_substitute so that missing variables are left as-is
        (useful for debugging) rather than raising KeyError.
        """
        system = Template(self._system_raw).safe_substitute(kwargs)
        user = Template(self._user_raw).safe_substitute(kwargs)
        return system, user

    def required_vars(self) -> list[str]:
        """Return a list of all $variable names in this template."""
        pattern = r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"
        sys_vars = re.findall(pattern, self._system_raw)
        usr_vars = re.findall(pattern, self._user_raw)
        return sorted(set(sys_vars + usr_vars))

    def __repr__(self) -> str:
        return f"<PromptTemplate name={self.name!r} version={self.version!r}>"


class PromptLibrary:
    """
    Loads all .yaml prompt files from a directory and provides them by name.

    Usage:
        lib = PromptLibrary()
        tmpl = lib["open_coding"]
        system, user = tmpl.render(segment_text="...", codebook_formatted="...")
    """

    def __init__(self, prompts_dir: Optional[Path] = None):
        self._dir = Path(prompts_dir or _DEFAULT_PROMPTS_DIR)
        self._cache: Dict[str, PromptTemplate] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self._dir.exists():
            raise FileNotFoundError(
                f"Prompts directory not found: {self._dir}\n"
                "Run `polyphony project new` first, or set POLYPHONY_PROMPTS_DIR."
            )
        for yaml_file in self._dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                tmpl = PromptTemplate(data, yaml_file)
                self._cache[yaml_file.stem] = tmpl
            except Exception as e:
                raise ValueError(f"Failed to load prompt {yaml_file}: {e}") from e
        self._loaded = True

    def __getitem__(self, name: str) -> PromptTemplate:
        self._ensure_loaded()
        if name not in self._cache:
            raise KeyError(
                f"No prompt named '{name}'. "
                f"Available: {sorted(self._cache.keys())}"
            )
        return self._cache[name]

    def get(self, name: str, default=None) -> Optional[PromptTemplate]:
        try:
            return self[name]
        except KeyError:
            return default

    def names(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._cache.keys())

    def reload(self) -> None:
        """Force a fresh reload from disk (useful after editing prompts)."""
        self._cache.clear()
        self._loaded = False


# Module-level singleton; can be overridden with POLYPHONY_PROMPTS_DIR env var
import os

_prompts_dir_env = os.environ.get("POLYPHONY_PROMPTS_DIR")
library = PromptLibrary(Path(_prompts_dir_env) if _prompts_dir_env else None)


def format_codebook(codes: list[dict]) -> str:
    """
    Convert a list of code dicts (from DB) into a plain-text block suitable
    for injection into prompts as $codebook_formatted.

    Groups codes by level (open → axial → selective).
    """
    if not codes:
        return "(No codes defined yet.)"

    by_level: Dict[str, list] = {"open": [], "axial": [], "selective": []}
    for c in codes:
        level = c.get("level", "open")
        by_level.setdefault(level, []).append(c)

    lines = []
    level_labels = {"open": "OPEN CODES", "axial": "AXIAL CODES", "selective": "SELECTIVE CODES"}

    for level in ("open", "axial", "selective"):
        group = by_level.get(level, [])
        if not group:
            continue
        lines.append(f"[{level_labels[level]}]")
        for code in sorted(group, key=lambda c: (c.get("sort_order", 0), c.get("name", ""))):
            if not code.get("is_active", True):
                continue
            lines.append(f"  {code['name']}")
            lines.append(f"    Description: {code['description']}")
            if code.get("inclusion_criteria"):
                lines.append(f"    Include if: {code['inclusion_criteria']}")
            if code.get("exclusion_criteria"):
                lines.append(f"    Exclude if: {code['exclusion_criteria']}")
            if code.get("example_quotes"):
                quotes = json.loads(code["example_quotes"]) if isinstance(code["example_quotes"], str) else code["example_quotes"]
                if quotes:
                    lines.append(f"    Example: \"{quotes[0]}\"")
            lines.append("")
        lines.append("")

    return "\n".join(lines).strip()
