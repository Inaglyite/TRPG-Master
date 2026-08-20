"""Skill Catalog manifest: 官方目录加载、模组合成条目与内容读取边界。

H3 契约（``docs/DEEPSEEK_HARNESS_ADOPTION.md`` §5.4）：`.skill` 正文不改，
元数据集中在 ``skills/catalog.json``；模组 skills 在运行时合成为
``bundled-module`` 条目。content_digest 不进 catalog 文件，pin 时计算并存入
world_skill_pins。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .lorebook import estimate_text_tokens

SKILL_ID_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
TRUST_LEVELS = ("core", "bundled-module")
RESIDENCY_LEVELS = ("core", "deterministic", "on_demand")
_CATALOG_FILE = "skills/catalog.json"
_MODULE_URI_PREFIX = "module://"
_MAX_SKILLS_PER_CATALOG = 128
_DEFAULT_MODULE_SKILL_MAX_CONTEXT_TOKENS = 12_000


class CatalogError(Exception):
    """catalog.json 缺失、非法或与磁盘内容不一致。"""


class _StrictSkillModel(BaseModel):
    """Catalog data is author input, never an extensible runtime config.

    ``extra=forbid`` is deliberately applied to every nested manifest type,
    rather than only the top-level catalog.  A misspelled capability must fail
    at install/startup instead of silently becoming an ignored security field.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


def _unique_text_list(value: list[str], *, label: str, max_item_length: int = 120) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = item.strip()
        if not cleaned or len(cleaned) > max_item_length:
            raise ValueError(f"{label} 包含空值或过长值")
        if cleaned in seen:
            raise ValueError(f"{label} 不可重复: {cleaned}")
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _safe_relative_resource(value: str) -> str:
    """Validate a portable, non-escaping resource name.

    The manifest stores logical references only.  It never stores a host
    absolute path, so an installed module cannot turn a Skill into a general
    filesystem reader by editing catalog metadata.
    """

    raw = value.strip()
    if not raw or raw.startswith("/") or "\\" in raw or "\x00" in raw:
        raise ValueError("资源必须是安全的相对 POSIX 路径")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("资源路径不得包含空段、. 或 ..")
    return path.as_posix()


def _is_project_skill_path(value: str) -> bool:
    try:
        return _safe_relative_resource(value).startswith("skills/")
    except ValueError:
        return False


def _is_module_skill_uri(value: str) -> bool:
    if not value.startswith(_MODULE_URI_PREFIX):
        return False
    try:
        return _safe_relative_resource(value[len(_MODULE_URI_PREFIX) :]).startswith("skills/")
    except ValueError:
        return False


class SkillActivation(_StrictSkillModel):
    """确定性激活谓词。键之间 OR；键内任一元素命中即中；空 = 永不自动激活。"""

    tools: list[str] = Field(default_factory=list, max_length=64)
    combat_active: bool | None = None
    san_below: int | None = Field(default=None, ge=0, le=100)
    phases: list[str] = Field(default_factory=list, max_length=32)
    scenes: list[str] = Field(default_factory=list, max_length=64)
    scene_capabilities: list[str] = Field(default_factory=list, max_length=64)
    module_capabilities: list[str] = Field(default_factory=list, max_length=64)
    rulesets: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("tools")
    @classmethod
    def _tool_names(cls, value: list[str]) -> list[str]:
        result = _unique_text_list(value, label="activation.tools", max_item_length=80)
        if any(not TOOL_NAME_RE.fullmatch(item) for item in result):
            raise ValueError("activation.tools 包含非法工具名")
        return result

    @field_validator("phases", "scenes", "scene_capabilities", "module_capabilities", "rulesets")
    @classmethod
    def _activation_names(cls, value: list[str], info) -> list[str]:
        return _unique_text_list(value, label=f"activation.{info.field_name}")


