"""H3 Skill Catalog tests: manifest / pins freeze / resolver / load_skill / 溯源。

Contract covered (see docs/DEEPSEEK_HARNESS_ADOPTION.md §5.4):
- catalog.json is the single metadata source; invalid catalogs fail closed
- world_skill_pins freeze per world: pin once, never re-pin, branch inherits
- resolver activation is fully deterministic; keywords only diagnose
- load_skill enters through the H0 frozen snapshot + H1 pipeline and only
  serves pinned on_demand entries (no path, no disk fallback for the model)
- skill injections land in context events with source_kind="skill" + digest
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import PROJECT_ROOT
from src.context_events import EVENT_CONTEXT_INJECTION, ContextEventStore
from src.database import (
    Base,
    World,
    WorldSkillPin,
    WorldSkillPinManifest,
    get_engine,
    session_scope,
)
from src.skill_manifest import (
    CatalogError,
    catalog_for,
    load_official_catalog,
    read_skill_content,
    skill_content_digest,
    validate_catalog,
)
from src.skill_pins import (
    copy_world_pins,
    ensure_world_pins,
    pinned_catalog,
    read_world_pins,
)
from src.skill_resolver import keyword_misses, resolve_activations
from src.tool_pipeline import ToolPipeline
from src.tool_policy import MODEL_CALLER, REQUEST_METADATA_KEY, ToolRequestSnapshot
from src.tool_request_authority import issue_model_request
from src.tools import MODEL_TOOLS, TOOL_SCHEMA_BY_NAME, tool_catalog_for_names


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


def seed_world(url: str, world_id: str) -> None:
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id=world_id, module_name="module-a"))


def fake_context(tmp_path: Path, world_id: str) -> SimpleNamespace:
    url = sqlite_url(tmp_path)
    return SimpleNamespace(
        project_root=PROJECT_ROOT,
        module_dir=PROJECT_ROOT / "mod" / "mansion_of_madness",
        module_name="mansion_of_madness",
        module_record=SimpleNamespace(version="1.0.0", capabilities=()),
        world_id=world_id,
        database_url=url,
    )


def tmp_project(tmp_path: Path) -> Path:
    """tmp project_root：复制 skills/ 与 rules/，空模组目录——测试绝不写真实工作区。"""
    import shutil

    root = tmp_path / "proj"
    shutil.copytree(PROJECT_ROOT / "skills", root / "skills")
    (root / "mod" / "mansion_of_madness").mkdir(parents=True)
    (root / "rules").mkdir()
    shutil.copy(PROJECT_ROOT / "rules" / "rule_config.json", root / "rules" / "rule_config.json")
    return root


def tmp_context(tmp_path: Path, world_id: str, project_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project_root=project_root,
        module_dir=project_root / "mod" / "mansion_of_madness",
        module_name="mansion_of_madness",
        module_record=SimpleNamespace(version="1.0.0", capabilities=()),
        world_id=world_id,
        database_url=sqlite_url(tmp_path),
    )


# ---- manifest -------------------------------------------------------------


def test_official_catalog_loads_and_covers_all_skill_files():
    catalog = load_official_catalog(PROJECT_ROOT)
    assert len(catalog.skills) == 15
    assert catalog.skills[0].id == "core.trpg_master"
    assert {"keeper.magic", "investigator.methods"} == {
        entry.id for entry in catalog.on_demand_entries()
    }
    for entry in catalog.skills:
        path = PROJECT_ROOT / entry.path
        assert path.is_file(), entry.path
        assert path.resolve().is_relative_to((PROJECT_ROOT / "skills").resolve())
    # 磁盘上不再有游离的官方 skill 文件（死文件已清理）
    on_disk = {
        str(path.relative_to(PROJECT_ROOT)) for path in (PROJECT_ROOT / "skills").rglob("*.skill")
    }
    assert on_disk == {entry.path for entry in catalog.skills}


def test_catalog_budgets_fit_content():
    """每个 catalog 条目的 max_context_tokens 必须装得下当前正文（防回归）。"""
    from src.lorebook import estimate_text_tokens

    catalog = load_official_catalog(PROJECT_ROOT)
    for entry in catalog.skills:
        content = (PROJECT_ROOT / entry.path).read_text(encoding="utf-8")
        assert estimate_text_tokens(content) <= entry.max_context_tokens, entry.id


def test_opening_subset_matches_four_entry_contract():
    catalog = load_official_catalog(PROJECT_ROOT)
    opening = [entry.id for entry in catalog.core_entries(opening=True)]
    assert opening == [
        "core.trpg_master",
        "core.no_spoiler",
        "keeper.atmosphere",
        "keeper.npc",
    ]


def test_catalog_rejects_duplicate_ids_and_bad_invocable(tmp_path: Path):
    root = tmp_path / "proj"
    skill_dir = root / "skills" / "core"
    skill_dir.mkdir(parents=True)
    (skill_dir / "a.skill").write_text("# A", encoding="utf-8")
    base = {
        "id": "core.a",
        "path": "skills/core/a.skill",
        "version": "1.0.0",
        "trust": "core",
        "residency": "core",
    }
    (root / "skills" / "catalog.json").write_text(
        json.dumps({"catalog_version": 1, "skills": [base, base]}),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError):
        load_official_catalog(root)
    bad_invocable = dict(base, residency="core", model_invocable=True)
    (root / "skills" / "catalog.json").write_text(
        json.dumps({"catalog_version": 1, "skills": [bad_invocable]}),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError):
        load_official_catalog(root)
    # 路径越界必须拒绝
    escaping = dict(base, id="core.escape", path="skills/../../etc/passwd")
    (root / "skills" / "catalog.json").write_text(
        json.dumps({"catalog_version": 1, "skills": [escaping]}),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError):
        load_official_catalog(root)


def test_module_skills_synthesize_bundled_entries():
    context = SimpleNamespace(
        project_root=PROJECT_ROOT,
        module_dir=PROJECT_ROOT / "mod" / "猩红文档",
        module_name="猩红文档",
        module_record=None,
    )
    catalog = catalog_for(context)
    module_entries = [e for e in catalog.skills if e.trust == "bundled-module"]
    assert len(module_entries) == 2
    for entry in module_entries:
        assert entry.residency == "core"
        assert not entry.model_invocable
        # Bundled Skills carry a logical URI only; the trusted runtime module
        # root is the sole authority that can resolve it.
        assert entry.path.startswith("module://skills/")
        # 非 ASCII 模组名也必须产生合法 id
        assert entry.id.startswith("module.m")
        assert read_skill_content(PROJECT_ROOT, entry, module_dir=context.module_dir)
        assert read_skill_content(PROJECT_ROOT, entry) is None


def test_installed_module_skills_use_only_a_trusted_logical_root(tmp_path: Path):
    """User packages never put absolute custom-Skill paths in the catalog."""
    project = tmp_project(tmp_path)
    runtime = tmp_path / "runtime"
    module_dir = runtime / "modules" / "demo" / "1.0.0"
    (module_dir / "skills").mkdir(parents=True)
    (module_dir / "skills" / "guide.skill").write_text("# frozen module rule", encoding="utf-8")
    context = SimpleNamespace(
        project_root=project,
        runtime_root=runtime,
        module_dir=module_dir,
        module_name="demo@1.0.0",
        module_record=SimpleNamespace(
            path=module_dir,
            source="user",
            version="1.0.0",
            capabilities=("custom_skills",),
        ),
    )

    # H3.1：runtime user_root 的本机安装/作者内容是 local-author 信任域。
    entry = next(entry for entry in catalog_for(context).skills if entry.trust == "local-author")
    assert entry.path == "module://skills/guide.skill"
    assert entry.max_context_tokens <= 4_000
    assert str(module_dir) not in entry.path
    assert read_skill_content(project, entry, module_dir=module_dir) == "# frozen module rule"
    assert read_skill_content(project, entry) is None

    # Even an internally constructed absolute-path entry cannot turn the
    # content reader into a generic filesystem primitive.
    absolute = entry.model_copy(update={"path": str(module_dir / "skills" / "guide.skill")})
    assert read_skill_content(project, absolute, module_dir=module_dir) is None

    context.module_record.capabilities = ()
    with pytest.raises(CatalogError, match="custom_skills"):
        catalog_for(context)


def test_local_author_budget_cap_and_v2_pin_roundtrip(tmp_path: Path):
    """local-author 预算硬顶 + catalog_version=2 新 pin 完整往返。"""
    from src.skill_manifest import SkillCatalog, SkillEntry

    # local-author 预算超过 4000 → 拒绝
    entry = SkillEntry(
        id="module.demo.big",
        path="module://skills/big.skill",
        version="1.0.0",
        trust="local-author",
        residency="core",
        max_context_tokens=4_001,
    )
    with pytest.raises(CatalogError, match="local-author"):
        validate_catalog(
            SkillCatalog(catalog_version=2, skills=[entry]),
            known_tool_names=frozenset(),
        )

    # 官方 catalog 已升 v2；新 pin 记录 v2 并完整往返。
    project = tmp_project(tmp_path)
    context = tmp_context(tmp_path, "world-v2", project)
    seed_world(sqlite_url(tmp_path), "world-v2")
    catalog = load_official_catalog(project)
    assert catalog.catalog_version == 2
    pins = ensure_world_pins(context, catalog)
    assert pins
    assert {pin.catalog_version for pin in pins.values()} == {2}
    rebuilt = pinned_catalog(pins)
    assert rebuilt.catalog_version == 2
    assert [skill.id for skill in rebuilt.skills] == [skill.id for skill in catalog.skills]


def test_v1_snapshot_remains_readable_after_v2_upgrade(tmp_path: Path):
    """v1 冻结快照（缺 H3.1 声明字段）在新代码下行为不变。"""
    project = tmp_project(tmp_path)
    context = tmp_context(tmp_path, "world-v1", project)
    seed_world(sqlite_url(tmp_path), "world-v1")
    catalog = load_official_catalog(project)
    pins = ensure_world_pins(context, catalog)

    # 把 sidecar 快照回写成 v1 形态：catalog_version=1 且删掉 H3.1 声明字段，
    # 模拟 0011 时代的冻结世界。
    from src.database import WorldSkillPin

    with session_scope(context.database_url) as session:
        for row in session.query(WorldSkillPin).filter_by(world_id="world-v1"):
            manifest = (
                session.query(WorldSkillPinManifest)
                .filter_by(pin_id=row.id)
                .one()
            )
            snapshot = dict(manifest.entry_snapshot)
            snapshot["catalog_version"] = 1
            for field in ("required_tools", "allowed_tools", "user_invocable"):
                snapshot.pop(field, None)
            manifest.entry_snapshot = snapshot
        session.commit()

    pins = read_world_pins(context)
    assert {pin.catalog_version for pin in pins.values()} == {1}
    rebuilt = pinned_catalog(pins)
    assert rebuilt.catalog_version == 1
    # 声明字段按缺省规范化：v1 世界没有任何工具/玩家调用权限。
    assert all(
        not skill.required_tools and not skill.allowed_tools and not skill.user_invocable
        for skill in rebuilt.skills
    )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda entry: {**entry, "unknown_permission": True},
        lambda entry: {**entry, "activation": {"typo": ["combat_start"]}},
        # user_invocable 仅 on_demand；core 声明仍然拒绝。
        lambda entry: {**entry, "user_invocable": True},
        # 未注册的工具名。
        lambda entry: {**entry, "required_tools": ["not_a_tool"]},
        # 引擎专用工具对模型不可见，声明即拒绝。
        lambda entry: {**entry, "allowed_tools": ["read_file"]},
    ),
)
def test_manifest_rejects_unknown_or_unenforceable_permission_fields(
    tmp_path: Path, mutate
):
    """Typos and unenforceable H3 declarations must fail instead of silently no-oping."""
    root = tmp_path / "proj"
    skill_dir = root / "skills" / "core"
    skill_dir.mkdir(parents=True)
    (skill_dir / "a.skill").write_text("# A", encoding="utf-8")
    base = {
        "id": "core.a",
        "path": "skills/core/a.skill",
        "version": "1.0.0",
        "trust": "core",
        "residency": "core",
    }
    (root / "skills" / "catalog.json").write_text(
        json.dumps({"catalog_version": 1, "skills": [mutate(base)]}),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError):
        load_official_catalog(root)


@pytest.mark.parametrize(
    "mutate",
    (
        # core/deterministic 可声明 required/allowed_tools（模型可见工具名）。
        lambda entry: {**entry, "required_tools": ["skill_check"]},
        lambda entry: {**entry, "allowed_tools": ["skill_check", "dice_roll"]},
    ),
)
def test_manifest_accepts_enforceable_tool_declarations(tmp_path: Path, mutate):
    """H3.1：可执行的工具声明不再是 fail-closed 拒绝对象。"""
    root = tmp_path / "proj"
    skill_dir = root / "skills" / "core"
    skill_dir.mkdir(parents=True)
    (skill_dir / "a.skill").write_text("# A", encoding="utf-8")
    base = {
        "id": "core.a",
        "path": "skills/core/a.skill",
        "version": "1.0.0",
        "trust": "core",
        "residency": "core",
    }
    (root / "skills" / "catalog.json").write_text(
        json.dumps({"catalog_version": 1, "skills": [mutate(base)]}),
        encoding="utf-8",
    )
    catalog = load_official_catalog(root)
    assert catalog.skills[0].id == "core.a"


def test_on_demand_skill_cannot_declare_tool_policy(tmp_path: Path):
    """on_demand 的加载时机由模型决定，没有"激活时并入工具"的语义。"""
    root = tmp_path / "proj"
    skill_dir = root / "skills" / "keeper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "a.skill").write_text("# A", encoding="utf-8")
    base = {
        "id": "keeper.a",
        "path": "skills/keeper/a.skill",
        "version": "1.0.0",
        "trust": "core",
        "residency": "on_demand",
        "model_invocable": True,
    }
    for field in ("required_tools", "allowed_tools"):
        (root / "skills" / "catalog.json").write_text(
            json.dumps({"catalog_version": 1, "skills": [{**base, field: ["skill_check"]}]}),
            encoding="utf-8",
        )
        with pytest.raises(CatalogError, match="on_demand"):
            load_official_catalog(root)


def _policy_engine_stub(tmp_path: Path, world_id: str, catalog_skills: list[dict]):
    """带自定义 catalog 的最小 engine duck：tmp project + DB 世界 + pin。"""
    from src.engine import GameEngine

    project = tmp_project(tmp_path)
    keeper_dir = project / "skills" / "keeper"
    (keeper_dir / "test_policy.skill").write_text("# policy test", encoding="utf-8")
    catalog_path = project / "skills" / "catalog.json"
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    raw["skills"].extend(catalog_skills)
    catalog_path.write_text(json.dumps(raw), encoding="utf-8")

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(World(id=world_id, module_name="mansion_of_madness"))
    engine = GameEngine.__new__(GameEngine)
    engine.context = tmp_context(tmp_path, world_id, project)
    engine.messages = []
    engine._loaded_optional_skills = set()
    engine._skill_catalog_cache = None
    engine._skill_pins_cache = None
    engine._action_resolution = None
    engine._tool_pipeline_ledger = None
    engine._active_turn_id = None
    return engine


def test_request_tool_policy_collects_active_skill_declarations(tmp_path: Path):
    """激活的 deterministic Skill 的 required/allowed_tools 进入请求策略。"""
    from src.skill_activation import request_tool_policy

    engine = _policy_engine_stub(
        tmp_path,
        "world-a",
        [
            {
                "id": "keeper.test_policy",
                "path": "skills/keeper/test_policy.skill",
                "version": "1.0.0",
                "trust": "core",
                "residency": "deterministic",
                "description": "policy test",
                "required_tools": ["combat_status"],
                "allowed_tools": ["dice_roll", "combat_status"],
                "activation": {"combat_active": False},
            }
        ],
    )
    engine.context.world_store = SimpleNamespace(load=lambda: {})

    required, allowed = request_tool_policy(engine)
    assert required == frozenset({"combat_status"})
    # required 永远并入 allowed 上限，不被自己的声明裁掉。
    assert allowed == frozenset({"dice_roll", "combat_status"})

    # 谓词不成立（combat 激活中）时该 Skill 不参与策略。
    engine.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": True}}
    )
    engine._skill_catalog_cache = None
    engine._skill_pins_cache = None
    required, allowed = request_tool_policy(engine)
    assert required == frozenset()
    assert allowed is None


def _prepared_request(host, **overrides):
    from src.model_request import StreamPolicy, prepare_model_request

    kwargs = {
        "policy": StreamPolicy(
            dynamic_tools=False,
            stream_usage=False,
            prompt_profile="full",
            thinking_type=None,
        ),
        "system_overlay": None,
        "system_prompt_override": None,
        "enable_tools": True,
        "temperature": 0.7,
        "messages_override": None,
    }
    kwargs.update(overrides)
    return prepare_model_request(host, "test-model", **kwargs)


def _tool_names(prepared) -> set[str]:
    return {
        str((tool.get("function") or {}).get("name") or "")
        for tool in prepared.request_tools
    }


def test_prepare_request_merges_required_tools_beyond_role_defaults(monkeypatch):
    """required_tools 显式声明优先于 role 默认排除表。"""
    import src.skill_activation as activation

    monkeypatch.setattr(activation, "loadable_skill_allowlist", lambda host: ())
    monkeypatch.setattr(
        activation,
        "request_tool_policy",
        lambda host: (frozenset({"combat_status"}), None),
    )
    host = SimpleNamespace(
        messages=[],
        _tool_request_step=0,
        context=SimpleNamespace(world_id="world-a"),
        _active_turn_id=None,
    )
    prepared = _prepared_request(host)
    # combat_status 默认被 story 目录排除，但 required 声明必须并入。
    assert "combat_status" in _tool_names(prepared)
    # 快照冻结的允许集同步包含（执行期授权不另开通道）。
    assert "combat_status" in prepared.request_snapshot.allowed_tool_names


def test_prepare_request_allowed_tools_intersect_catalog(monkeypatch):
    """allowed_tools 非空声明把当回合模型目录裁到声明并集。"""
    import src.skill_activation as activation

    monkeypatch.setattr(activation, "loadable_skill_allowlist", lambda host: ())
    monkeypatch.setattr(
        activation,
        "request_tool_policy",
        lambda host: (frozenset({"combat_status"}), frozenset({"dice_roll"})),
    )
    host = SimpleNamespace(
        messages=[],
        _tool_request_step=0,
        context=SimpleNamespace(world_id="world-a"),
        _active_turn_id=None,
    )
    prepared = _prepared_request(host)
    # required 并入 allowed 上限后，目录只剩声明并集。
    assert _tool_names(prepared) == {"dice_roll", "combat_status"}
    assert set(prepared.request_snapshot.allowed_tool_names) == {
        "dice_roll",
        "combat_status",
    }


def test_activate_user_skill_loads_user_invocable_pin(tmp_path: Path):
    """玩家 /skill：pin 内 user_invocable 条目受信注入，其余一律拒绝。"""
    from src.skill_activation import activate_user_skill

    engine = _policy_engine_stub(
        tmp_path,
        "world-a",
        [
            {
                "id": "keeper.player_booster",
                "path": "skills/keeper/test_policy.skill",
                "version": "1.0.0",
                "trust": "core",
                "residency": "on_demand",
                "description": "玩家可调用",
                "user_invocable": True,
            },
            {
                "id": "keeper.model_only",
                "path": "skills/keeper/test_policy.skill",
                "version": "1.0.0",
                "trust": "core",
                "residency": "on_demand",
                "description": "仅模型可调用",
                "model_invocable": True,
            },
        ],
    )
    engine.context.world_store = SimpleNamespace(load=lambda: {})

    ok, message = activate_user_skill(engine, "keeper.player_booster")
    assert ok and "已加载技能" in message
    assert len(engine.messages) == 1
    content = str(engine.messages[0]["content"])
    assert "应玩家请求加载" in content
    assert "[skill-pin id=keeper.player_booster " in content
    assert "# policy test" in content

    # 重复调用幂等：不重复注入。
    ok, message = activate_user_skill(engine, "keeper.player_booster")
    assert ok and "已在上下文中" in message
    assert len(engine.messages) == 1

    # 非 user_invocable / 未知 id 一律拒绝，且不改变上下文。
    ok, message = activate_user_skill(engine, "keeper.model_only")
    assert not ok and "不接受玩家调用" in message
    ok, message = activate_user_skill(engine, "keeper.unknown")
    assert not ok and "没有可调用的技能" in message
    assert len(engine.messages) == 1


# ---- pins -----------------------------------------------------------------


def test_world_pins_freeze_content_and_never_repin(tmp_path: Path):
    project = tmp_project(tmp_path)
    seed_world(sqlite_url(tmp_path), "world-a")
    context = tmp_context(tmp_path, "world-a", project)
    catalog = catalog_for(context)
    pins = ensure_world_pins(context, catalog)
    assert pins is not None and len(pins) == len(catalog.skills)
    combat = pins["keeper.combat"]
    assert combat.digest == skill_content_digest(combat.content)
    assert combat.entry.activation.combat_active is True  # manifest 元数据已冻结

    # 磁盘文件改动后，世界仍读到冻结快照
    skill_file = project / "skills" / "keeper" / "keeper_combat.skill"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8") + "\n\n热更新不应生效。\n",
        encoding="utf-8",
    )
    again = ensure_world_pins(context, catalog)
    assert again is not None
    assert again["keeper.combat"].content == combat.content
    assert "热更新不应生效" not in again["keeper.combat"].content


def test_world_without_row_fails_soft(tmp_path: Path):
    Base.metadata.create_all(get_engine(sqlite_url(tmp_path)))
    context = fake_context(tmp_path, "ghost-world")
    catalog = catalog_for(context)
    assert ensure_world_pins(context, catalog) is None


def test_branch_inherits_source_pins(tmp_path: Path):
    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    seed_world(url, "world-b")
    source = fake_context(tmp_path, "world-a")
    catalog = catalog_for(source)
    source_pins = ensure_world_pins(source, catalog)
    assert source_pins is not None

    copied = copy_world_pins(url, "world-a", "world-b")
    assert copied == len(source_pins)
    target_pins = ensure_world_pins(fake_context(tmp_path, "world-b"), catalog)
    assert target_pins is not None
    assert target_pins == source_pins
    # 二次复制是 no-op
    assert copy_world_pins(url, "world-a", "world-b") == 0


# ---- resolver -------------------------------------------------------------


def _catalog():
    return load_official_catalog(PROJECT_ROOT)


def test_resolver_tool_and_combat_predicates():
    catalog = _catalog()
    calm_world = {"combat_state": {"active": False}, "pc": {"san": 60}}
    assert resolve_activations(catalog, world=calm_world) == []

    by_tool = resolve_activations(catalog, world=calm_world, tool_name="combat_start")
    assert [e.id for e in by_tool] == ["keeper.combat"]

    in_combat = {"combat_state": {"active": True}, "pc": {"san": 60}}
    assert [e.id for e in resolve_activations(catalog, world=in_combat)] == ["keeper.combat"]

    san_world = {"combat_state": {"active": False}, "pc": {"san": 35}}
    assert [e.id for e in resolve_activations(catalog, world=san_world)] == ["keeper.psychology"]

    assert [
        e.id for e in resolve_activations(catalog, world=calm_world, tool_name="create_character")
    ] == ["investigator.creation"]
    assert [
        e.id for e in resolve_activations(catalog, world=calm_world, tool_name="skill_check")
    ] == ["investigator.skills"]


def test_resolver_phase_scene_capability_ruleset_predicates():
    catalog = _catalog()
    world = {"combat_state": {}, "pc": {"san": 80}, "current_scene": {"id": "hall"}}
    # 官方 catalog 没有 phase/scene/capability/ruleset 谓词——构造合成条目验证语义
    from src.skill_manifest import SkillEntry

    synthetic = SkillEntry(
        id="keeper.synthetic",
        path="skills/keeper/keeper_core.skill",
        version="1.0.0",
        trust="core",
        residency="deterministic",
        activation={
            "phases": ["contact"],
            "scenes": ["hall"],
            "scene_capabilities": ["occult"],
            "module_capabilities": ["custom_skills"],
            "rulesets": ["COC 第七版"],
        },
    )
    catalog2 = catalog.model_copy(update={"skills": [*catalog.skills, synthetic]})
    phase = SimpleNamespace(value="contact")
    resolution = SimpleNamespace(phase=phase)
    hit = resolve_activations(catalog2, world=world, action_resolution=resolution)
    assert synthetic in hit
    hit = resolve_activations(catalog2, world=world, module_capabilities={"custom_skills"})
    assert synthetic in hit
    hit = resolve_activations(catalog2, world=world, ruleset="COC 第七版")
    assert synthetic in hit
    capability_world = {
        **world,
        "current_scene": {"id": "cellar", "capabilities": ["occult"]},
    }
    hit = resolve_activations(catalog2, world=capability_world)
    assert synthetic in hit
    miss = resolve_activations(catalog2, world={**world, "current_scene": {"id": " cellar "}})
    assert synthetic not in miss


def test_resolver_respects_available_ids_and_keywords_only_diagnose():
    catalog = _catalog()
    world = {"combat_state": {"active": True}, "pc": {"san": 10}}
    limited = resolve_activations(catalog, world=world, available_ids={"keeper.combat"})
    assert [e.id for e in limited] == ["keeper.combat"]

    # 关键词命中但谓词未命中 → 只出现在诊断里
    calm = {"combat_state": {"active": False}, "pc": {"san": 90}}
    activated = resolve_activations(catalog, world=calm)
    missed = keyword_misses(catalog, "我拔枪瞄准他", {e.id for e in activated})
    assert [e.id for e in missed] == ["keeper.combat"]
    assert activated == []


def test_deterministic_dependencies_are_validated_and_loaded_first():
    """Dependency closure is deterministic and never relies on prompt ordering luck."""
    from src.skill_manifest import SkillCatalog, SkillEntry, validate_catalog

    prerequisite = SkillEntry(
        id="keeper.prerequisite",
        path="skills/keeper/keeper_core.skill",
        version="1.0.0",
        trust="core",
        residency="deterministic",
        activation={"tools": ["prepare_ritual"]},
    )
    target = SkillEntry(
        id="keeper.ritual",
        path="skills/keeper/keeper_core.skill",
        version="1.0.0",
        trust="core",
        residency="deterministic",
        activation={"tools": ["cast_ritual"]},
        dependencies=["keeper.prerequisite"],
    )
    catalog = validate_catalog(SkillCatalog(catalog_version=1, skills=[target, prerequisite]))
    activated = resolve_activations(catalog, world={}, tool_name="cast_ritual")
    assert [entry.id for entry in activated] == ["keeper.prerequisite", "keeper.ritual"]

    core = SkillEntry(
        id="core.always",
        path="skills/core/trpg_master.skill",
        version="1.0.0",
        trust="core",
        residency="core",
    )
    with pytest.raises(CatalogError, match="deterministic"):
        validate_catalog(
            SkillCatalog(catalog_version=1, skills=[core, target.model_copy(update={"dependencies": ["core.always"]})])
        )


# ---- load_skill 工具（H0 快照 + H1 管线）---------------------------------


def _engine_stub(tmp_path: Path, world_id: str, *, seed: bool = True):
    """Minimal engine duck for ToolPipeline: context + ledger + execute."""
    from src.engine import GameEngine

    if seed:
        seed_world(sqlite_url(tmp_path), world_id)
    engine = GameEngine.__new__(GameEngine)
    engine.context = fake_context(tmp_path, world_id)
    engine.messages = []
    engine._loaded_optional_skills = set()
    engine._skill_catalog_cache = None
    engine._skill_pins_cache = None
    engine._action_resolution = None
    engine._tool_pipeline_ledger = None
    engine._active_turn_id = None
    return engine


def _authorized_call(
    engine,
    name: str,
    args: dict,
    allowed: tuple[str, ...],
    skill_allowlist: tuple[tuple[str, str], ...] = (),
    call_id: str | None = None,
) -> dict:
    catalog = tool_catalog_for_names(allowed, skill_allowlist=skill_allowlist)
    snapshot = ToolRequestSnapshot.create(
        step=1,
        profile="story:full",
        caller=MODEL_CALLER,
        tools=catalog,
        world_id=str(engine.context.world_id),
        turn_id=getattr(engine, "_active_turn_id", None),
        skill_allowlist=skill_allowlist,
    )
    call = {
        "id": call_id or f"call_{name}_{json.dumps(args, sort_keys=True)}",
        "function": {"name": name, "arguments": json.dumps(args)},
        REQUEST_METADATA_KEY: snapshot.to_dict(),
    }
    issue_model_request(engine, snapshot, catalog)
    return call


def _frozen_skills(engine) -> tuple[tuple[str, str], ...]:
    from src.skill_activation import loadable_skill_allowlist

    return loadable_skill_allowlist(engine)


def test_load_skill_in_frozen_model_catalog():
    names = {tool["function"]["name"] for tool in MODEL_TOOLS}
    assert "load_skill" not in names
    assert "load_skill" in TOOL_SCHEMA_BY_NAME
    assert "read_file" not in names  # 旧边界不破


def test_load_skill_pipeline_denies_unlisted_tool(tmp_path: Path):
    engine = _engine_stub(tmp_path, "world-a")
    pipeline = ToolPipeline(engine, timeout_ms=5000)
    # snapshot 不含 load_skill → H1 授权拒绝，handler 根本不会执行
    outcome = pipeline.execute(
        _authorized_call(engine, "load_skill", {"skill_id": "keeper.magic"}, ("skill_check",))
    )
    assert outcome.status == "denied"


def test_load_skill_serves_only_pinned_on_demand(tmp_path: Path):
    engine = _engine_stub(tmp_path, "world-a")
    pipeline = ToolPipeline(engine, timeout_ms=5000)
    allowed = ("load_skill",)
    frozen = _frozen_skills(engine)
    assert {skill_id for skill_id, _digest in frozen} == {
        "keeper.magic",
        "investigator.methods",
    }

    def call(skill_id: str):
        return pipeline.execute(
            _authorized_call(
                engine, "load_skill", {"skill_id": skill_id}, allowed, skill_allowlist=frozen
            )
        )

    # 冻结集合之外的 id（core / deterministic / 未知 / 路径穿越）在 H1 授权
    # 层就被拒绝，handler 不会执行
    for denied_id in ("core.no_spoiler", "keeper.combat", "keeper.unknown", "../x"):
        outcome = call(denied_id)
        assert outcome.status == "denied"
        # Frozen provider enum rejects an unknown id before handler dispatch.
        assert outcome.error_code == "invalid_arguments"
        assert "keeper" not in outcome.output or "invalid_arguments" in outcome.output

    outcome = call("keeper.magic")
    payload = json.loads(outcome.output)
    assert payload["ok"] is True
    assert payload["skill_id"] == "keeper.magic"
    assert payload["digest"].startswith("sha256:")
    assert "魔法" in payload["content"]
    assert "/" not in payload["digest"]

    # 跨调用幂等：ledger 语义去重 → reused
    again = call("keeper.magic")
    assert again.status == "reused"


def test_load_skill_snapshot_freeze_governs_not_live_catalog(tmp_path: Path):
    """快照里没有的 id 即使 handler 侧可加载也必须被拒绝（冻结优先于现状）。"""
    engine = _engine_stub(tmp_path, "world-a")
    pipeline = ToolPipeline(engine, timeout_ms=5000)
    # keeper.magic 在 pin 里且 handler 会放行，但本快照冻结集为空
    outcome = pipeline.execute(
        _authorized_call(engine, "load_skill", {"skill_id": "keeper.magic"}, ("load_skill",))
    )
    assert outcome.status == "denied"
    # No loadable Skill means the server did not issue load_skill at all.
    assert outcome.error_code == "model_tool_forbidden"


def test_load_skill_dsml_shape_call_denied_for_non_frozen_id(tmp_path: Path):
    """DSML 解析出的调用（无 type/合成 id）与 native 走同一授权语义。"""
    engine = _engine_stub(tmp_path, "world-a")
    pipeline = ToolPipeline(engine, timeout_ms=5000)
    dsml_style_call = _authorized_call(
        engine, "load_skill", {"skill_id": "core.no_spoiler"}, ("load_skill",), call_id=""
    )
    dsml_style_call.pop("id")  # DSML 合成的调用没有 provider call id
    outcome = pipeline.execute(dsml_style_call)
    assert outcome.status == "denied"
    assert outcome.error_code == "model_tool_forbidden"


def _execution_window(engine, skill_allowlist=None):
    """模拟一次已签发模型请求的执行窗口（H1 执行期证据）。"""
    from src.skill_activation import loadable_skill_allowlist
    from src.tool_request_authority import execution_snapshot

    if skill_allowlist is None:
        skill_allowlist = loadable_skill_allowlist(engine)
    snapshot = ToolRequestSnapshot.create(
        step=1,
        profile="story:full",
        caller=MODEL_CALLER,
        tools=tool_catalog_for_names(("load_skill",), skill_allowlist=skill_allowlist),
        world_id=str(engine.context.world_id),
        turn_id=None,
        skill_allowlist=skill_allowlist,
    )
    return execution_snapshot(engine, snapshot)


def test_load_skill_without_pins_fails_closed(tmp_path: Path):
    # 无 world 行 → 无 pin → 模型路径拒绝（不做磁盘回退）
    Base.metadata.create_all(get_engine(sqlite_url(tmp_path)))
    from src.engine import GameEngine

    engine = GameEngine.__new__(GameEngine)
    engine.context = fake_context(tmp_path, "ghost")
    engine.messages = []
    engine._loaded_optional_skills = set()
    engine._skill_catalog_cache = None
    engine._skill_pins_cache = None
    from src.skill_activation import execute_load_skill

    with _execution_window(engine, skill_allowlist=()):
        result = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert result["ok"] is False


def test_load_skill_requires_issued_snapshot_and_exact_digest(tmp_path: Path):
    """无执行期快照、或冻结 digest 与 pin 不一致，都必须拒绝。"""
    from src.skill_activation import execute_load_skill, loadable_skill_allowlist

    engine = _engine_stub(tmp_path, "world-a")
    engine.context.world_store = SimpleNamespace(load=lambda: {})

    # 无执行窗口 → 拒绝
    assert json.loads(execute_load_skill(engine, "keeper.magic"))["ok"] is False

    allowlist = loadable_skill_allowlist(engine)
    assert dict(allowlist).get("keeper.magic", "").startswith("sha256:")

    # digest 被篡改的冻结集合 → 拒绝
    tampered = tuple(
        (sid, "sha256:" + "0" * 64) if sid == "keeper.magic" else (sid, digest)
        for sid, digest in allowlist
    )
    with _execution_window(engine, skill_allowlist=tampered):
        result = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert result["ok"] is False
    assert "keeper.magic" not in engine._loaded_optional_skills

    # 精确匹配 → 放行
    with _execution_window(engine, skill_allowlist=allowlist):
        result = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert result["ok"] is True


# ---- 注入与溯源 ------------------------------------------------------------


def test_deterministic_injection_uses_pin_and_records_provenance(tmp_path: Path):
    engine = _engine_stub(tmp_path, "world-a")
    engine.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": True}, "pc": {"san": 80}}
    )

    engine._maybe_hint_optional_skill("combat_start")

    assert "keeper.combat" in engine._loaded_optional_skills
    assert len(engine.messages) == 1
    content = engine.messages[0]["content"]
    assert "keeper.combat" in content
    from src.skill_activation import skill_catalog as engine_skill_catalog

    pin = ensure_world_pins(engine.context, engine_skill_catalog(engine))["keeper.combat"]
    assert pin.content in content

    # 重复触发不重复注入
    engine._maybe_hint_optional_skill("combat_action")
    assert len(engine.messages) == 1

    # 溯源：注入事件落 context events 时带 source_kind=skill + digest
    from src.context_shadow import note_skill_injection

    store = ContextEventStore(engine.context.database_url)
    store.ensure_session("world-a")
    session = store.session_for_world("world-a")
    message = engine.messages[0]
    note_skill_injection(
        engine,
        message=message,
        skill_id="keeper.combat",
        digest=pin.digest,
    )
    status, sequences = store.sync_messages(
        session["id"],
        engine.messages,
        provenance={
            # 与 context_shadow 相同的消息摘要键
            __import__("src.context_events", fromlist=["payload_digest"]).payload_digest(
                store._normalize_messages([dict(message)])[0]
            ): {
                "source_kind": "skill",
                "source_id": "keeper.combat",
                "source_version": pin.digest,
            }
        },
    )
    assert status == "appended"
    with session_scope(engine.context.database_url) as db:
        from src.database import ModelContextEvent

        event = db.query(ModelContextEvent).filter_by(session_id=session["id"]).one()
        assert event.event_type == EVENT_CONTEXT_INJECTION
        assert event.source_kind == "skill"
        assert event.source_id == "keeper.combat"
        assert event.source_version == pin.digest


def test_keyword_hit_does_not_inject(tmp_path: Path):
    engine = _engine_stub(tmp_path, "world-a")
    engine.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": False}, "pc": {"san": 90}}
    )
    logged = []
    import src.skill_activation as skill_activation_module

    original = skill_activation_module.log_game
    skill_activation_module.log_game = logged.append
    try:
        engine._detect_content_skill_hint("我拔枪瞄准管家")
    finally:
        skill_activation_module.log_game = original

    assert engine.messages == []
    assert engine._loaded_optional_skills == set()
    assert any("keeper.combat" in entry for entry in logged)


def test_deterministic_skill_refresh_uses_current_surface_not_lifetime_set(tmp_path: Path):
    """A compacted-away rule returns before a same-session retry, without duplicates."""
    from src.skill_activation import refresh_deterministic_skills

    engine = _engine_stub(tmp_path, "world-a")
    engine.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": True}, "pc": {"san": 90}}
    )
    assert refresh_deterministic_skills(engine) == 1
    initial = [
        message
        for message in engine.messages
        if "[skill-pin id=keeper.combat " in str(message.get("content") or "")
    ]
    assert len(initial) == 1
    assert "keeper.combat" in engine._loaded_optional_skills

    # Stand in for a replace checkpoint that removed an old control message.
    # The in-memory lifetime set intentionally remains populated.
    engine.messages = [{"role": "system", "content": "compacted surface"}]
    assert "keeper.combat" in engine._loaded_optional_skills
    assert refresh_deterministic_skills(engine) == 1
    restored = [
        message
        for message in engine.messages
        if "[skill-pin id=keeper.combat " in str(message.get("content") or "")
    ]
    assert len(restored) == 1
    assert refresh_deterministic_skills(engine) == 0
    assert len(
        [
            message
            for message in engine.messages
            if "[skill-pin id=keeper.combat " in str(message.get("content") or "")
        ]
    ) == 1


def test_tool_triggered_skill_restores_after_same_turn_compaction(tmp_path: Path):
    """A transient tool predicate survives H2 surface replacement for its live turn."""
    from src.skill_activation import refresh_deterministic_skills

    engine = _engine_stub(tmp_path, "world-a")
    engine.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": False}, "pc": {"san": 90}}
    )
    engine._active_turn_id = "turn-use-item"
    engine._maybe_hint_optional_skill("use_item")
    assert "keeper.items" in engine._loaded_optional_skills
    assert any("[skill-pin id=keeper.items " in item["content"] for item in engine.messages)

    # A capacity preflight/overflow compaction may remove the engine control
    # after the tool handler completed.  World state alone cannot rederive
    # this tools-only predicate, so the per-turn frozen continuation restores
    # it before the retry request is built.
    engine.messages = [{"role": "system", "content": "compacted surface"}]
    assert refresh_deterministic_skills(engine) == 1
    assert any("[skill-pin id=keeper.items " in item["content"] for item in engine.messages)

    # The continuation is deliberately scoped to the durable turn id; it
    # cannot make a tool-only rule leak into the next player action.
    engine._active_turn_id = "turn-next"
    engine.messages = [{"role": "system", "content": "next turn surface"}]
    assert refresh_deterministic_skills(engine) == 0


def test_player_control_lookalike_cannot_suppress_skill_refresh(tmp_path: Path):
    """Only the engine-registered current control object can count as loaded."""
    from src.skill_activation import refresh_deterministic_skills

    engine = _engine_stub(tmp_path, "world-a")
    engine.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": True}, "pc": {"san": 90}}
    )
    assert refresh_deterministic_skills(engine) == 1
    original = engine.messages[-1]
    # A user can copy text, including a known digest, but never the trusted
    # in-memory control message identity.
    engine.messages = [{"role": "user", "content": original["content"]}]
    assert refresh_deterministic_skills(engine) == 1
    assert engine.messages[-1] is not original
    assert engine.messages[-1]["content"].startswith("[引擎控制指令｜非玩家发言]")


# ---- fail-closed / 冻结快照 / 分支继承（安全点 1-3）------------------------


def test_partial_existing_pins_never_topped_up(tmp_path: Path):
    """世界已有任何 pin 时绝不补写、不改写（不热更新）。"""
    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    legacy_content = "legacy content"
    with session_scope(url) as session:
        session.add(
            WorldSkillPin(
                id="wsp_legacy1",
                world_id="world-a",
                skill_id="keeper.combat",
                skill_version="0.9.0",
                content_digest=skill_content_digest(legacy_content),
                trust="core",
                residency="deterministic",
                content=legacy_content,
            )
        )
    context = fake_context(tmp_path, "world-a")
    pins = ensure_world_pins(context, catalog_for(context))
    assert pins is not None
    assert set(pins) == {"keeper.combat"}
    assert pins["keeper.combat"].content == legacy_content


def test_corrupted_pin_digest_fails_closed(tmp_path: Path):
    """pin 内容被篡改（digest 不匹配）→ PinUnavailable，不信任、不回退。"""
    from src.skill_pins import PinUnavailable

    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    context = fake_context(tmp_path, "world-a")
    catalog = catalog_for(context)
    assert ensure_world_pins(context, catalog)
    with session_scope(url) as session:
        row = (
            session.query(WorldSkillPin)
            .filter_by(world_id="world-a", skill_id="keeper.combat")
            .one()
        )
        row.content = row.content + "tampered"

    with pytest.raises(PinUnavailable):
        ensure_world_pins(context, catalog)


def test_catalog_metadata_change_does_not_affect_pinned_world(tmp_path: Path):
    """改 catalog 元数据/内容后，已 pin 世界的 prompt/resolver/loader 行为不变。"""
    project = tmp_project(tmp_path)
    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    seed_world(url, "world-b")
    context_a = tmp_context(tmp_path, "world-a", project)
    context_b = tmp_context(tmp_path, "world-b", project)

    from src.persistence import load_system_prompt
    from src.skill_activation import (
        execute_load_skill,
        loadable_skill_allowlist,
        resolve_for_engine,
    )

    engine_a = _engine_stub(tmp_path, "world-a", seed=False)
    engine_a.context = context_a
    engine_a.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": True}, "pc": {"san": 80}}
    )
    prompt_before = load_system_prompt(context_a)
    resolver_before = [e.id for e in resolve_for_engine(engine_a)]
    allowlist_before = loadable_skill_allowlist(engine_a)
    with _execution_window(engine_a):
        loader_before = json.loads(execute_load_skill(engine_a, "keeper.magic"))

    # 改 catalog 元数据：keeper.magic 不再模型可调、combat 去掉 combat_active
    # 谓词、改 description；改 skill 文件内容。
    catalog_path = project / "skills" / "catalog.json"
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    for skill in raw["skills"]:
        if skill["id"] == "keeper.magic":
            skill["model_invocable"] = False
            skill["description"] = "已改写的描述"
        if skill["id"] == "keeper.combat":
            skill["activation"]["combat_active"] = None
            skill["activation"]["tools"] = ["combat_start"]
        if skill["id"] == "core.no_spoiler":
            skill["opening"] = False
    catalog_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    combat_file = project / "skills" / "keeper" / "keeper_combat.skill"
    combat_file.write_text(
        combat_file.read_text(encoding="utf-8") + "\n\n新规则不应生效。\n",
        encoding="utf-8",
    )

    # 已 pin 世界：全部行为冻结
    engine_a2 = _engine_stub(tmp_path, "world-a", seed=False)
    engine_a2.context = context_a
    engine_a2.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": True}, "pc": {"san": 80}}
    )
    assert load_system_prompt(context_a) == prompt_before
    assert [e.id for e in resolve_for_engine(engine_a2)] == resolver_before
    assert "keeper.combat" in resolver_before  # combat_active 谓词仍冻结生效
    assert loadable_skill_allowlist(engine_a2) == allowlist_before
    assert dict(allowlist_before)["keeper.magic"].startswith("sha256:")
    with _execution_window(engine_a2):
        loader_after = json.loads(execute_load_skill(engine_a2, "keeper.magic"))
    assert loader_after["ok"] == loader_before["ok"] == True  # noqa: E712
    assert loader_after["content"] == loader_before["content"]
    opening_prompt = load_system_prompt(context_a, profile="opening")
    assert "防剧透硬约束" in opening_prompt  # 冻结的 opening=true 仍生效

    # 对照：新世界按新 catalog 行为（keeper.magic 不再可加载）
    engine_b = _engine_stub(tmp_path, "world-b", seed=False)
    engine_b.context = context_b
    new_allowlist = dict(loadable_skill_allowlist(engine_b))
    assert "keeper.magic" not in new_allowlist
    assert "investigator.methods" in new_allowlist


def test_pinned_world_immune_to_catalog_deletion(tmp_path: Path):
    """catalog.json 整个丢失：已 pin 世界 prompt/resolver 仍由冻结快照治理。"""
    project = tmp_project(tmp_path)
    seed_world(sqlite_url(tmp_path), "world-a")
    context = tmp_context(tmp_path, "world-a", project)

    from src.persistence import load_system_prompt
    from src.skill_activation import resolve_for_engine

    prompt_before = load_system_prompt(context)
    (project / "skills" / "catalog.json").unlink()

    engine = _engine_stub(tmp_path, "world-a", seed=False)
    engine.context = context
    engine.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": True}, "pc": {"san": 80}}
    )
    assert load_system_prompt(context) == prompt_before
    assert [e.id for e in resolve_for_engine(engine)] == ["keeper.combat"]


def test_concurrent_first_pin_is_atomic(tmp_path: Path):
    """并发首 pin：唯一约束兜底，所有线程得到同一份快照，无半插入。"""
    import threading

    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    context = fake_context(tmp_path, "world-a")
    catalog = catalog_for(context)
    results, errors = [], []

    def worker():
        try:
            results.append(ensure_world_pins(context, catalog))
        except Exception as exc:  # noqa: BLE001 - 收集后统一断言
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 4
    assert all(result == results[0] for result in results)
    with session_scope(url) as session:
        count = session.query(WorldSkillPin).filter_by(world_id="world-a").count()
    assert count == len(catalog.skills)


def test_existing_world_pin_failure_fails_closed(tmp_path: Path):
    """已存在世界的 pin 读取失败：禁止磁盘回退，全链路受控失败。"""
    import sqlalchemy as sa

    from src.database import get_engine
    from src.persistence import load_system_prompt
    from src.skill_pins import PinUnavailable

    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    context = fake_context(tmp_path, "world-a")
    catalog = catalog_for(context)
    assert ensure_world_pins(context, catalog)

    engine = _engine_stub(tmp_path, "world-a", seed=False)
    engine.context.world_store = SimpleNamespace(
        load=lambda: {"combat_state": {"active": True}, "pc": {"san": 80}}
    )

    with get_engine(url).begin() as conn:
        conn.execute(sa.text("DROP TABLE world_skill_pins"))

    with pytest.raises(PinUnavailable):
        ensure_world_pins(context, catalog)
    with pytest.raises(PinUnavailable):
        load_system_prompt(context)

    # 确定性激活与模型 loader 同样 fail-closed：不注入、不加载、不漂移
    engine._maybe_hint_optional_skill("combat_start")
    assert engine.messages == []
    assert engine._loaded_optional_skills == set()
    from src.skill_activation import execute_load_skill

    with _execution_window(engine, skill_allowlist=()):
        result = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert result["ok"] is False


def test_zero_pin_db_world_with_broken_catalog_never_falls_back_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A persisted but not-yet-pinned world has no mutable-disk escape hatch."""
    from src.persistence import load_system_prompt
    from src.skill_pins import PinUnavailable

    project = tmp_project(tmp_path)
    seed_world(sqlite_url(tmp_path), "world-a")
    context = tmp_context(tmp_path, "world-a", project)
    # The fallback helper is deliberately booby-trapped: the controlled error
    # must happen before any legacy disk reader gets a chance to run.
    monkeypatch.setattr(
        "src.persistence._read_disk_skill",
        lambda _path: pytest.fail("zero-pin DB world read a live disk Skill"),
    )
    (project / "skills" / "catalog.json").unlink()
    with pytest.raises(PinUnavailable, match="尚未冻结"):
        load_system_prompt(context)
    with session_scope(context.database_url) as session:
        assert session.query(WorldSkillPin).filter_by(world_id="world-a").count() == 0


