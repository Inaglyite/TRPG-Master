"""Source-aware, body-free accounting of the actual model input."""

from __future__ import annotations

import hashlib
from collections import Counter

from src.ai.context.context_summary import is_control_message
from src.ai.context.lorebook import estimate_text_tokens


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def source_catalog(host) -> dict[str, dict]:
    """Resolve frozen Skill sources; never replace pins with mutable disk rules."""
    context = getattr(host, "context", None)
    world_id = str(getattr(context, "world_id", "") or "")
    cached = getattr(host, "_prompt_source_catalog", None)
    if isinstance(cached, tuple) and cached[0] == world_id:
        return cached[1]
    sources = {}

    def register(content: str, metadata: dict) -> None:
        # Skills and compiled module prose may contain the same delimiter as
        # the assembler. Attribute their transmitted subsections as well.
        for part in [content, *content.split("\n\n---\n\n")]:
            if part.strip():
                sources.setdefault(_digest(part.strip()), metadata)

    try:
        from src.ai.skills.skill_pins import read_world_pins
        from src.storage.persistence import (
            _module_prompt_content,
            neutralize_legacy_format_contract,
        )

        for pin in (read_world_pins(context) or {}).values():
            text = neutralize_legacy_format_contract(pin.content.strip())
            register(text, {
                "source_id": pin.entry.id,
                "version": pin.entry.version,
                "owner": "module" if pin.entry.trust == "bundled-module"
                else "style" if pin.entry.id == "keeper.atmosphere"
                else "ruling" if pin.entry.id in {"keeper.adjudication", "core.dice_system"}
                else "platform",
            })
        module_dir = getattr(context, "module_dir", None)
        module_file = module_dir / "module.md" if module_dir is not None else None
        if module_file is not None and module_file.exists():
            module_record = getattr(context, "module_record", None)
            register(neutralize_legacy_format_contract(_module_prompt_content(module_file.read_text(encoding="utf-8")).strip()),
                     {"source_id": "module_document", "owner": "module",
                      "version": str(getattr(module_record, "version", "unknown"))})
    except Exception:
        pass  # Accounting is optional; never change request authority on failure.
    if context is not None and world_id:
        host._prompt_source_catalog = (world_id, sources)
    return sources


def describe_prompt(messages: list[dict], sources: dict[str, dict] | None = None) -> dict:
    """Measure transmitted parts, including fresh authority and summary/history.

    No prompt bodies, private facts, keys, or arbitrary headings are returned.
    Unknown/custom sources remain explicitly unattributed.
    """
    from src.ai.model.dialogue_output import DIALOGUE_INSTRUCTION

    sources = sources or {}
    sections, history, duplicate_counts = [], {}, Counter()
    for message in messages:
        text = str(message.get("content") or "")
        if message.get("role") == "system":
            text_without_contract = text.replace(DIALOGUE_INSTRUCTION, "")
            for part in text_without_contract.split("\n\n---\n\n"):
                if not part.strip():
                    continue
                digest = _digest(part.strip())
                duplicate_counts[digest] += 1
                sections.append({**sources.get(digest, {"source_id": "unattributed", "owner": "unknown"}),
                                 "digest": digest, "chars": len(part),
                                 "estimated_tokens": estimate_text_tokens(part)})
            if DIALOGUE_INSTRUCTION in text:
                sections.append({"source_id": "dialogue_v1", "owner": "platform",
                                 "chars": len(DIALOGUE_INSTRUCTION),
                                 "estimated_tokens": estimate_text_tokens(DIALOGUE_INSTRUCTION),
                                 "copies": text.count(DIALOGUE_INSTRUCTION)})
        else:
            kind = "authority" if is_control_message(message) or "[引擎权威状态｜" in text \
                else "summary" if text.startswith("（会话摘要——") \
                else str(message.get("role") or "unknown")
            bucket = history.setdefault(kind, {"messages": 0, "chars": 0, "estimated_tokens": 0})
            bucket["messages"] += 1
            bucket["chars"] += len(text)
            bucket["estimated_tokens"] += estimate_text_tokens(text)
    return {"system_parts": sections, "history_parts": history,
            "duplicate_block_count": sum(count - 1 for count in duplicate_counts.values()),
            "ownership": {"protocol": "platform", "checks": "ruling", "facts": "module", "tone": "style"}}
