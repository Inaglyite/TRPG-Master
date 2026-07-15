"""模组编译诊断：把模型校验和作者建议转换成稳定、可定位的结构。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from .module_format import ModuleDefinition, ModuleManifest, engine_supports

DiagnosticLevel = Literal["error", "warning", "advice"]
DiagnosticPhase = Literal[
    "manifest_validation",
    "module_validation",
    "compatibility",
    "content_advice",
    "compilation",
]


@dataclass(frozen=True)
class ModuleDiagnostic:
    phase: DiagnosticPhase
    level: DiagnosticLevel
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "phase": self.phase,
            "level": self.level,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


def _field_path(root: str, location: tuple[Any, ...]) -> str:
    path = root
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        elif part not in {"__root__", ""}:
            path += f".{part}"
    return path


def diagnostics_from_validation_error(
    exc: ValidationError,
    *,
    phase: Literal["manifest_validation", "module_validation"],
    root: Literal["manifest", "module"],
) -> tuple[ModuleDiagnostic, ...]:
    diagnostics = []
    for error in exc.errors(include_url=False):
        diagnostics.append(ModuleDiagnostic(
            phase=phase,
            level="error",
            code=str(error.get("type") or "validation_error"),
            path=_field_path(root, tuple(error.get("loc", ()))),
            message=str(error.get("msg") or "字段校验失败"),
        ))
    return tuple(diagnostics)


def analyze_module(
    manifest: ModuleManifest,
    module: ModuleDefinition,
) -> tuple[ModuleDiagnostic, ...]:
    diagnostics: list[ModuleDiagnostic] = []
    if not engine_supports(manifest.min_engine_version):
        diagnostics.append(ModuleDiagnostic(
            phase="compatibility",
            level="error",
            code="engine_too_old",
            path="manifest.min_engine_version",
            message=f"模组需要 TRPG Master {manifest.min_engine_version} 或更高版本",
        ))
    if not manifest.license.strip():
        diagnostics.append(ModuleDiagnostic(
            phase="content_advice",
            level="warning",
            code="license_missing",
            path="manifest.license",
            message="模组尚未声明许可证或授权信息",
        ))
    if not manifest.author.strip():
        diagnostics.append(ModuleDiagnostic(
            phase="content_advice",
            level="advice",
            code="author_missing",
            path="manifest.author",
            message="模组尚未填写作者信息",
        ))
    if "custom_skills" in manifest.capabilities:
        diagnostics.append(ModuleDiagnostic(
            phase="content_advice",
            level="warning",
            code="custom_skills_context",
            path="manifest.capabilities",
            message="自定义 Skill 会进入守秘人模型上下文",
        ))
    for clue_id, clue in module.clues.items():
        if clue.type == "hidden" and not clue.discovery_notes.strip():
            diagnostics.append(ModuleDiagnostic(
                phase="content_advice",
                level="advice",
                code="hidden_clue_without_discovery_notes",
                path=f"module.clues.{clue_id}.discovery_notes",
                message="隐藏线索尚未说明发现条件",
            ))
    for group_name in ("npcs", "scenes", "clues"):
        definitions = getattr(module, group_name)
        referenced = {
            definition.asset_id
            for definition in definitions.values()
            if definition.asset_id
        }
        for asset_id, asset in getattr(module.assets, group_name).items():
            exact_triggers = [
                trigger
                for trigger in asset.reveal_on
                if trigger.entity_id
            ]
            for index, trigger in enumerate(asset.reveal_on):
                if trigger.entity_id:
                    continue
                diagnostics.append(ModuleDiagnostic(
                    phase="content_advice",
                    level="warning",
                    code="text_handout_trigger_ignored",
                    path=(
                        f"module.assets.{group_name}.{asset_id}."
                        f"reveal_on[{index}]"
                    ),
                    message=(
                        "文本匹配不能授权素材展示；请用实体 asset_id 绑定，"
                        "或为 reveal_on 提供稳定 entity_id"
                    ),
                ))
            if asset_id not in referenced and not exact_triggers:
                diagnostics.append(ModuleDiagnostic(
                    phase="content_advice",
                    level="warning",
                    code="asset_without_reveal_path",
                    path=f"module.assets.{group_name}.{asset_id}",
                    message="素材没有实体关联或 reveal_on 触发规则，游戏中不会自动分发",
                ))
    return tuple(diagnostics)


def has_blocking_diagnostics(diagnostics: tuple[ModuleDiagnostic, ...]) -> bool:
    return any(diagnostic.level == "error" for diagnostic in diagnostics)