def test_legacy_content_only_pins_stay_playable_without_current_catalog(tmp_path: Path):
    """Pre-0011 rows become bounded core text solely from frozen content."""
    from src.persistence import load_system_prompt
    from src.skill_pins import pinned_catalog, read_world_pins

    project = tmp_project(tmp_path)
    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    legacy_content = "# Legacy keeper rule\nUse only this frozen rule text."
    with session_scope(url) as session:
        session.add(
            WorldSkillPin(
                id="wsp_legacy_on_demand",
                world_id="world-a",
                skill_id="keeper.magic",
                skill_version="0.9.0",
                content_digest=skill_content_digest(legacy_content),
                trust="core",
                residency="on_demand",
                content=legacy_content,
            )
        )
    context = tmp_context(tmp_path, "world-a", project)
    (project / "skills" / "catalog.json").unlink()

    pins = read_world_pins(context)
    assert pins is not None
    pin = pins["keeper.magic"]
    assert pin.entry.residency == "core"
    assert pin.entry.model_invocable is False
    assert pin.entry.activation.model_dump(exclude_defaults=True) == {}
    assert [entry.id for entry in pinned_catalog(pins).core_entries()] == ["keeper.magic"]
    assert legacy_content in load_system_prompt(context)


def test_branch_world_inherits_parent_pins_not_drifted_disk(tmp_path: Path):
    """分支零 pin 时从父世界复制冻结快照——即使磁盘之后已改，也绝不独立 pin。"""
    project = tmp_project(tmp_path)
    url = sqlite_url(tmp_path)
    seed_world(url, "parent")
    parent_context = tmp_context(tmp_path, "parent", project)
    catalog = catalog_for(parent_context)
    parent_pins = ensure_world_pins(parent_context, catalog)
    assert parent_pins is not None

    skill_file = project / "skills" / "keeper" / "keeper_combat.skill"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8") + "\n\n漂移标记。\n", encoding="utf-8"
    )
    with session_scope(url) as session:
        session.add(
            World(
                id="child",
                module_name="module-a",
                metadata_json={"branch": {"parent_world_id": "parent"}},
            )
        )
    child_pins = ensure_world_pins(tmp_context(tmp_path, "child", project), catalog)

    assert child_pins is not None
    assert child_pins == parent_pins
    assert "漂移标记" not in child_pins["keeper.combat"].content


