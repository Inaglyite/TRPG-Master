"""Quarantine provider tool protocols accidentally emitted as text."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

_START_MARKERS = ("<｜DSML｜tool_calls", "<|DSML|tool_calls")
_END_MARKERS = ("</｜DSML｜tool_calls>", "</|DSML|tool_calls>")
_DSML_TAG = re.compile(r"(<\s*/?)\s*(?:｜DSML｜|\|DSML\|)([A-Za-z_][\w-]*)")
_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_MAX_PROTOCOL_CHARS = 262_144


def _partial_marker_suffix(text: str) -> int:
    """Return suffix length which may be the start of a protocol marker."""
    limit = min(len(text), max(map(len, _START_MARKERS)) - 1)
    for size in range(limit, 0, -1):
        suffix = text[-size:]
        if any(marker.startswith(suffix) for marker in _START_MARKERS):
            return size
    return 0


def _typed_parameter(element: ET.Element) -> object:
    value = element.text or ""
    try:
        if "integer" in element.attrib:
            return int(value)
        if "number" in element.attrib or "float" in element.attrib:
            return float(value)
        if "boolean" in element.attrib or "bool" in element.attrib:
            return value.strip().lower() in {"1", "true", "yes"}
        if "array" in element.attrib or "object" in element.attrib:
            return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return value


def parse_dsml_tool_calls(block: str) -> list[dict]:
    """Convert a complete DSML block into normalized tool calls.

    Invalid protocol is intentionally discarded rather than exposed to players.
    """
    normalized = _DSML_TAG.sub(r"\1\2", block)
    try:
        root = ET.fromstring(normalized)
    except ET.ParseError:
        return []
    if root.tag != "tool_calls":
        return []

    calls: list[dict] = []
    for invoke in root.findall("invoke"):
        name = invoke.get("name", "")
        if not _SAFE_TOOL_NAME.fullmatch(name):
            continue
        arguments: dict[str, object] = {}
        for parameter in invoke.findall("parameter"):
            parameter_name = parameter.get("name", "")
            if _SAFE_TOOL_NAME.fullmatch(parameter_name):
                arguments[parameter_name] = _typed_parameter(parameter)
        calls.append({
            "id": f"dsml_{len(calls)}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        })
    return calls


@dataclass
class ToolProtocolFilter:
    """Streaming firewall separating public prose from text tool protocols."""

    pending: str = ""
    hidden: str | None = None
    blocks: list[str] = field(default_factory=list)
    malformed: bool = False

    def feed(self, chunk: str) -> str:
        self.pending += chunk
        visible: list[str] = []
        while self.pending:
            if self.hidden is not None:
                end_positions = [
                    (self.pending.find(marker), marker)
                    for marker in _END_MARKERS
                    if self.pending.find(marker) >= 0
                ]
                if not end_positions:
                    keep = max(map(len, _END_MARKERS)) - 1
                    if len(self.pending) > keep:
                        self.hidden += self.pending[:-keep]
                        self.pending = self.pending[-keep:]
                    if len(self.hidden) > _MAX_PROTOCOL_CHARS:
                        self.hidden = ""
                        self.malformed = True
                    break
                position, marker = min(end_positions, key=lambda item: item[0])
                self.hidden += self.pending[:position + len(marker)]
                self.blocks.append(self.hidden)
                self.hidden = None
                self.pending = self.pending[position + len(marker):]
                continue

            starts = [
                (self.pending.find(marker), marker)
                for marker in _START_MARKERS
                if self.pending.find(marker) >= 0
            ]
            if starts:
                position, _marker = min(starts, key=lambda item: item[0])
                visible.append(self.pending[:position])
                self.hidden = ""
                self.pending = self.pending[position:]
                continue
            suffix_size = _partial_marker_suffix(self.pending)
            if suffix_size:
                visible.append(self.pending[:-suffix_size])
                self.pending = self.pending[-suffix_size:]
            else:
                visible.append(self.pending)
                self.pending = ""
            break
        return "".join(visible)

    def flush(self) -> str:
        if self.hidden is not None:
            self.hidden = None
            self.pending = ""
            self.malformed = True
            return ""
        visible, self.pending = self.pending, ""
        return visible

    def tool_calls(self) -> list[dict]:
        calls: list[dict] = []
        for block in self.blocks:
            calls.extend(parse_dsml_tool_calls(block))
        return calls
