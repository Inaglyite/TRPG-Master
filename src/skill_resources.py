"""Trusted loading of fixed optional Skill resources."""

from __future__ import annotations

from pathlib import Path

from .config import OPTIONAL_SKILL_RESOURCES


def load_optional_skill_resource(project_root: Path, resource_id: str) -> str | None:
    """Return an allowlisted Skill beneath ``skills/``, never an arbitrary path."""
    if resource_id not in OPTIONAL_SKILL_RESOURCES:
        return None
    path = (project_root / resource_id).resolve()
    skills_root = (project_root / "skills").resolve()
    try:
        path.relative_to(skills_root)
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeError):
        return None