class SkillEntry(_StrictSkillModel):
    id: str = Field(min_length=3, max_length=120)
    path: str = Field(min_length=1, max_length=240)
    version: str = Field(min_length=1, max_length=80)
    trust: Literal["core", "bundled-module"]
    residency: Literal["core", "deterministic", "on_demand"]
    description: str = Field(default="", max_length=120)
    opening: bool = False
    model_invocable: bool = False
    # These fields are part of the H3 author contract.  Dependency closure is
    # implemented below; the other capability-declaration fields are rejected
    # when non-empty/true until a request-scoped tool/UI policy can enforce
    # them, so authors never receive a silently ignored permission promise.
    required_tools: list[str] = Field(default_factory=list, max_length=64)
    allowed_tools: list[str] = Field(default_factory=list, max_length=64)
    dependencies: list[str] = Field(default_factory=list, max_length=32)
    user_invocable: bool = False
    resources: list[str] = Field(default_factory=list, max_length=32)
    max_context_tokens: int = Field(default=3000, ge=1, le=32_000)
    activation: SkillActivation = Field(default_factory=SkillActivation)
    diagnostic_keywords: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not SKILL_ID_RE.fullmatch(value):
            raise ValueError(f"非法 skill id: {value!r}")
        return value

    @field_validator("required_tools", "allowed_tools")
    @classmethod
    def _declared_tool_names(cls, value: list[str], info) -> list[str]:
        result = _unique_text_list(value, label=info.field_name, max_item_length=80)
        if any(not TOOL_NAME_RE.fullmatch(item) for item in result):
            raise ValueError(f"{info.field_name} 包含非法工具名")
        return result

    @field_validator("dependencies")
    @classmethod
    def _dependencies(cls, value: list[str]) -> list[str]:
        result = _unique_text_list(value, label="dependencies", max_item_length=120)
        if any(not SKILL_ID_RE.fullmatch(item) for item in result):
            raise ValueError("dependencies 包含非法 skill id")
        return result

    @field_validator("resources")
    @classmethod
    def _resources(cls, value: list[str]) -> list[str]:
        result = _unique_text_list(value, label="resources", max_item_length=240)
        for item in result:
            if item.startswith(_MODULE_URI_PREFIX):
                _safe_relative_resource(item[len(_MODULE_URI_PREFIX) :])
            else:
                _safe_relative_resource(item)
        return result

    @field_validator("diagnostic_keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        return _unique_text_list(value, label="diagnostic_keywords")


class SkillCatalog(_StrictSkillModel):
    catalog_version: int = Field(ge=1, le=1)
    skills: list[SkillEntry] = Field(min_length=1, max_length=_MAX_SKILLS_PER_CATALOG)

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


def validate_catalog(catalog: SkillCatalog, *, frozen: bool = False) -> SkillCatalog:
    """Validate cross-entry H3 semantics and return the same frozen catalog.

    The implementation intentionally has no ambient tool registry dependency:
    it can prove that declarations are internally safe, while request-specific
    tool authorization remains the H1 pipeline's responsibility.
    """

    if not catalog.skills or len(catalog.skills) > _MAX_SKILLS_PER_CATALOG:
        raise CatalogError("catalog Skill 数量超出安全上限")

    seen: set[str] = set()
    by_id = catalog.by_id
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
        if entry.user_invocable:
            raise CatalogError(f"{entry.id}: 当前运行时尚不支持 user_invocable Skill")
        if entry.required_tools or entry.allowed_tools:
            raise CatalogError(
                f"{entry.id}: 当前运行时尚不支持 required_tools/allowed_tools 声明"
            )
        if not frozen and entry.trust == "core":
            if not _is_project_skill_path(entry.path):
                raise CatalogError(f"{entry.id}: core Skill 必须位于 skills/ 安全路径")
            if any(not _is_project_skill_path(resource) for resource in entry.resources):
                raise CatalogError(f"{entry.id}: core Skill resource 必须位于 skills/ 安全路径")
        elif not frozen and entry.trust == "bundled-module":
            if not _is_module_skill_uri(entry.path):
                raise CatalogError(f"{entry.id}: bundled Skill 必须使用 module://skills/ 路径")
            if any(not _is_module_skill_uri(resource) for resource in entry.resources):
                raise CatalogError(
                    f"{entry.id}: bundled Skill resource 必须使用 module://skills/ 路径"
                )

        if entry.dependencies and entry.residency != "deterministic":
            raise CatalogError(f"{entry.id}: 只有 deterministic Skill 可声明 dependencies")
        for dependency_id in entry.dependencies:
            dependency = by_id.get(dependency_id)
            if dependency is None:
                raise CatalogError(f"{entry.id}: dependency 不存在: {dependency_id}")
            if dependency_id == entry.id:
                raise CatalogError(f"{entry.id}: dependency 不可引用自身")
            # Dependencies participate in the deterministic activation
            # lifecycle.  Core rules are already always present and on-demand
            # rules require a model request, so neither has a safe meaning as
            # a transient dependency.
            if dependency.residency != "deterministic":
                raise CatalogError(
                    f"{entry.id}: dependency 必须引用 deterministic Skill: {dependency_id}"
                )

    # Detect all dependency cycles up front.  Resolver closure below can then
    # remain deterministic and never recurse through author-controlled loops.
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(skill_id: str) -> None:
        if skill_id in visited:
            return
        if skill_id in visiting:
            raise CatalogError(f"Skill dependency 存在循环: {skill_id}")
        visiting.add(skill_id)
        for dependency_id in by_id[skill_id].dependencies:
            visit(dependency_id)
        visiting.remove(skill_id)
        visited.add(skill_id)

    for entry in catalog.skills:
        visit(entry.id)
    return catalog


# Backward-compatible internal spelling retained for existing private callers.
_validate_catalog = validate_catalog


def load_official_catalog(project_root: Path) -> SkillCatalog:
    """加载并校验官方 catalog；任何不一致都 fail-closed（启动期错误）。"""
    path = Path(project_root) / _CATALOG_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"无法读取 {_CATALOG_FILE}: {exc}") from exc
    try:
        catalog = SkillCatalog.model_validate(raw)
    except ValidationError as exc:
        raise CatalogError(f"{_CATALOG_FILE} 校验失败: {exc}") from exc
    catalog = validate_catalog(catalog)
    for entry in catalog.skills:
        if entry.trust != "core":
            raise CatalogError(f"{entry.id}: 官方 catalog 只允许 trust=core")
        content = read_skill_content(project_root, entry)
        if content is None:
            raise CatalogError(f"{entry.id}: 文件不存在: {entry.path}")
        if not skill_content_within_budget(content, entry):
            raise CatalogError(f"{entry.id}: Skill 正文超出 max_context_tokens")
    return catalog


