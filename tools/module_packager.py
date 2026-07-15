#!/usr/bin/env python3
""".trpgmod 模组工程校验、打包与检查命令。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.lorebook import lorebook_json_schema  # noqa: E402
from src.module_compiler import compile_payload  # noqa: E402
from src.module_format import (  # noqa: E402
    manifest_json_schema,
    manifest_v2_json_schema,
    module_json_schema,
    module_v2_json_schema,
)
from src.module_migrations import migrate_v1_to_v2  # noqa: E402
from src.module_registry import (  # noqa: E402
    ModulePackageError,
    build_package,
    inspect_package,
)
from src.world_store import atomic_write_json  # noqa: E402


def _print_error(exc: ModulePackageError) -> None:
    print(f"错误 [{exc.code}]: {exc.message}", file=sys.stderr)
    for detail in exc.details:
        print(f"  - {detail}", file=sys.stderr)


def cmd_validate(path: Path) -> int:
    inspection = inspect_package(path)
    print(json.dumps(inspection.summary(), ensure_ascii=False, indent=2))
    return 0


def cmd_pack(source: Path, output: Path) -> int:
    inspection = build_package(source, output)
    print(f"模组包已生成: {output}")
    print(json.dumps(inspection.summary(), ensure_ascii=False, indent=2))
    return 0


def cmd_schema(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "module-manifest-v1.schema.json", manifest_json_schema())
    atomic_write_json(output / "module-v1.schema.json", module_json_schema())
    atomic_write_json(
        output / "module-manifest-v2.schema.json",
        manifest_v2_json_schema(),
    )
    atomic_write_json(output / "module-v2.schema.json", module_v2_json_schema())
    atomic_write_json(output / "lorebook-v3.schema.json", lorebook_json_schema())
    print(f"Schema 已写入: {output}")
    return 0


def _read_project_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModulePackageError("missing_file", f"模组工程缺少: {path.name}") from exc
    except UnicodeDecodeError as exc:
        raise ModulePackageError("invalid_encoding", f"{path.name} 必须使用 UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ModulePackageError(
            "invalid_json",
            f"{path.name} 不是有效 JSON: 第 {exc.lineno} 行第 {exc.colno} 列",
        ) from exc
    if not isinstance(payload, dict):
        raise ModulePackageError("invalid_json", f"{path.name} 根节点必须是 object")
    return payload


def cmd_compile(source: Path, output: Path | None) -> int:
    """从作者工程生成编译报告；仅指定 output 时才写入编译产物。"""
    source = source.resolve()
    if not source.is_dir():
        raise ModulePackageError("invalid_source", f"模组工程目录不存在: {source}")
    manifest = _read_project_json(source / "manifest.json")
    module = _read_project_json(source / "module.json")
    keeper_path = (
        source / "keeper.md"
        if manifest.get("keeper_document", "keeper.md") == "keeper.md"
        else None
    )
    try:
        keeper_notes = (
            keeper_path.read_text(encoding="utf-8")
            if keeper_path is not None and keeper_path.exists()
            else ""
        )
    except UnicodeDecodeError as exc:
        raise ModulePackageError("invalid_encoding", "keeper.md 必须使用 UTF-8") from exc

    lorebook = None
    lorebook_path = (
        source / "lorebook.json"
        if manifest.get("lorebook") == "lorebook.json"
        else None
    )
    if lorebook_path is not None:
        lorebook = _read_project_json(lorebook_path)

    preview = compile_payload(manifest, module, keeper_notes, lorebook)
    if preview.ok and output is not None:
        output.mkdir(parents=True, exist_ok=True)
        result = preview.result
        assert result is not None
        atomic_write_json(output / "world_state_initial.json", result.world_state)
        (output / "module.md").write_text(result.keeper_prompt, encoding="utf-8")
        atomic_write_json(
            output / "compilation-report.json",
            preview.to_dict(include_outputs=False),
        )
        print(f"编译产物已写入: {output}")
    print(json.dumps(
        preview.to_dict(include_outputs=output is None),
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if preview.ok else 2


def cmd_migrate_v2(source: Path, output: Path) -> int:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ModulePackageError(
            "unsafe_output",
            "v2 迁移必须输出到新目录，不能原地覆盖作者工程",
        )
    result = migrate_v1_to_v2(
        _read_project_json(source / "manifest.json"),
        _read_project_json(source / "module.json"),
    )
    output.mkdir(parents=True, exist_ok=False)
    atomic_write_json(output / "manifest.json", result.manifest)
    atomic_write_json(output / "module.json", result.module)
    atomic_write_json(output / "migration-report.json", result.report)
    print(f"v2 工程已写入: {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TRPG Master 模组包工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="校验并查看 .trpgmod")
    validate_parser.add_argument("package", type=Path)

    pack_parser = subparsers.add_parser("pack", help="把模组工程目录打包为 .trpgmod")
    pack_parser.add_argument("source", type=Path)
    pack_parser.add_argument("output", type=Path)

    schema_parser = subparsers.add_parser("schema", help="生成编辑器共享的 JSON Schema")
    schema_parser.add_argument("output", type=Path)

    compile_parser = subparsers.add_parser("compile", help="编译模组工程并输出诊断与来源追踪")
    compile_parser.add_argument("source", type=Path)
    compile_parser.add_argument(
        "--output",
        type=Path,
        help="可选的编译产物目录；省略时只打印无副作用预览",
    )
    migrate_parser = subparsers.add_parser(
        "migrate-v2",
        help="把 v1 作者工程安全迁移到新的 v2 目录",
    )
    migrate_parser.add_argument("source", type=Path)
    migrate_parser.add_argument("output", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "validate":
            return cmd_validate(args.package)
        if args.command == "pack":
            return cmd_pack(args.source, args.output)
        if args.command == "schema":
            return cmd_schema(args.output)
        if args.command == "compile":
            return cmd_compile(args.source, args.output)
        if args.command == "migrate-v2":
            return cmd_migrate_v2(args.source, args.output)
    except ModulePackageError as exc:
        _print_error(exc)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