def test_request_snapshot_freezes_loadable_skill_ids_and_digests(tmp_path: Path):
    """H0：请求构造时把可加载 skill id+digest 冻结进 ToolRequestSnapshot。"""
    engine = _engine_stub(tmp_path, "world-a")
    engine.messages = [{"role": "system", "content": "sys"}]

    from src.model_request import StreamPolicy, prepare_model_request

    prepared = prepare_model_request(
        engine,
        "test-model",
        policy=StreamPolicy(
            dynamic_tools=True,
            stream_usage=False,
            prompt_profile="full",
            thinking_type=None,
        ),
        system_overlay=None,
        system_prompt_override=None,
        enable_tools=True,
        temperature=0.8,
        messages_override=None,
    )
    allowlist = dict(prepared.request_snapshot.skill_allowlist)
    assert set(allowlist) == {"keeper.magic", "investigator.methods"}
    pins = ensure_world_pins(engine.context, catalog_for(engine.context))
    assert pins is not None
    assert allowlist["keeper.magic"] == pins["keeper.magic"].digest

    # 快照序列化往返后冻结集合保持（attach_request_snapshot → from_dict）
    restored = ToolRequestSnapshot.from_dict(prepared.request_snapshot.to_dict())
    assert restored.skill_allowlist == prepared.request_snapshot.skill_allowlist