def catalog_for(context) -> SkillCatalog:
    """官方 catalog + 当前模组 skills 合成的 bundled-module 条目。"""
    catalog = load_official_catalog(context.project_root)
    entries = list(catalog.skills)
    module_dir = _trusted_module_dir(context)
    skills_dir = module_dir / "skills" if module_dir is not None else None
    if skills_dir is not None and skills_dir.is_dir():
        module_name = str(getattr(context, "module_name", "") or "module")
        slug = re.sub(r"[^a-z0-9_]+", "_", module_name.lower()).strip("_")
        if not slug:
            # 非 ASCII 模组名（如中文目录）用稳定哈希保证 id 合法且唯一。
            slug = "m" + hashlib.sha1(module_name.encode("utf-8")).hexdigest()[:8]
        skills_root = skills_dir.resolve()
        if not _is_relative_to(skills_root, module_dir.resolve()):
            raise CatalogError("模组 skills/ 目录越出受信 module 根目录")
        for path in sorted(skills_dir.glob("*.skill")):
            # The package installer rejects symlinks, but legacy builtin
            # directories are writable by their owner.  Do the boundary check
            # again at runtime so a symlink cannot turn a custom Skill into an
            # arbitrary host-file reader.
            if not _is_relative_to(path.resolve(), skills_root):
                raise CatalogError(f"模组 Skill 路径越出 skills/ 边界: {path.name}")
            stem = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_") or "skill"
            record = getattr(context, "module_record", None)
            entry = SkillEntry(
                id=f"module.{slug}.{stem}",
                # Never persist an absolute installed-module path.  The
                # trusted runtime context supplies the module root only when
                # the engine itself asks to read this logical URI.
                path=f"{_MODULE_URI_PREFIX}skills/{path.name}",
                version=str(getattr(record, "version", "") or "0"),
                trust="bundled-module",
                residency="core",
                description=f"模组 {module_name} 自带规则：{path.stem}",
                opening=False,
                model_invocable=False,
                # Third-party packages currently contain declaration-free
                # ``.skill`` text.  Until module format v2 adds per-Skill
                # manifests, use a fixed bounded budget instead of letting
                # arbitrary author text become unbounded core prompt content.
                max_context_tokens=_DEFAULT_MODULE_SKILL_MAX_CONTEXT_TOKENS,
            )
            content = read_skill_content(context.project_root, entry, module_dir=module_dir)
            if content is None:
                raise CatalogError(f"模组 Skill 内容不可读: {path.name}")
            if not skill_content_within_budget(content, entry):
                raise CatalogError(f"{entry.id}: Skill 正文超出 max_context_tokens")
            entries.append(entry)
    return validate_catalog(catalog.model_copy(update={"skills": entries}))


