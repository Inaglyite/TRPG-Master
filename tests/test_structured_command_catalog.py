"""模型可见命令目录 vs 冻结 schema：防漂移对照。

真实模型验收暴露过：提示词目录把 `record_fact.audience`、`advance_time.reason`
写成可选，而 `schemas/structured-play/v1/command_request.json` 要求必填；
`present_information.target` 在目录里干脆没写。只靠提示词补枚举、却让错误
类型进入执行层，是同一类问题。这个测试把「模型看到的命令定义」与「执行层
校验」钉死在同一份 schema 上，漂移即失败。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.structured.agent_prompts import COMMAND_CATALOG_BRIEF

SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "structured-play"
    / "v1"
    / "command_request.json"
)


def _schema_fields() -> dict[str, tuple[set[str], set[str]]]:
    data = json.loads(SCHEMA.read_text(encoding="utf-8"))
    fields: dict[str, tuple[set[str], set[str]]] = {}
    for branch in data["oneOf"]:
        definition = data["$defs"][branch["$ref"].split("/")[-1]]
        kind = definition["properties"]["kind"]["const"]
        payload = definition["properties"]["payload"]
        required = set(payload.get("required") or [])
        fields[kind] = (required, set(payload["properties"]) - required)
    return fields


def _catalog_docs() -> dict[str, str]:
    docs: dict[str, str] = {}
    for line in COMMAND_CATALOG_BRIEF.splitlines():
        match = re.match(r"- (\w+): (.*)$", line)
        if match:
            docs[match.group(1)] = match.group(2)
    return docs


def _catalog_fields(doc: str) -> tuple[set[str], set[str]]:
    """目录里写到的顶层字段：必填（无 ?）与标记可选（带 ?）两组。

    先剥掉一层花括号（`speaker{kind: ...}` 这类嵌套写法里的 kind 是枚举值，
    不是 payload 字段），再按逗号取每段的开头标识符。
    """
    stripped = re.sub(r"\{[^{}]*\}", "", doc)
    required: set[str] = set()
    optional: set[str] = set()
    for part in stripped.split(","):
        match = re.match(r"\s*([a-z_][a-z0-9_]*)(\?)?", part)
        if not match:
            continue
        (optional if match.group(2) else required).add(match.group(1))
    return required, optional


def test_catalog_covers_every_command_in_schema():
    fields = _schema_fields()
    docs = _catalog_docs()
    assert set(docs) == set(fields), (
        f"目录与 schema 的命令集合不一致：多={sorted(set(docs) - set(fields))} "
        f"少={sorted(set(fields) - set(docs))}"
    )


def test_catalog_required_fields_match_schema_exactly():
    """必填字段必须在目录里写明且不带 ?；可选字段若写了要带 ?。"""
    problems: list[str] = []
    for kind, (schema_required, schema_optional) in _schema_fields().items():
        doc_required, doc_optional = _catalog_fields(_catalog_docs()[kind])
        missing = sorted(schema_required - doc_required)
        if missing:
            problems.append(f"{kind} 漏写/标错必填字段 {missing}")
        for field in sorted(schema_required & doc_optional):
            problems.append(f"{kind}.{field} 是必填，目录却标成可选")
        for field in sorted(schema_optional & doc_required):
            problems.append(f"{kind}.{field} 是可选，目录没标 ?（易被模型当必填）")
    assert not problems, "；".join(problems)


def test_catalog_does_not_invent_fields():
    """目录不得出现 schema 里没有的字段名。"""
    problems: list[str] = []
    for kind, (schema_required, schema_optional) in _schema_fields().items():
        doc_required, doc_optional = _catalog_fields(_catalog_docs()[kind])
        unknown = sorted(
            (doc_required | doc_optional) - schema_required - schema_optional
        )
        if unknown:
            problems.append(f"{kind} 出现 schema 没有的字段 {unknown}")
    assert not problems, "；".join(problems)
