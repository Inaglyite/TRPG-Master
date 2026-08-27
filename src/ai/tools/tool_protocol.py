"""Quarantine provider tool protocols accidentally emitted as text."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Providers have emitted both ASCII/full-width bars, sometimes duplicated, and
# may split the tag at any character boundary.  Match the grammar, not a fixed
# spelling of the delimiter.
_BAR = r"[|｜]"
_START_TAG = re.compile(
    rf"<\s*(?:{_BAR}\s*)+DSML(?:\s*{_BAR})+\s*tool_calls\b[^>]*>", re.IGNORECASE
)
_END_TAG = re.compile(
    rf"</\s*(?:{_BAR}\s*)+DSML(?:\s*{_BAR})+\s*tool_calls\s*>", re.IGNORECASE
)
_DSML_TAG = re.compile(
    rf"(<\s*/?)\s*(?:{_BAR}\s*)+DSML(?:\s*{_BAR})+\s*([A-Za-z_][\w-]*)",
    re.IGNORECASE,
)
_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_MAX_PROTOCOL_CHARS = 262_144
_MAX_TAG_CHARS = 1024


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
    """Convert a complete DSML block into normalized tool calls."""
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
                combined = self.hidden + self.pending
                end = _END_TAG.search(combined)
                if end:
                    self.blocks.append(combined[:end.end()])
                    self.hidden = None
                    self.pending = combined[end.end():]
                    continue
                # Keep the whole hidden block until its closing tag arrives.
                self.hidden = combined
                self.pending = ""
                if len(self.hidden) > _MAX_PROTOCOL_CHARS:
                    self.hidden = ""
                    self.malformed = True
                break

            opening = self.pending.find("<")
            if opening < 0:
                visible.append(self.pending)
                self.pending = ""
                break
            visible.append(self.pending[:opening])
            self.pending = self.pending[opening:]
            tag_end = self.pending.find(">")
            if tag_end < 0:
                # A possible protocol tag must never be emitted prematurely.
                if len(self.pending) > _MAX_TAG_CHARS:
                    visible.append(self.pending[0])
                    self.pending = self.pending[1:]
                    continue
                break
            candidate = self.pending[:tag_end + 1]
            if _START_TAG.fullmatch(candidate):
                self.hidden = candidate
                self.pending = self.pending[tag_end + 1:]
                continue
            visible.append(candidate)
            self.pending = self.pending[tag_end + 1:]
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


def strip_tool_protocol(text: str) -> str:
    """Remove protocol blocks from already assembled or persisted narrative."""
    firewall = ToolProtocolFilter()
    return firewall.feed(text) + firewall.flush()