def skill_content_digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def skill_content_within_budget(content: str, entry: SkillEntry) -> bool:
    """Whether frozen text is small enough for this Skill's declared budget."""

    return estimate_text_tokens(content) <= entry.max_context_tokens


def read_skill_content(
    project_root: Path,
    entry: SkillEntry,
    *,
    module_dir: Path | None = None,
) -> str | None:
    """Read one Skill body through its manifest-scoped resource boundary.

    Core entries may only resolve beneath ``<project>/skills``.  Bundled
    entries use the logical ``module://skills/...`` namespace and require an
    already trusted module root from :func:`catalog_for`/``RuntimeContext``;
    passing an arbitrary absolute ``entry.path`` is never sufficient.
    """

    path = _resolve_manifest_path(project_root, entry, entry.path, module_dir=module_dir)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def read_skill_resource(
    project_root: Path,
    entry: SkillEntry,
    resource: str,
    *,
    module_dir: Path | None = None,
) -> str | None:
    """Read an explicitly allowlisted text resource for a trusted engine.

    There is intentionally no model tool exposing this helper.  It exists so
    future deterministic engine code has one narrow, audited path rather than
    falling back to ``read_file`` or a host absolute path.
    """

    if resource not in entry.resources:
        return None
    path = _resolve_manifest_path(project_root, entry, resource, module_dir=module_dir)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _resolve_manifest_path(
    project_root: Path,
    entry: SkillEntry,
    reference: str,
    *,
    module_dir: Path | None,
) -> Path | None:
    root = Path(project_root).resolve()
    if entry.trust == "core":
        if not _is_project_skill_path(reference):
            return None
        base = (root / "skills").resolve()
        path = (root / reference).resolve()
        if not _is_relative_to(base, root):
            return None
    elif entry.trust == "bundled-module":
        if module_dir is None or not _is_module_skill_uri(reference):
            return None
        module_root = Path(module_dir).resolve()
        base = (module_root / "skills").resolve()
        path = (module_root / reference[len(_MODULE_URI_PREFIX) :]).resolve()
        if not _is_relative_to(base, module_root):
            return None
    else:  # Defensive: ``SkillEntry`` normally makes this unreachable.
        return None
    if not _is_relative_to(path, base) or not path.is_file():
        return None
    return path


def _trusted_module_dir(context) -> Path | None:
    """Return the module root only when it is anchored by the runtime registry.

    Builtin/legacy modules are allowed below the immutable project ``mod/``
    root.  Installed modules must agree with their resolved ``ModuleRecord``
    and live below ``<runtime>/modules``.  This makes the user-owned install
    root usable without ever accepting arbitrary absolute Skill paths.
    """

    raw_module_dir = getattr(context, "module_dir", None)
    if not raw_module_dir:
        return None
    project_root = Path(context.project_root).resolve()
    module_dir = Path(raw_module_dir).resolve()
    record = getattr(context, "module_record", None)
    record_path = getattr(record, "path", None)
    if record_path:
        try:
            if Path(record_path).resolve() != module_dir:
                raise CatalogError("运行时 module_dir 与注册表 ModuleRecord 不一致")
        except OSError as exc:
            raise CatalogError("运行时 ModuleRecord 路径不可解析") from exc

    source = str(getattr(record, "source", "") or "")
    if source == "user":
        runtime_root = getattr(context, "runtime_root", None)
        if not runtime_root:
            raise CatalogError("已安装模组缺少受信 runtime_root")
        user_root = (Path(runtime_root).resolve() / "modules").resolve()
        if not _is_relative_to(module_dir, user_root):
            raise CatalogError("已安装模组路径越出 runtime modules/ 边界")
        capabilities = {str(item) for item in (getattr(record, "capabilities", ()) or ())}
        if "custom_skills" not in capabilities:
            if (module_dir / "skills").is_dir():
                raise CatalogError("已安装模组含 skills/ 但 manifest 未声明 custom_skills")
            return None
        return module_dir

    # Builtin legacy modules are project-owned; synthetic test contexts that
    # lack a full ModuleRecord follow the same bounded path only.
    builtin_root = (project_root / "mod").resolve()
    if not _is_relative_to(module_dir, builtin_root):
        raise CatalogError("模组路径越出 project mod/ 边界")
    return module_dir


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