# ---- pin 加固：probe / 完整性 / 继承不读活 catalog / loader 预算与溯源 ------


def test_probe_distinguishes_db_states(tmp_path: Path):
    from src.skill_pins import probe_world_pins

    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    # 无 world_id/database_url → no_db
    assert probe_world_pins(SimpleNamespace()).state == "no_db"
    # 有库无 world 行 → no_world
    assert probe_world_pins(fake_context(tmp_path, "ghost")).state == "no_world"
    # world 存在零 pin → empty；pin 后 → ready
    seed_world(url, "world-a")
    context = fake_context(tmp_path, "world-a")
    assert probe_world_pins(context).state == "empty"
    ensure_world_pins(context, catalog_for(context))
    probe = probe_world_pins(context)
    assert probe.state == "ready" and probe.pins


def test_unreadable_pin_database_never_falls_back_to_live_catalog(tmp_path: Path):
    """存在 DB 配置但表不可读不是 legacy：必须受控失败，不能热读磁盘。"""
    from src.skill_pins import PinUnavailable, probe_world_pins, read_world_pins

    # 空 SQLite 文件有 URL，但没有任何表；这与真正的 duck/no-world context
    # 不同。若把它当作 ``None`` 回退磁盘，已运行世界会绕过 pin 冻结。
    context = fake_context(tmp_path, "world-a")
    assert probe_world_pins(context).state == "unreadable"
    with pytest.raises(PinUnavailable):
        read_world_pins(context)
    with pytest.raises(PinUnavailable):
        ensure_world_pins(context, catalog_for(context))


