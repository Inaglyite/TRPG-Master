"""Skill Catalog manifest: 官方目录加载、模组合成条目与内容读取边界。

H3 契约（/tmp/H3_SKILL_AUDIT.md §C1）：`.skill` 正文不改，元数据集中在
``skills/catalog.json``；模组 skills 在运行时合成为 ``bundled-module`` 条目。
content_digest 不进 catalog 文件，pin 时计算并存入 world_skill_pins。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SKILL_ID_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
TRUST_LEVELS = ("core", "bundled-module")
RESIDENCY_LEVELS = ("core", "deterministic", "on_demand")
_CATALOG_FILE = "skills/catalog.json"


class CatalogError(Exception):
    """catalog.json 缺失、非法或与磁盘内容不一致。"""


class SkillActivation(BaseModel):
    """确定性激活谓词。键之间 OR；键内任一元素命中即中；空 = 永不自动激活。"""

    tools: list[str] = Field(default_factory=list)
    combat_active: bool | None = None
    san_below: int | None = Field(default=None, ge=0, le=100)
    phases: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    module_capabilities: list[str] = Field(default_factory=list)
    rulesets: list[str] = Field(default_factory=list)


class SkillEntry(BaseModel):
    id: str = Field(min_length=3, max_length=120)
    path: str = Field(min_length=1, max_length=240)
    version: str = Field(min_length=1, max_length=80)
    trust: Literal["core", "bundled-module"]
    residency: Literal["core", "deterministic", "on_demand"]
    description: str = Field(default="", max_length=120)
    opening: bool = False
    model_invocable: bool = False
    max_context_tokens: int = Field(default=3000, ge=1, le=32_000)
    activation: SkillActivation = Field(default_factory=SkillActivation)
    diagnostic_keywords: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not SKILL_ID_RE.fullmatch(value):
            raise ValueError(f"非法 skill id: {value!r}")
        return value


class SkillCatalog(BaseModel):
    catalog_version: int = Field(ge=1, le=1)
    skills: list[SkillEntry]

    @property
    def by_id(self) -> dict[str, SkillEntry]:
        return {entry.id: entry for entry in self.skills}

    def core_entries(self, *, opening: bool = False) -> list[SkillEntry]:
        entries = [s for s in self.skills if s.residency == "core"]
        if opening:
            entries = [s for s in entries if s.opening]
        return entries

    def on_demand_entries(self) -> list[SkillEntry]:
        return [s for s in self.skills if s.residency == "on_demand"]


def _validate_catalog(catalog: SkillCatalog) -> SkillCatalog:
    seen: set[str] = set()
    for entry in catalog.skills:
        if entry.id in seen:
            raise CatalogError(f"catalog 存在重复 skill id: {entry.id}")
        seen.add(entry.id)
        if entry.model_invocable and entry.residency != "on_demand":
            raise CatalogError(f"{entry.id}: 仅 on_demand skill 可 model_invocable")
        if entry.residency == "deterministic" and not entry.activation.model_dump(
            exclude_defaults=True
        ):
            raise CatalogError(f"{entry.id}: deterministic skill 缺少 activation 谓词")
        if entry.residency == "core" and entry.activation.model_dump(exclude_defaults=True):
            raise CatalogError(f"{entry.id}: core skill 不应声明 activation 谓词")
    return catalog


def load_official_catalog(project_root: Path) -> SkillCatalog:
    """加载并校验官方 catalog；任何不一致都 fail-closed（启动期错误）。"""
    path = Path(project_root) / _CATALOG_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"无法读取 {_CATALOG_FILE}: {exc}") from exc
    try:
        catalog = SkillCatalog.model_validate(raw)
    except ValueError as exc:
        raise CatalogError(f"{_CATALOG_FILE} 校验失败: {exc}") from exc
    catalog = _validate_catalog(catalog)
    for entry in catalog.skills:
        if entry.trust != "core":
            raise CatalogError(f"{entry.id}: 官方 catalog 只允许 trust=core")
        skill_path = (Path(project_root) / entry.path).resolve()
        try:
            skill_path.relative_to((Path(project_root) / "skills").resolve())
        except ValueError as exc:
            raise CatalogError(f"{entry.id}: path 越出 skills/ 边界") from exc
        if not skill_path.is_file():
            raise CatalogError(f"{entry.id}: 文件不存在: {entry.path}")
    return catalog


def catalog_for(context) -> SkillCatalog:
    """官方 catalog + 当前模组 skills 合成的 bundled-module 条目。"""
    catalog = load_official_catalog(context.project_root)
    module_dir = Path(context.module_dir)
    skills_dir = module_dir / "skills"
    entries = list(catalog.skills)
    if skills_dir.is_dir():
        module_name = str(getattr(context, "module_name", "") or "module")
        slug = re.sub(r"[^a-z0-9_]+", "_", module_name.lower()).strip("_")
        if not slug:
            # 非 ASCII 模组名（如中文目录）用稳定哈希保证 id 合法且唯一。
            slug = "m" + hashlib.sha1(module_name.encode("utf-8")).hexdigest()[:8]
        for path in sorted(skills_dir.glob("*.skill")):
            stem = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_") or "skill"
            record = getattr(context, "module_record", None)
            entries.append(
                SkillEntry(
                    id=f"module.{slug}.{stem}",
                    path=str(path.relative_to(context.project_root))
                    if path.is_relative_to(context.project_root)
                    else str(path),
                    version=str(getattr(record, "version", "") or "0"),
                    trust="bundled-module",
                    residency="core",
                    description=f"模组 {module_name} 自带规则：{path.stem}",
                    opening=False,
                    model_invocable=False,
                )
            )
    return _validate_catalog(catalog.model_copy(update={"skills": entries}))


def skill_content_digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_skill_content(project_root: Path, entry: SkillEntry) -> str | None:
    """读取 catalog 条目正文；路径必须留在 skills/ 或模组目录边界内。"""
    root = Path(project_root).resolve()
    path = (root / entry.path).resolve()
    allowed_roots = [(root / "skills").resolve(), (root / "mod").resolve()]
    if not any(_is_relative_to(path, base) for base in allowed_roots):
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
