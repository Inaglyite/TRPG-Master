"""World-level Skill pins: 每个世界冻结一次的 Skill 内容 + manifest 元数据快照。

冻结语义（H3 契约）：

- 世界**零 pin** 时把当前 catalog 整体原子快照进 ``world_skill_pins``（内容 +
  digest + 完整 ``SkillEntry`` manifest JSON + catalog 顺序）；之后任何磁盘
  改动、reset、catalog 元数据改动都不会影响这个世界的 pin（绝不热更新）。
- **fail-closed 边界**：已存在世界的 pin 读取失败、完整性校验失败（digest
  不匹配 / 快照非法 / 行列不一致）、或首次初始化失败，抛 ``PinUnavailable``
  受控错误——调用方不得回退磁盘。仅当上下文根本不是 DB 世界（鸭子上下文 /
  world 行尚未创建）时返回 ``None``，允许遗留磁盘路径；数据库或 pin 表不可读
  一律视为受控失败，绝不把它误当作 legacy 回退。
- 分支世界零 pin 时必须从父世界复制同一快照；孤儿、自指、循环、超深祖先链或
  父世界无法给出 pin 一律受控失败，绝不把分支独立按当前磁盘首次初始化。
- 并发安全：进程内按 (url, world_id) 加锁 + ``uq_world_skill_pin`` 唯一约束兜底；
  并发首 pin 的 loser 回滚后重读 winner 的结果，不会半插入。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.ai.skills.skill_manifest import (
    CatalogError,
    SkillActivation,
    SkillCatalog,
    SkillEntry,
    catalog_for,
    read_skill_content,
    skill_content_digest,
    skill_content_within_budget,
    validate_catalog,
)
from src.storage.database import (
    World,
    WorldSkillPin,
    WorldSkillPinManifest,
    new_id,
    session_scope,
)


class PinUnavailable(RuntimeError):
    """已存在世界的 Skill pin 读取/校验/初始化失败（受控错误，禁止磁盘回退）。"""


@dataclass(frozen=True)
class PinnedSkill:
    skill_id: str
    version: str
    digest: str
    trust: str
    residency: str
    content: str
    entry: SkillEntry  # 冻结的 manifest 元数据（pin 时重建，行为治理的唯一依据）
    order: int  # pin 时的 catalog 顺序（prompt 拼接顺序）
    catalog_version: int = 1  # pin 时的 catalog 版本（v1 遗产/旧快照为 1）


_PIN_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_PIN_LOCKS_GUARD = threading.Lock()
_MAX_LINEAGE_DEPTH = 8
_ENTRY_SNAPSHOT_FIELDS = frozenset(SkillEntry.model_fields)
_ACTIVATION_SNAPSHOT_FIELDS = frozenset(SkillActivation.model_fields)
_SNAPSHOT_FIELDS = frozenset({"order", "catalog_version", "catalog_ids"}) | _ENTRY_SNAPSHOT_FIELDS
# New H3 declarations added after the first sidecar release are safe only
# when omitted: all default to no capability / no dependency / no resource.
# Existing frozen worlds therefore remain readable without consulting current
# disk metadata, while an unknown or partially written authorization field is
# still rejected.
_OPTIONAL_SNAPSHOT_FIELDS = frozenset(
    {"required_tools", "allowed_tools", "dependencies", "user_invocable", "resources"}
)
_OPTIONAL_ACTIVATION_SNAPSHOT_FIELDS = frozenset({"scene_capabilities"})
_LEGACY_PIN_MAX_CONTEXT_TOKENS = 12_000


def _pin_lock(database_url: str, world_id: str) -> threading.RLock:
    key = (database_url, world_id)
    with _PIN_LOCKS_GUARD:
        lock = _PIN_LOCKS.get(key)
        if lock is None:
            lock = _PIN_LOCKS[key] = threading.RLock()
        return lock


def _snapshot_payload(entry: SkillEntry, order: int, catalog: SkillCatalog) -> dict:
    return {
        "order": order,
        "catalog_version": catalog.catalog_version,
        "catalog_ids": [item.id for item in catalog.skills],
        **entry.model_dump(mode="json"),
    }


def _legacy_entry_from_row(skill_row: WorldSkillPin) -> tuple[SkillEntry, int]:
    """Conservative compatibility view for a *pure* pre-0011 pin set."""

    # 内容-only 遗产 pin：元数据缺失时不能反读当前 catalog。为了保留可玩
    # 的规则行为，全部当作 normal prompt 的 core 内容（不是模型按需或自动
    # 工具能力），并施加固定的单条安全上限。这样旧 deterministic/on_demand
    # 内容仍来自冻结正文，却不会猜测已丢失的 activation/model 权限。
    entry = SkillEntry(
        id=skill_row.skill_id,
        path="skills/legacy-pinned.skill",
        version=skill_row.skill_version or "0",
        trust=skill_row.trust if skill_row.trust in ("core", "bundled-module") else "core",
        residency="core",
        max_context_tokens=_LEGACY_PIN_MAX_CONTEXT_TOKENS,
    )
    return entry, 1 << 30


def _strict_snapshot_entry(
    skill_row: WorldSkillPin, snapshot: object
) -> tuple[SkillEntry, int, int]:
    """Rebuild one full H3 sidecar snapshot, rejecting any partial shape.

    The sidecar is a frozen authorization manifest, not a convenience cache.
    Once a world has any sidecar row, an empty JSON object, default-filled
    field, or mismatched metadata must therefore be a controlled failure — it
    must never silently turn into a legacy row or a live catalog read.
    Returns ``(entry, order, catalog_version)``.
    """

    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("快照不是非空 JSON object")
    snapshot_keys = set(snapshot)
    missing_fields = _SNAPSHOT_FIELDS - snapshot_keys
    extra_fields = snapshot_keys - _SNAPSHOT_FIELDS
    if extra_fields or missing_fields - _OPTIONAL_SNAPSHOT_FIELDS:
        missing = sorted(missing_fields - _OPTIONAL_SNAPSHOT_FIELDS)
        extra = sorted(extra_fields)
        details = []
        if missing:
            details.append("缺少 " + ", ".join(missing))
        if extra:
            details.append("未知 " + ", ".join(extra))
        raise ValueError("快照字段不完整: " + "; ".join(details))

    # ``0011`` snapshots predate the capability declarations above.  Omission
    # of only those explicitly safe defaults is compatible; source catalogs
    # still emit the complete modern shape for every new pin.
    normalized = dict(snapshot)
    for field_name in _OPTIONAL_SNAPSHOT_FIELDS - snapshot_keys:
        normalized[field_name] = SkillEntry.model_fields[field_name].get_default(call_default_factory=True)

    order = normalized["order"]
    if isinstance(order, bool) or not isinstance(order, int) or order < 0:
        raise ValueError("order 非法")
    catalog_version = normalized["catalog_version"]
    # v1：H3/0011 快照；v2：H3.1 起的新 pin。两版快照结构一致，v1 缺失的
    # H3.1 声明字段已按上面的 optional 缺省规范化。
    if isinstance(catalog_version, bool) or catalog_version not in (1, 2):
        raise ValueError("catalog_version 非法")
    catalog_ids = normalized["catalog_ids"]
    if (
        not isinstance(catalog_ids, list)
        or not catalog_ids
        or any(not isinstance(skill_id, str) or not skill_id for skill_id in catalog_ids)
        or len(set(catalog_ids)) != len(catalog_ids)
    ):
        raise ValueError("catalog_ids 非法")

    activation = normalized["activation"]
    if not isinstance(activation, dict):
        raise ValueError("activation 快照字段不完整")
    missing_activation = _ACTIVATION_SNAPSHOT_FIELDS - set(activation)
    extra_activation = set(activation) - _ACTIVATION_SNAPSHOT_FIELDS
    if extra_activation or missing_activation - _OPTIONAL_ACTIVATION_SNAPSHOT_FIELDS:
        raise ValueError("activation 快照字段不完整")
    activation = dict(activation)
    for field_name in _OPTIONAL_ACTIVATION_SNAPSHOT_FIELDS - set(activation):
        activation[field_name] = SkillActivation.model_fields[field_name].get_default(
            call_default_factory=True
        )
    normalized["activation"] = activation
    entry = SkillEntry.model_validate(
        {key: normalized[key] for key in _ENTRY_SNAPSHOT_FIELDS}
    )
    activation_values = entry.activation.model_dump(exclude_defaults=True)
    if entry.model_invocable and entry.residency != "on_demand":
        raise ValueError("非 on_demand Skill 不可 model_invocable")
    if entry.user_invocable and entry.residency != "on_demand":
        raise ValueError("非 on_demand Skill 不可 user_invocable")
    if entry.residency == "on_demand" and (entry.required_tools or entry.allowed_tools):
        raise ValueError("on_demand Skill 不可声明工具策略")
    if entry.residency == "deterministic" and not activation_values:
        raise ValueError("deterministic Skill 缺少 activation")
    if entry.residency == "core" and activation_values:
        raise ValueError("core Skill 不可声明 activation")
    if entry.id != skill_row.skill_id:
        raise ValueError("快照 id 与 pin 行不一致")
    return entry, order, catalog_version


def _pin_from_row(
    row: WorldSkillPin, snapshot: object, *, legacy: bool
) -> PinnedSkill:
    # 完整性校验：content 必须匹配 digest；快照必须合法且与列一致。
    if skill_content_digest(row.content) != row.content_digest:
        raise PinUnavailable(f"pin 内容 digest 不匹配: {row.skill_id}")
    try:
        entry, order, catalog_version = (
            (*_legacy_entry_from_row(row), 1)
            if legacy
            else _strict_snapshot_entry(row, snapshot)
        )
    except Exception as exc:
        raise PinUnavailable(f"pin 快照非法: {row.skill_id} ({type(exc).__name__})") from exc
    if entry.id != row.skill_id:
        raise PinUnavailable(f"pin 快照 id 与列不一致: {row.skill_id}")
    if not skill_content_within_budget(row.content, entry):
        raise PinUnavailable(f"pin 内容超出 max_context_tokens: {row.skill_id}")
    if not legacy:
        if entry.trust != row.trust or entry.residency != row.residency:
            raise PinUnavailable(f"pin 快照 trust/residency 与列不一致: {row.skill_id}")
        if entry.version != (row.skill_version or ""):
            raise PinUnavailable(f"pin 快照 version 与列不一致: {row.skill_id}")
    return PinnedSkill(
        skill_id=row.skill_id,
        version=row.skill_version,
        digest=row.content_digest,
        trust=row.trust,
        residency=row.residency,
        content=row.content,
        entry=entry,
        order=order,
        catalog_version=catalog_version,
    )


def _read_pins(database_url: str, world_id: str) -> dict[str, PinnedSkill]:
    """读取并校验一个世界的全部 pin（含 sidecar manifest）；损坏抛 PinUnavailable。"""
    try:
        with session_scope(database_url) as session:
            rows = (
                session.query(WorldSkillPin)
                .filter_by(world_id=world_id)
                .order_by(WorldSkillPin.pinned_at, WorldSkillPin.id)
                .all()
            )
            manifests = (
                session.query(WorldSkillPinManifest)
                .filter(WorldSkillPinManifest.pin_id.in_([row.id for row in rows]))
                .all()
                if rows
                else []
            )
            snapshots = {row.pin_id: row.entry_snapshot for row in manifests}
            # A world is legacy only when *none* of its rows has a sidecar.
            # ``None``/``{}`` in an existing sidecar row is corruption, not a
            # license to manufacture conservative defaults.
            legacy = not manifests
            pins = {
                row.skill_id: _pin_from_row(row, snapshots.get(row.id), legacy=legacy)
                for row in rows
            }
    except PinUnavailable:
        raise
    except Exception as exc:
        raise PinUnavailable(f"world={world_id} Skill pin 读取失败: {type(exc).__name__}") from exc
    if len(pins) != len(rows):
        raise PinUnavailable(f"world={world_id} Skill pin 存在重复 skill_id")
    if pins:
        _validate_pin_set(world_id, pins, rows, snapshots)
        # Validate the frozen catalog at the earliest shared read boundary,
        # not only in prompt/resolver callers.  Otherwise a malformed sidecar
        # could be consumed by a narrower path such as the model loader before
        # its unsupported capability/dependency declaration was rejected.
        pinned_catalog(pins)
    return pins


def _validate_pin_set(
    world_id: str, pins: dict[str, PinnedSkill], rows: list[WorldSkillPin], snapshots: dict
) -> None:
    """Validate a complete H3 sidecar set, or a pure legacy set only.

    Per-row schema validation happens in :func:`_strict_snapshot_entry`.
    This function verifies the cross-row snapshot: all rows share one exact
    ordered catalog, every catalog entry has exactly one pin, and the saved
    ``order`` values still describe that catalog.  Partial snapshots cannot
    change a world's model-visible authority surface.
    """

    if not snapshots:
        return  # Pure pre-0011 pin set: conservative compatibility path.
    have_snapshot = [row for row in rows if row.id in snapshots]
    if len(have_snapshot) != len(rows):
        missing = sorted(row.skill_id for row in rows if row.id not in snapshots)
        raise PinUnavailable(
            f"world={world_id} Skill pin 元数据不完整，缺少快照行: {', '.join(missing)}"
        )
    ordered_catalogs = [tuple(snapshots[row.id]["catalog_ids"]) for row in rows]
    if len(set(ordered_catalogs)) != 1:
        raise PinUnavailable(f"world={world_id} Skill pin 快照的 catalog_ids 不一致")
    catalog_versions = {pins[row.skill_id].catalog_version for row in rows}
    if len(catalog_versions) != 1:
        raise PinUnavailable(f"world={world_id} Skill pin 快照的 catalog_version 不一致")
    catalog_ids = ordered_catalogs[0]
    if len(catalog_ids) != len(rows) or set(catalog_ids) != set(pins):
        missing = sorted(set(catalog_ids) - set(pins))
        extra = sorted(set(pins) - set(catalog_ids))
        details = []
        if missing:
            details.append("缺少行: " + ", ".join(missing))
        if extra:
            details.append("未知行: " + ", ".join(extra))
        raise PinUnavailable(f"world={world_id} Skill pin 集不完整，" + "; ".join(details))
    orders = [pins[row.skill_id].order for row in rows]
    if sorted(orders) != list(range(len(rows))):
        raise PinUnavailable(f"world={world_id} Skill pin 快照 order 不完整或重复")
    for row in rows:
        pin = pins[row.skill_id]
        if catalog_ids[pin.order] != row.skill_id:
            raise PinUnavailable(f"world={world_id} Skill pin 快照 order 与 skill_id 不一致")


def _world_row(database_url: str, world_id: str) -> World | None:
    with session_scope(database_url) as session:
        return session.get(World, world_id)


def _snapshot_catalog(
    world_id: str,
    project_root,
    catalog: SkillCatalog,
    *,
    module_dir=None,
) -> list[tuple[WorldSkillPin, WorldSkillPinManifest]]:
    rows = []
    for order, entry in enumerate(catalog.skills):
        content = read_skill_content(project_root, entry, module_dir=module_dir)
        if content is None:
            raise CatalogError(f"skill 内容不可读: {entry.id} ({entry.path})")
        if not skill_content_within_budget(content, entry):
            raise CatalogError(f"skill 内容超出 max_context_tokens: {entry.id}")
        pin = WorldSkillPin(
            id=new_id("wsp"),
            world_id=world_id,
            skill_id=entry.id,
            skill_version=entry.version,
            content_digest=skill_content_digest(content),
            trust=entry.trust,
            residency=entry.residency,
            content=content,
        )
        manifest = WorldSkillPinManifest(
            pin_id=pin.id,
            entry_snapshot=_snapshot_payload(entry, order, catalog),
        )
        rows.append((pin, manifest))
    return rows


def _insert_pins(
    database_url: str, rows: list[tuple[WorldSkillPin, WorldSkillPinManifest]]
) -> None:
    with session_scope(database_url) as session:
        for pin, manifest in rows:
            session.add(pin)
            session.add(manifest)
        session.flush()


def read_world_pins(context) -> dict[str, PinnedSkill] | None:
    """只读已有 pin（不首 pin）。

    ``None`` 只代表非 DB duck context、world 行尚未建立或真实的零 pin
    世界。数据库/表不可读不是“没有 pin”：对一个可能已经在运行的 DB 世界
    回退磁盘会形成热更新通道，因此必须 fail closed。
    """
    probe = probe_world_pins(context)
    if probe.state == "unreadable":
        raise PinUnavailable("Skill pin 数据库不可读")
    return probe.pins if probe.state == "ready" else None


@dataclass(frozen=True)
class PinProbe:
    """世界 pin 状态区分：no_db / unreadable / no_world / empty / ready。

    ``unreadable`` 是库结构/连接不可读（如未迁移旧 schema），与
    ``empty``（世界存在但尚未 pin）严格区分；公开入口会把它转换为
    ``PinUnavailable``，绝不能误作可回退磁盘的 legacy state。
    """

    state: str
    pins: dict[str, PinnedSkill] | None = None


def probe_world_pins(context) -> PinProbe:
    world_id = str(getattr(context, "world_id", "") or "")
    database_url = str(getattr(context, "database_url", "") or "")
    if not world_id or not database_url:
        return PinProbe("no_db")
    try:
        world = _world_row(database_url, world_id)
    except Exception:
        return PinProbe("unreadable")
    if world is None:
        return PinProbe("no_world")
    pins = _read_pins(database_url, world_id)
    return PinProbe("ready", pins) if pins else PinProbe("empty")


def _ensure_impl(
    database_url: str,
    world_id: str,
    project_root,
    catalog: SkillCatalog,
    *,
    depth: int,
    module_dir=None,
    lineage: tuple[str, ...] = (),
) -> dict[str, PinnedSkill]:
    """世界行已确认存在后的 pin 读取/继承/首 pin（全程 fail-closed）。"""
    if world_id in lineage:
        raise PinUnavailable(f"world={world_id} Skill pin 分支祖先链存在循环")
    if depth > _MAX_LINEAGE_DEPTH:
        raise PinUnavailable(f"world={world_id} Skill pin 分支祖先链超过深度上限")
    lineage = (*lineage, world_id)
    pins = _read_pins(database_url, world_id)
    if pins:
        return pins

    with _pin_lock(database_url, world_id):
        # 锁内重读：并发首 pin 的 winner 可能已经写入。
        pins = _read_pins(database_url, world_id)
        if pins:
            return pins

        # 分支世界：先继承父世界的冻结快照，绝不绕过父世界独立按磁盘 pin。
        parent_id = ""
        try:
            world = _world_row(database_url, world_id)
            if world is None:
                raise PinUnavailable(f"world={world_id} 不存在")
            metadata = getattr(world, "metadata_json", None) or {}
            branch = metadata.get("branch") if isinstance(metadata, dict) else None
            if isinstance(metadata, dict) and "branch" in metadata:
                if not isinstance(branch, dict):
                    raise PinUnavailable(f"world={world_id} 分支元数据非法")
                parent_id = str(branch.get("parent_world_id") or "")
                if not parent_id:
                    raise PinUnavailable(f"world={world_id} 分支缺少 parent_world_id")
        except Exception as exc:
            if isinstance(exc, PinUnavailable):
                raise
            raise PinUnavailable(
                f"world={world_id} 世界元数据读取失败: {type(exc).__name__}"
            ) from exc
        if parent_id:
            if parent_id == world_id:
                raise PinUnavailable(f"world={world_id} 分支父世界不能是自身")
            if depth >= _MAX_LINEAGE_DEPTH:
                raise PinUnavailable(f"world={world_id} Skill pin 分支祖先链超过深度上限")
            parent_world = _world_row(database_url, parent_id)
            if parent_world is None:
                raise PinUnavailable(
                    f"world={world_id} 分支父世界不存在: {parent_id}"
                )
            # 先确保父世界 pin（递归沿分支链向上），再复制到本世界。父链损坏、
            # 复制为空或深度异常都必须停在这里；分支不能悄悄按当前磁盘重 pin。
            _ensure_impl(
                database_url,
                parent_id,
                project_root,
                catalog,
                depth=depth + 1,
                module_dir=module_dir,
                lineage=lineage,
            )
            copy_world_pins(database_url, parent_id, world_id)
            inherited = _read_pins(database_url, world_id)
            if inherited:
                return inherited
            raise PinUnavailable(
                f"world={world_id} 从父世界 {parent_id} 继承 Skill pin 后仍为空"
            )

        try:
            rows = _snapshot_catalog(
                world_id,
                project_root,
                catalog,
                module_dir=module_dir,
            )
            _insert_pins(database_url, rows)
        except IntegrityError:
            # 并发首 pin 的 loser：唯一约束兜底，回滚后读 winner 的结果。
            pins = _read_pins(database_url, world_id)
            if pins:
                return pins
            raise PinUnavailable(f"world={world_id} Skill pin 并发初始化后仍为空") from None
        except Exception as exc:
            raise PinUnavailable(
                f"world={world_id} Skill pin 初始化失败: {type(exc).__name__}"
            ) from exc
        return _read_pins(database_url, world_id)


def ensure_world_pins(context, catalog: SkillCatalog) -> dict[str, PinnedSkill] | None:
    """返回该世界的 pin 集；世界还没有 pin 时原子快照一次（绝不改写已有 pin）。

    返回 ``None`` 仅表示这不是一个 DB 世界（鸭子上下文、world 行未创建），
    调用方可以走遗留磁盘路径。已存在世界的任何 pin 读取/校验/初始化失败，
    以及数据库不可读，都会抛 ``PinUnavailable``——禁止磁盘回退。
    用 ``probe_world_pins`` 可区分具体状态（empty 与 unreadable 不同）。
    """
    probe = probe_world_pins(context)
    if probe.state == "ready":
        return probe.pins
    if probe.state == "unreadable":
        raise PinUnavailable("Skill pin 数据库不可读")
    if probe.state != "empty":
        return None
    try:
        # A caller may have assembled a synthetic catalog rather than using
        # ``catalog_for``.  Never let that bypass cross-entry dependency or
        # capability validation at the one irreversible pinning boundary.
        catalog = validate_catalog(catalog)
    except CatalogError as exc:
        raise PinUnavailable("Skill catalog 语义非法，拒绝首 pin") from exc
    return _ensure_impl(
        str(context.database_url),
        str(context.world_id),
        context.project_root,
        catalog,
        depth=0,
        module_dir=getattr(context, "module_dir", None),
    )


def pinned_catalog(pins: dict[str, PinnedSkill]) -> SkillCatalog:
    """用冻结快照重建该世界的有效 catalog（行为治理只认它，不认当前磁盘 catalog）。"""
    entries = [pin.entry for pin in sorted(pins.values(), key=lambda pin: pin.order)]
    versions = {pin.catalog_version for pin in pins.values()}
    if len(versions) != 1:
        raise PinUnavailable("冻结 Skill pin 的 catalog_version 不一致")
    try:
        # A frozen sidecar may contain an old absolute ``path`` from a prior
        # runtime release.  It is never used to read the pin's body, so retain
        # it as historical metadata while still enforcing every semantic
        # cross-entry invariant (dependencies, residency and permissions).
        return validate_catalog(
            SkillCatalog(catalog_version=versions.pop(), skills=entries), frozen=True
        )
    except Exception as exc:
        raise PinUnavailable(f"冻结 Skill catalog 语义非法: {type(exc).__name__}") from exc


def copy_world_pins(database_url: str, source_world_id: str, target_world_id: str) -> int:
    """分支继承：把源世界的 pin 复制到目标世界。目标已有 pin / 源无 pin → 0。

    并发安全：目标世界锁内复查 + 唯一约束兜底，race 的 loser 不会半复制。
    """
    if not database_url or not source_world_id or not target_world_id:
        return 0
    if source_world_id == target_world_id:
        return 0
    with _pin_lock(database_url, target_world_id):
        try:
            with session_scope(database_url) as session:
                existing = session.scalar(
                    select(WorldSkillPin.id)
                    .where(WorldSkillPin.world_id == target_world_id)
                    .limit(1)
                )
                if existing is not None:
                    return 0
                rows = session.query(WorldSkillPin).filter_by(world_id=source_world_id).all()
                manifests = (
                    session.query(WorldSkillPinManifest)
                    .filter(WorldSkillPinManifest.pin_id.in_([row.id for row in rows]))
                    .all()
                    if rows
                    else []
                )
                snapshots = {row.pin_id: row.entry_snapshot for row in manifests}
                for row in rows:
                    pin = WorldSkillPin(
                        id=new_id("wsp"),
                        world_id=target_world_id,
                        skill_id=row.skill_id,
                        skill_version=row.skill_version,
                        content_digest=row.content_digest,
                        trust=row.trust,
                        residency=row.residency,
                        content=row.content,
                    )
                    session.add(pin)
                    if row.id in snapshots:
                        session.add(
                            WorldSkillPinManifest(
                                pin_id=pin.id,
                                entry_snapshot=dict(snapshots[row.id] or {}),
                            )
                        )
                session.flush()
                return len(rows)
        except IntegrityError:
            # 并发复制的 loser：目标已有完整 pin 则幂等成功，否则受控失败。
            if _read_pins(database_url, target_world_id):
                return 0
            raise PinUnavailable(
                f"Skill pin 复制并发冲突后目标仍无 pin: {target_world_id}"
            ) from None
        except PinUnavailable:
            raise
        except Exception as exc:
            raise PinUnavailable(
                f"Skill pin 复制失败 {source_world_id} -> {target_world_id}: {type(exc).__name__}"
            ) from exc


def inherit_pins_for_branch(source_context, target_context) -> int:
    """分支继承入口：源已有 pin 直接复制（不读活 catalog）；源无 pin 才首 pin。

    ``CatalogError``（环境无 catalog，如合成测试根）原样上抛，由调用方决定
    跳过；``PinUnavailable``（源 pin 失效 / 复制失败）上抛即分支创建受控
    失败——目标绝不独立按磁盘 pin。
    """
    pins = read_world_pins(source_context)
    if pins is None:
        catalog = catalog_for(source_context)
        ensure_world_pins(source_context, catalog)
    return copy_world_pins(
        str(target_context.database_url),
        str(source_context.world_id),
        str(target_context.world_id),
    )