def test_pin_set_integrity_detects_missing_and_mismatched_rows(tmp_path: Path):
    from src.skill_pins import PinUnavailable

    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    context = fake_context(tmp_path, "world-a")
    catalog = catalog_for(context)
    assert ensure_world_pins(context, catalog)

    # 1) 删一行 sidecar → 混合集（部分有快照）→ fail-closed

    with session_scope(url) as session:
        row = session.query(WorldSkillPin).filter_by(world_id="world-a").first()
        manifest = session.get(WorldSkillPinManifest, row.id)
        session.delete(manifest)
    with pytest.raises(PinUnavailable):
        ensure_world_pins(context, catalog)

    # 2) 快照 version 与列不一致 → fail-closed
    (tmp_path / "b").mkdir()
    url2 = sqlite_url(tmp_path / "b")
    seed_world(url2, "world-b")
    context_b = tmp_context(tmp_path / "b", "world-b", PROJECT_ROOT)
    context_b.database_url = url2
    assert ensure_world_pins(context_b, catalog_for(context_b))
    with session_scope(url2) as session:
        row = (
            session.query(WorldSkillPin)
            .filter_by(world_id="world-b", skill_id="keeper.combat")
            .one()
        )
        manifest = session.get(WorldSkillPinManifest, row.id)
        manifest.entry_snapshot = {**manifest.entry_snapshot, "version": "9.9.9"}
    with pytest.raises(PinUnavailable):
        ensure_world_pins(context_b, catalog_for(context_b))

    # 3) 删一行 pin → catalog_ids 与实际行集不符 → fail-closed
    (tmp_path / "c").mkdir()
    url3 = sqlite_url(tmp_path / "c")
    seed_world(url3, "world-c")
    context_c = tmp_context(tmp_path / "c", "world-c", PROJECT_ROOT)
    context_c.database_url = url3
    assert ensure_world_pins(context_c, catalog_for(context_c))
    with session_scope(url3) as session:
        row = (
            session.query(WorldSkillPin)
            .filter_by(world_id="world-c", skill_id="keeper.magic")
            .one()
        )
        manifest = session.get(WorldSkillPinManifest, row.id)
        if manifest is not None:
            session.delete(manifest)
        session.delete(row)
    with pytest.raises(PinUnavailable):
        ensure_world_pins(context_c, catalog_for(context_c))


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("empty", lambda snapshot: {}),
        ("empty_catalog", lambda snapshot: {**snapshot, "catalog_ids": []}),
        (
            "partial_entry",
            lambda snapshot: {key: value for key, value in snapshot.items() if key != "activation"},
        ),
    ],
)
def test_sidecar_snapshot_must_be_full_and_nonempty(
    tmp_path: Path, case: str, mutate
) -> None:
    """A present sidecar is a full frozen authority snapshot, never legacy."""
    from src.skill_pins import PinUnavailable

    case_root = tmp_path / case
    case_root.mkdir()
    url = sqlite_url(case_root)
    seed_world(url, "world-a")
    context = fake_context(case_root, "world-a")
    assert ensure_world_pins(context, catalog_for(context))
    with session_scope(url) as session:
        pin = session.query(WorldSkillPin).filter_by(world_id="world-a").first()
        manifest = session.get(WorldSkillPinManifest, pin.id)
        assert manifest is not None
        manifest.entry_snapshot = mutate(dict(manifest.entry_snapshot))

    with pytest.raises(PinUnavailable):
        ensure_world_pins(context, catalog_for(context))


