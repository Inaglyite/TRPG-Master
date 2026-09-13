"""结构化操作协议 v1：schema 与 fixtures 的双向校验。

发布给前端的契约以 `schemas/structured-play/v1/` 为准。本测试保证：

- 每个正式 fixture 通过对应 schema；每个 invalid fixture 被明确拒绝（schema 有牙齿）；
- 命令/事件枚举与 fixtures 一一对应（改了 schema 必须同步改 fixtures，反之亦然）；
- 错误码集合覆盖前端已知集（前端对未知码用服务端文案兜底，但已知码不得改名）；
- 权限矩阵覆盖全部命令 kind 与全部角色。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas" / "structured-play" / "v1"
FIXTURE_DIR = SCHEMA_DIR / "fixtures"

# 前端 protocol/structured.ts 的 REQUEST_ERROR_TEXTS 已知集；改名会破坏前端映射。
FRONTEND_KNOWN_ERROR_CODES = {
    "revision_conflict",
    "duplicate_request_conflict",
    "unknown_target",
    "stale_target",
    "unsupported_protocol",
    "not_authorized",
    "not_actor",
    "check_not_pending",
    "check_already_resolved",
    "invalid_action",
    "rate_limited",
    "keeper_unavailable",
    "request_not_found",
}

COMMAND_KINDS = {
    "publish_message",
    "request_check",
    "resolve_check",
    "present_information",
    "grant_clue",
    "use_item",
    "transfer_item",
    "adjust_stat",
    "advance_time",
    "move_party",
    "resolve_intent",
    "present_handout",
    "set_npc_presence",
    "record_fact",
}

EVENT_TYPES = {
    "session_snapshot",
    "action_ack",
    "action_status",
    "check_requested",
    "check_resolved",
    "check_cancelled",
    "roll_resolved",
    "message_started",
    "message_chunk",
    "message_completed",
    "scene_changed",
    "clue_granted",
    "inventory_changed",
    "state_changed",
    "request_error",
    "keeper_draft",
    "keeper_draft_resolved",
    "keeper_control",
    "intent_pending",
    "handout_presented",
}

SCHEMA_BY_DIR = {
    "action_request": "action_request.json",
    "free_roll_request": "free_roll_request.json",
    "check_response": "check_response.json",
    "cancel_request": "cancel_request.json",
    "command_request": "command_request.json",
    "event": "events.json",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> tuple[Registry, dict[str, dict]]:
    schemas = {
        path.name: _load_json(path)
        for path in SCHEMA_DIR.glob("*.json")
        if path.name != "permission-matrix.json"
    }
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(
            schema["$id"],
            Resource.from_contents(schema, default_specification=DRAFT202012),
        )
    return registry, schemas


class StructuredPlayProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry, cls.schemas = _registry()

    def _validator(self, name: str) -> jsonschema.Draft202012Validator:
        return jsonschema.Draft202012Validator(self.schemas[name], registry=self.registry)

    def test_schemas_are_valid_draft202012(self):
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                jsonschema.Draft202012Validator.check_schema(schema)

    def test_valid_fixtures_pass(self):
        covered: set[str] = set()
        for directory, schema_name in SCHEMA_BY_DIR.items():
            for path in sorted((FIXTURE_DIR / directory).glob("*.json")):
                with self.subTest(fixture=f"{directory}/{path.name}"):
                    self._validator(schema_name).validate(_load_json(path))
                    covered.add(f"{directory}/{path.name}")
        self.assertTrue(covered, "fixtures 目录为空")

    def test_invalid_fixtures_are_rejected(self):
        for path in sorted((FIXTURE_DIR / "invalid").glob("*.json")):
            with self.subTest(fixture=path.name):
                with self.assertRaises(jsonschema.ValidationError):
                    if "action_request" in path.name:
                        self._validator("action_request.json").validate(_load_json(path))
                    elif "free_roll" in path.name:
                        self._validator("free_roll_request.json").validate(_load_json(path))
                    elif "check_response" in path.name:
                        self._validator("check_response.json").validate(_load_json(path))
                    elif "command" in path.name:
                        self._validator("command_request.json").validate(_load_json(path))
                    elif "event" in path.name:
                        self._validator("events.json").validate(_load_json(path))
                    else:  # pragma: no cover - 新增 invalid fixture 必须落入已知前缀
                        self.fail(f"invalid fixture 没有对应 schema 映射: {path.name}")

    def test_every_command_kind_has_a_fixture(self):
        fixture_files = {path.stem for path in (FIXTURE_DIR / "command_request").glob("*.json")}
        self.assertEqual(COMMAND_KINDS, fixture_files)
        command_schema = self.schemas["command_request.json"]
        declared = {
            command_schema["$defs"][branch["$ref"].split("/")[-1]]["properties"]["kind"]["const"]
            for branch in command_schema["oneOf"]
        }
        self.assertEqual(COMMAND_KINDS, declared)

    def test_every_event_type_has_a_fixture(self):
        fixture_files = {path.stem for path in (FIXTURE_DIR / "event").glob("*.json")}
        # state_changed 有三个代表形态（调查员状态 / 世界时钟 / 在场目标）
        self.assertEqual(
            EVENT_TYPES | {"state_changed_clock", "state_changed_targets"}, fixture_files
        )
        events_schema = self.schemas["events.json"]
        declared = {
            events_schema["$defs"][branch["$ref"].split("/")[-1]]["properties"]["type"]["const"]
            for branch in events_schema["oneOf"]
        }
        self.assertEqual(EVENT_TYPES, declared)

    def test_error_codes_cover_the_frontend_known_set(self):
        codes = set(self.schemas["common.json"]["$defs"]["error_code"]["enum"])
        self.assertTrue(
            FRONTEND_KNOWN_ERROR_CODES <= codes,
            f"前端已知错误码缺失: {sorted(FRONTEND_KNOWN_ERROR_CODES - codes)}",
        )

    def test_permission_matrix_covers_commands_and_roles(self):
        matrix = _load_json(SCHEMA_DIR / "permission-matrix.json")
        roles = set(matrix["roles"])
        self.assertEqual({"owner", "keeper", "player", "viewer", "agent"}, roles)
        permissions = matrix["permissions"]
        for kind in COMMAND_KINDS:
            with self.subTest(command=kind):
                matching = {
                    key: entry
                    for key, entry in permissions.items()
                    if key == f"command.{kind}" or key.startswith(f"command.{kind}.")
                }
                self.assertTrue(matching, f"权限矩阵缺少 command.{kind} 条目")
                for key, entry in matching.items():
                    self.assertTrue(entry["roles"], f"{key} 必须至少允许一个角色")
                    unknown = set(entry["roles"]) - roles
                    self.assertFalse(unknown, f"{key} 引用了未知角色: {unknown}")
        # keeper 读秘密与 owner 管理必须分开授权
        self.assertNotIn("owner", permissions["keeper.read_module_secrets"]["roles"])
        self.assertIn("keeper", permissions["keeper.read_module_secrets"]["roles"])
        # 玩家按钮三类提交都要求控制对应调查员
        for key in (
            "action_request.submit",
            "free_roll_request.submit",
            "check_response.submit",
        ):
            self.assertIn("controls_investigator", permissions[key]["conditions"])


if __name__ == "__main__":
    unittest.main()
