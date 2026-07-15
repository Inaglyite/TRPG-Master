"""Validation and persistence for runtime model routing settings."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,119}$")


def validate_model_id(value: object, field: str) -> str:
    model = str(value or "").strip()
    if not _MODEL_ID.fullmatch(model):
        raise ValueError(
            f"{field} 必须是 1-120 位模型 ID，只能包含字母、数字及 ._:/@+-"
        )
    return model


@dataclass(frozen=True)
class ModelSettings:
    narrative_model: str
    judgement_model: str

    @classmethod
    def validated(cls, narrative_model: object, judgement_model: object):
        return cls(
            narrative_model=validate_model_id(narrative_model, "叙述模型"),
            judgement_model=validate_model_id(judgement_model, "判定模型"),
        )

    def to_payload(self, flash_model: str, pro_model: str) -> dict:
        return {
            "narrative_model": self.narrative_model,
            "judgement_model": self.judgement_model,
            "available_models": [
                {"id": flash_model, "label": "Flash"},
                {"id": pro_model, "label": "Pro"},
            ],
        }


def persist_model_settings(path: Path, settings: ModelSettings) -> None:
    """Atomically update role models while preserving API credentials."""
    raw: dict = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(".env.json 根节点必须是对象")
        raw = loaded
    raw["narrative_model"] = settings.narrative_model
    raw["judgement_model"] = settings.judgement_model
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(raw, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