def test_sidecar_catalog_order_must_match_each_pinned_skill(tmp_path: Path) -> None:
    """A complete but reordered catalog snapshot cannot alter injection order."""
    from src.skill_pins import PinUnavailable

    url = sqlite_url(tmp_path)
    seed_world(url, "world-a")
    context = fake_context(tmp_path, "world-a")
    assert ensure_world_pins(context, catalog_for(context))
    with session_scope(url) as session:
        for manifest in session.query(WorldSkillPinManifest).all():
            snapshot = dict(manifest.entry_snapshot)
            snapshot["catalog_ids"] = list(reversed(snapshot["catalog_ids"]))
            manifest.entry_snapshot = snapshot

    with pytest.raises(PinUnavailable):
        ensure_world_pins(context, catalog_for(context))


@pytest.mark.parametrize("kind", ("orphan", "self", "cycle", "depth"))
def test_branch_pin_lineage_corruption_never_falls_back_to_live_catalog(
    tmp_path: Path, kind: str
) -> None:
    """Branch metadata errors must fail before any child can independently pin."""
    from src.skill_pins import PinUnavailable

    case_root = tmp_path / kind
    case_root.mkdir()
    url = sqlite_url(case_root)
    target = "child"

    if kind == "orphan":
        seed_world(url, target)
        with session_scope(url) as session:
            session.get(World, target).metadata_json = {"branch": {"parent_world_id": "gone"}}
    elif kind == "self":
        seed_world(url, target)
        with session_scope(url) as session:
            session.get(World, target).metadata_json = {"branch": {"parent_world_id": target}}
    elif kind == "cycle":
        seed_world(url, "a")
        seed_world(url, "b")
        target = "a"
        with session_scope(url) as session:
            session.get(World, "a").metadata_json = {"branch": {"parent_world_id": "b"}}
            session.get(World, "b").metadata_json = {"branch": {"parent_world_id": "a"}}
    else:
        # child -> ... -> root is nine edges; the bounded parent lineage must
        # reject it rather than reaching root and pinning current disk files.
        ids = [f"w{index}" for index in range(10)]
        for world_id in ids:
            seed_world(url, world_id)
        target = ids[-1]
        with session_scope(url) as session:
            for index in range(1, len(ids)):
                session.get(World, ids[index]).metadata_json = {
                    "branch": {"parent_world_id": ids[index - 1]}
                }

    context = fake_context(case_root, target)
    with pytest.raises(PinUnavailable):
        ensure_world_pins(context, catalog_for(context))
    with session_scope(url) as session:
        assert session.query(WorldSkillPin).filter_by(world_id=target).count() == 0


