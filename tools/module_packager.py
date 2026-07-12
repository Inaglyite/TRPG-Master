#!/usr/bin/env python3
""".trpgmod 模组工程校验、打包与检查命令。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.module_format import manifest_json_schema, module_json_schema  # noqa: E402
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
    print(f"Schema 已写入: {output}")
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

    args = parser.parse_args()
    try:
        if args.command == "validate":
            return cmd_validate(args.package)
        if args.command == "pack":
            return cmd_pack(args.source, args.output)
        if args.command == "schema":
            return cmd_schema(args.output)
    except ModulePackageError as exc:
        _print_error(exc)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
