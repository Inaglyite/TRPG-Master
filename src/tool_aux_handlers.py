"""Low-frequency compatibility and presentation tool handlers.

The registry stays in :mod:`src.tools`; this module keeps side-effecting legacy
handlers out of the catalog/execution policy boundary.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Callable

from .consequences import SanitySeverity, classify_sanity_consequence
from .endings import validate_ending
from .runtime import RuntimeContext
from .tool_runtime import ToolRuntime


def register_auxiliary_handlers(
    runtime: ToolRuntime,
    *,
    json_result: Callable[[object], str],
    state_command: Callable[..., str],
) -> None:
    """Register legacy handlers whose authority is enforced by ``src.tools``."""

    @runtime.handler("sanity_trigger")
    def sanity_trigger(args: dict, _context: RuntimeContext) -> str:
        consequence = classify_sanity_consequence(args.get("description", ""))
        return json.dumps(
            {
                "suggestion": consequence.severity.value,
                "note": "这是建议的严重度，最终由守秘人根据具体情境决定。确认后调用 sanity_loss(severity=...)",
                "severity_options": {
                    SanitySeverity.TRIVIAL.value: "0/1 (几乎无损失)",
                    SanitySeverity.MINOR.value: "0/1D4 (轻微不适)",
                    SanitySeverity.MODERATE.value: "1/1D6+1 (明显冲击)",
                    SanitySeverity.MAJOR.value: "1D4/2D6+2 (严重创伤)",
                    SanitySeverity.CATASTROPHIC.value: "1D10/1D100 (终极恐怖)",
                },
            },
            ensure_ascii=False,
        )

    @runtime.handler("sanity_event")
    def sanity_event(args: dict, context: RuntimeContext) -> str:
        trigger = json.loads(
            runtime.execute("sanity_trigger", {"description": args.get("description", "")}, context)
        )
        loss = json.loads(
            runtime.execute(
                "sanity_loss",
                {"severity": args.get("severity", trigger["suggestion"])},
                context,
            )
        )
        return json.dumps(
            {
                **loss,
                "description": args.get("description", ""),
                "suggested_severity": trigger.get("suggestion"),
            },
            ensure_ascii=False,
        )

    @runtime.handler("suggest_check")
    def suggest_check(args: dict, _context: RuntimeContext) -> str:
        skill = args.get("skill", "?")
        attribute = args.get("attribute", "?")
        dc = args.get("dc", 15)
        print()
        print(f"  ⚡ 检定提议：{args.get('description', '')}")
        print(f"     【{skill}】（{attribute}）— 难度：{args.get('dc_label', '中等')}（DC {dc}）")
        try:
            answer = input("  → 确定尝试吗？(y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("y", "yes", "是"):
            return json.dumps({"confirmed": True, "skill": skill, "attribute": attribute, "dc": dc})
        return json.dumps({"confirmed": False, "reason": "玩家选择不冒险"})

    @runtime.handler("cache_scene")
    def cache_scene(args: dict, context: RuntimeContext) -> str:
        scene_id = args.get("scene_id", "")
        description = args.get("description", "")
        try:

            def cache(data: dict) -> None:
                data.setdefault("scene_cache", {})[scene_id] = description

            context.world_store.update(cache)
            return json.dumps({"cached": True, "scene_id": scene_id})
        except Exception as exc:
            return json.dumps({"cached": False, "error": str(exc)})

    @runtime.handler("end_game")
    def end_game(args: dict, context: RuntimeContext) -> str:
        resolution = validate_ending(context.world_store.load(), args)
        if not resolution.get("ok"):
            return json.dumps({"game_over": False, **resolution}, ensure_ascii=False)

        def finish(data: dict) -> None:
            data["game_over"] = {
                "id": resolution.get("ending_id"),
                "type": resolution["ending_type"],
                "title": resolution["title"],
                "summary": resolution["summary"],
            }

        context.world_store.update(finish)
        return json.dumps(
            {
                "ok": True,
                "game_over": True,
                "ending_id": resolution.get("ending_id"),
                "ending_type": resolution["ending_type"],
                "title": resolution["title"],
                "summary": resolution["summary"],
            },
            ensure_ascii=False,
        )

    @runtime.handler("read_file")
    def read_file(_args: dict, _context: RuntimeContext) -> str:
        return "[错误] read_file 已停用；规则素材只能由引擎按固定资源目录注入"

    @runtime.handler("load_skill")
    def load_skill(_args: dict, _context: RuntimeContext) -> str:
        # execute_function 直调不是模型路径：模型唯一入口是 GameEngine +
        # frozen ToolPipeline（request snapshot 冻结 skill id/digest）。
        # 此 handler 固定拒绝，避免绕过快照去读当前 catalog/磁盘。
        return json_result({"ok": False, "error": "skill_not_loadable"})

    @runtime.handler("show_handout")
    def show_handout(args: dict, context: RuntimeContext) -> str:
        result = state_command(
            context,
            "cmd_show_handout",
            args.get("entity_type", "npc"),
            args.get("entity_id", ""),
            args.get("asset_id") or None,
        )
        try:
            info = json.loads(result)
            if info.get("found") and info.get("file"):
                asset_path = context.assets_dir / info["file"]
                if asset_path.exists():
                    mime = mimetypes.guess_type(str(asset_path))[0] or "image/png"
                    data = base64.b64encode(asset_path.read_bytes()).decode("ascii")
                    info["asset_data_uri"] = f"data:{mime};base64,{data}"
                    info["asset_url"] = f"/api/assets/{context.module_name}/{info['file']}"
                    result = json.dumps(info, ensure_ascii=False)
        except (json.JSONDecodeError, OSError, TypeError):
            pass
        return result