def test_branch_inherit_without_live_catalog_and_copy_fail_closed(tmp_path: Path):
    """源已 pin 后继承不读活 catalog；复制路径 DB 失败受控上抛。"""
    from src.skill_pins import PinUnavailable, inherit_pins_for_branch

    project = tmp_project(tmp_path)
    url = sqlite_url(tmp_path)
    seed_world(url, "parent")
    parent = tmp_context(tmp_path, "parent", project)
    parent_pins = ensure_world_pins(parent, catalog_for(parent))
    assert parent_pins is not None
    seed_world(url, "child")
    child = tmp_context(tmp_path, "child", project)

    # 删掉 catalog.json 后继承仍成功（不读活 catalog）
    (project / "skills" / "catalog.json").unlink()
    assert inherit_pins_for_branch(parent, child) == len(parent_pins)
    from src.skill_pins import read_world_pins

    assert read_world_pins(child) == parent_pins

    # 复制路径 DB 失败（pin 表被删）→ PinUnavailable，不静默返回 0
    (tmp_path / "d").mkdir()
    url2 = sqlite_url(tmp_path / "d")
    seed_world(url2, "p2")
    seed_world(url2, "c2")
    parent2 = tmp_context(tmp_path / "d", "p2", project)
    child2 = tmp_context(tmp_path / "d", "c2", project)
    parent2.database_url = child2.database_url = url2
    import sqlalchemy as sa

    with get_engine(url2).begin() as conn:
        conn.execute(sa.text("DROP TABLE world_skill_pins"))
    with pytest.raises(PinUnavailable):
        inherit_pins_for_branch(parent2, child2)


