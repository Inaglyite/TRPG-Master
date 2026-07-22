#!/usr/bin/env python3
"""Fast architecture ratchet used locally and in CI."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOMAIN_MODULES = (
    "src/action_resolution.py",
    "src/discovery.py",
    "src/encounters.py",
    "src/consequences.py",
)
FORBIDDEN_DOMAIN_IMPORTS = {"fastapi", "openai", "server", "src.engine"}
LINE_RATCHETS = {
    # The former limits predated the already-merged master baseline and had
    # therefore stopped being an actionable ratchet. These values are the
    # measured post-extraction baseline: server.py is below master (1699),
    # while each dedicated adapter gets its own deliberately tight ceiling.
    "src/engine.py": 2126,
    "src/model_streamer.py": 412,
    "server.py": 1699,
    "src/tools.py": 1503,
    "tools/state_manager.py": 797,
    "src/auth_http.py": 120,
    "src/multiplayer_http.py": 420,
    "src/multiplayer_ws.py": 740,
}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def check_generated_schemas(errors: list[str]) -> None:
    from src.lorebook import lorebook_json_schema
    from src.module_format import (
        manifest_json_schema,
        manifest_v2_json_schema,
        module_json_schema,
        module_v2_json_schema,
    )

    expected = {
        "module-manifest-v1.schema.json": manifest_json_schema(),
        "module-v1.schema.json": module_json_schema(),
        "module-manifest-v2.schema.json": manifest_v2_json_schema(),
        "module-v2.schema.json": module_v2_json_schema(),
        "lorebook-v3.schema.json": lorebook_json_schema(),
    }
    for filename, generated in expected.items():
        path = ROOT / "schemas" / "trpgmod" / filename
        if not path.exists() or json.loads(path.read_text(encoding="utf-8")) != generated:
            fail(errors, f"schema 不可复现: {path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    for relative, maximum in LINE_RATCHETS.items():
        count = len((ROOT / relative).read_text(encoding="utf-8").splitlines())
        if count > maximum:
            fail(errors, f"{relative} 为 {count} 行，超过架构上限 {maximum}")

    for relative in DOMAIN_MODULES:
        for imported in imports(ROOT / relative):
            if any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for forbidden in FORBIDDEN_DOMAIN_IMPORTS
            ):
                fail(errors, f"领域层 {relative} 禁止依赖 {imported}")

    server_source = (ROOT / "server.py").read_text(encoding="utf-8")
    if "msg_type ==" in server_source:
        fail(errors, "server.py 不得恢复 WebSocket msg_type 中央分发链")
    tools_source = (ROOT / "src/tools.py").read_text(encoding="utf-8")
    if 'if name ==' in tools_source or 'elif name ==' in tools_source:
        fail(errors, "src/tools.py 不得恢复工具 name 中央分发链")

    from src.tools import TOOL_RUNTIME, TOOLS

    declared = {tool["function"]["name"] for tool in TOOLS}
    missing = sorted(declared - TOOL_RUNTIME.names)
    if missing:
        fail(errors, f"工具 schema 缺少 handler: {missing}")
    check_generated_schemas(errors)

    if errors:
        for error in errors:
            print(f"[architecture] {error}", file=sys.stderr)
        return 1
    print("architecture checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