def test_load_skill_enforces_frozen_max_context_tokens(tmp_path: Path):
    """内容超出冻结元数据的 max_context_tokens → 整个 pin 集 fail closed。"""
    project = tmp_project(tmp_path)
    engine = _engine_stub(tmp_path, "world-a")
    engine.context = tmp_context(tmp_path, "world-a", project)
    engine.context.world_store = SimpleNamespace(load=lambda: {})

    # This is a frozen-metadata test, rather than a source-catalog test: an
    # invalid source budget must now be rejected before pinning.  Pin a valid
    # catalog first, then simulate a corrupted/overly-small frozen budget.

    assert ensure_world_pins(engine.context, catalog_for(engine.context))
    with session_scope(engine.context.database_url) as session:
        pin = (
            session.query(WorldSkillPin)
            .filter_by(world_id="world-a", skill_id="keeper.magic")
            .one()
        )
        manifest = session.get(WorldSkillPinManifest, pin.id)
        assert manifest is not None
        manifest.entry_snapshot = {**manifest.entry_snapshot, "max_context_tokens": 1}
    engine._skill_catalog_cache = None
    engine._skill_pins_cache = None

    from src.skill_activation import execute_load_skill

    with _execution_window(engine):
        result = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert result["ok"] is False
    # A frozen manifest/content mismatch is integrity corruption.  It is
    # rejected before the tool can enter a model request, rather than merely
    # returning the over-budget body to an otherwise live request.
    assert result["error"] == "skill_not_loadable"
    assert "keeper.magic" not in engine._loaded_optional_skills


def test_load_skill_result_event_carries_skill_provenance(tmp_path: Path):
    """load_skill 成功结果落 context events 时带 source_kind=skill + digest。"""
    from src.skill_activation import execute_load_skill, note_load_skill_result

    engine = _engine_stub(tmp_path, "world-a")
    engine.context.world_store = SimpleNamespace(load=lambda: {})
    with _execution_window(engine):
        output = execute_load_skill(engine, "keeper.magic")
    payload = json.loads(output)
    assert payload["ok"]

    message = {"role": "tool", "tool_call_id": "call_1", "content": output}
    engine.messages.append(message)
    note_load_skill_result(engine, message, output)

    store = ContextEventStore(engine.context.database_url)
    store.ensure_session("world-a")
    session = store.session_for_world("world-a")
    # 与 context_shadow._sync 相同：pending_skill_sources 透传 provenance
    shadow = engine.__dict__.get("_context_shadow")
    assert shadow is not None and shadow.pending_skill_sources
    status, _ = store.sync_messages(
        session["id"], engine.messages, provenance=shadow.pending_skill_sources
    )
    assert status == "appended"
    with session_scope(engine.context.database_url) as db:
        from src.database import ModelContextEvent

        event = (
            db.query(ModelContextEvent)
            .filter_by(session_id=session["id"], event_type="tool_result")
            .one()
        )
        assert event.source_kind == "skill"
        assert event.source_id == "keeper.magic"
        assert event.source_version == payload["digest"]

    # 拒绝/失败的输出不登记溯源
    denied = json.dumps({"ok": False, "error": "skill_not_loadable"})
    shadow.pending_skill_sources.clear()
    note_load_skill_result(engine, {"role": "tool", "content": denied}, denied)
    assert not shadow.pending_skill_sources

    # A compact acknowledgement (or a malformed success) has no injected
    # content, so it must never create a ``source_version=""`` provenance row.
    already_loaded = json.dumps(
        {"ok": True, "already_loaded": True, "skill_id": "keeper.magic"}
    )
    note_load_skill_result(engine, {"role": "tool", "content": already_loaded}, already_loaded)
    assert not shadow.pending_skill_sources


def test_load_skill_checks_current_surface_and_never_bypasses_frozen_digest(tmp_path: Path):
    """Current-surface acknowledgement may not bypass frozen id/digest checks."""
    from src.skill_activation import execute_load_skill, loadable_skill_allowlist

    engine = _engine_stub(tmp_path, "world-a")
    engine.context.world_store = SimpleNamespace(load=lambda: {})
    allowlist = loadable_skill_allowlist(engine)

    with _execution_window(engine, skill_allowlist=allowlist):
        first = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert first["ok"] is True
    assert "keeper.magic" in engine._loaded_optional_skills
    # ToolPipeline normally appends this tool result.  Make that current
    # model-visible surface explicit for this direct-handler test.
    engine.messages.append({"role": "tool", "tool_call_id": "call_1", "content": json.dumps(first)})

    # 同一 skill 已加载，但当次请求冻结集合不含它 → 连 already_loaded 都没有
    with _execution_window(engine, skill_allowlist=()):
        result = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert result["ok"] is False
    assert "already_loaded" not in result

    # 冻结集合含该 id 但 digest 被篡改 → 同样拒绝
    tampered = tuple(
        (sid, "sha256:" + "1" * 64) if sid == "keeper.magic" else (sid, digest)
        for sid, digest in allowlist
    )
    with _execution_window(engine, skill_allowlist=tampered):
        result = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert result["ok"] is False

    # 精确匹配且完整结果仍在当前 surface → compact acknowledgement.
    with _execution_window(engine, skill_allowlist=allowlist):
        result = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert result["ok"] is True and result["already_loaded"] is True
    assert result["digest"] == first["digest"]
    assert "content" not in result

    # A compaction/prune that removes the full result must force a full pinned
    # reload, even though the old in-memory lifetime set is still populated.
    engine.messages = [{"role": "system", "content": "compacted surface"}]
    with _execution_window(engine, skill_allowlist=allowlist):
        restored = json.loads(execute_load_skill(engine, "keeper.magic"))
    assert restored["ok"] is True
    assert restored["digest"] == first["digest"]
    assert restored["content"] == first["content"]
    assert "already_loaded" not in restored
