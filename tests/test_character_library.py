"""角色库：卡面校验、按 owner 隔离的 CRUD、HTTP 面与开局解析。

覆盖验收语义：合法导入可见、非法/超大/错误版本不留半成品、同名不覆盖、
编辑不影响已开局世界（库条目与世界快照互不回写）、跨用户不可读写。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.auth.service import create_user
from src.gameplay.character_library import (
    CARD_FORMAT,
    CARD_FORMAT_VERSION,
    CharacterLibraryError,
    create_entry,
    delete_entry,
    duplicate_entry,
    get_entry,
    inspect_payload,
    list_entries,
    resolve_library_card,
    update_entry,
    validate_card,
)
from src.gameplay.characters import list_character_options, resolve_character
from src.storage.database import (
    Base,
    World,
    WorldMember,
    get_engine,
    new_id,
    session_scope,
)


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


def fresh_db(tmp_path: Path) -> str:
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    return url


def valid_card() -> dict:
    return {
        "name": "测试调查员",
        "occupation": "记者",
        "age": 30,
        "era": "1920年代",
        "attributes": {
            "STR": 50, "DEX": 60, "CON": 55, "INT": 70,
            "POW": 65, "SIZ": 50, "APP": 45, "EDU": 75,
        },
        "derived": {
            "HP": 10, "max_HP": 10, "SAN": 65, "max_SAN": 65,
            "MP": 13, "MOV": 8, "DB": "0", "BUILD": 0, "LUCK": 60,
        },
        "skills": {"library_use": 60, "spot_hidden": 55},
        "credit_rating": 30,
        "inventory": ["笔记本", {"label": "手电筒", "quantity": 1}],
        "backstory": {"description": "短发，眼神锐利", "background": "跑社会新闻五年"},
        "psychological_profile": {
            "traits": ["谨慎"],
            "key_relationships": [],
            "phobias": [],
            "manias": [],
        },
        "career": {
            "reputation": 1,
            "titles": [],
            "known_contacts": [],
            "completed_modules": [],
            "case_history": [],
        },
    }


# ---------------------------------------------------------------- 卡面校验


def test_valid_card_passes_and_preserves_values():
    card, errors, warnings = validate_card(valid_card())
    assert errors == []
    assert card["name"] == "测试调查员"
    assert card["attributes"]["POW"] == 65
    assert card["derived"]["SAN"] == 65
    assert card["derived"]["LUCK"] == 60
    assert card["skills"]["library_use"] == 60
    # 与属性一致的 derived 不产生重算告警
    assert not any("重算" in warning for warning in warnings)


def test_missing_name_and_bad_attribute_are_field_errors():
    card = valid_card()
    card["name"] = ""
    card["attributes"]["STR"] = "很强"
    _, errors, _ = validate_card(card)
    fields = {error["field"] for error in errors}
    assert "name" in fields
    assert "attributes.STR" in fields


def test_missing_attributes_blocks_import():
    card = valid_card()
    del card["attributes"]
    _, errors, _ = validate_card(card)
    assert any(error["field"] == "attributes" for error in errors)


def test_out_of_range_attribute_warns_but_keeps_value():
    card = valid_card()
    card["attributes"]["POW"] = 95  # 老卡成长可超出建卡范围：告警但保留
    normalized, errors, warnings = validate_card(card)
    assert errors == []
    assert normalized["attributes"]["POW"] == 95
    assert normalized["derived"]["SAN"] == 95  # 推导跟随属性
    assert any("POW" in warning for warning in warnings)


def test_derived_inconsistency_is_recomputed_with_warning():
    card = valid_card()
    card["derived"]["HP"] = 99
    card["derived"]["max_HP"] = 99
    normalized, errors, warnings = validate_card(card)
    assert errors == []
    assert normalized["derived"]["HP"] == 10  # (CON 55 + SIZ 50) // 10
    assert normalized["derived"]["max_HP"] == 10
    assert any("HP" in warning for warning in warnings)


def test_missing_luck_warns_and_defaults():
    card = valid_card()
    del card["derived"]["LUCK"]
    normalized, errors, warnings = validate_card(card)
    assert errors == []
    assert normalized["derived"]["LUCK"] == 50
    assert any("幸运" in warning for warning in warnings)


def test_identity_fields_are_stripped_with_warning():
    card = valid_card()
    card.update(
        {
            "id": "evil-id",
            "owner_user_id": "user_victim",
            "world_id": "world-x",
            "controller_user_id": "user_victim",
        }
    )
    normalized, errors, warnings = validate_card(card)
    assert errors == []
    for key in ("id", "owner_user_id", "world_id", "controller_user_id"):
        assert key not in normalized
    assert any("身份/权限字段" in warning for warning in warnings)


def test_unknown_top_level_fields_are_preserved_with_warning():
    card = valid_card()
    card["homebrew_note"] = "模组的自定义字段"
    normalized, errors, warnings = validate_card(card)
    assert errors == []
    assert normalized["homebrew_note"] == "模组的自定义字段"
    assert any("homebrew_note" in warning for warning in warnings)


def test_skill_type_error_blocks_and_high_skill_warns():
    card = valid_card()
    card["skills"]["occult"] = "很多"
    _, errors, _ = validate_card(card)
    assert any(error["field"] == "skills.occult" for error in errors)

    card = valid_card()
    card["skills"]["cthulhu_mythos"] = 95
    normalized, errors, warnings = validate_card(card)
    assert errors == []
    assert normalized["skills"]["cthulhu_mythos"] == 95
    assert any("cthulhu_mythos" in warning for warning in warnings)


def test_bad_inventory_and_backstory_types_are_field_errors():
    card = valid_card()
    card["inventory"] = [123]
    card["backstory"]["description"] = {"bad": "type"}
    _, errors, _ = validate_card(card)
    fields = {error["field"] for error in errors}
    assert "inventory[0]" in fields
    assert "backstory.description" in fields


# ---------------------------------------------------------------- CRUD 与归属隔离


def test_crud_roundtrip_and_duplicate(tmp_path: Path):
    url = fresh_db(tmp_path)
    created = create_entry(url, "", valid_card())
    entry = created["entry"]
    assert entry["name"] == "测试调查员"
    assert entry["ref"]["source"] == "library"

    listed = list_entries(url, "")
    assert [item["id"] for item in listed] == [entry["id"]]

    fetched = get_entry(url, "", entry["id"])
    assert fetched["card"]["skills"]["library_use"] == 60

    card = valid_card()
    card["name"] = "改名调查员"
    card["skills"]["library_use"] = 70
    updated = update_entry(url, "", entry["id"], card)
    assert updated["entry"]["name"] == "改名调查员"

    clone = duplicate_entry(url, "", entry["id"])
    assert clone["entry"]["name"].endswith("（副本）")
    assert clone["entry"]["id"] != entry["id"]
    assert len(list_entries(url, "")) == 2

    delete_entry(url, "", entry["id"])
    delete_entry(url, "", clone["entry"]["id"])
    assert list_entries(url, "") == []


def test_cross_owner_access_is_invisible(tmp_path: Path):
    url = fresh_db(tmp_path)
    mine = create_entry(url, "user_a", valid_card())["entry"]
    assert list_entries(url, "user_b") == []
    with pytest.raises(CharacterLibraryError) as excinfo:
        get_entry(url, "user_b", mine["id"])
    assert excinfo.value.status == 404
    with pytest.raises(CharacterLibraryError):
        update_entry(url, "user_b", mine["id"], valid_card())
    with pytest.raises(CharacterLibraryError):
        delete_entry(url, "user_b", mine["id"])
    with pytest.raises(CharacterLibraryError):
        duplicate_entry(url, "user_b", mine["id"])
    # 所有者不受影响
    assert list_entries(url, "user_a")[0]["id"] == mine["id"]


def test_same_name_import_creates_new_entry_instead_of_overwriting(tmp_path: Path):
    url = fresh_db(tmp_path)
    first = create_entry(url, "", valid_card())["entry"]
    inspected = inspect_payload(url, "", valid_card())
    assert inspected["ok"] is True
    assert any("同名" in warning for warning in inspected["warnings"])
    second = create_entry(url, "", valid_card())
    assert second["entry"]["id"] != first["id"]
    assert any("同名" in warning for warning in second["warnings"])
    # 旧角色原样保留
    assert get_entry(url, "", first["id"])["card"]["occupation"] == "记者"


def test_invalid_payload_leaves_no_partial_entry(tmp_path: Path):
    url = fresh_db(tmp_path)
    card = valid_card()
    card["attributes"]["POW"] = "很强"
    with pytest.raises(CharacterLibraryError) as excinfo:
        create_entry(url, "", card)
    assert excinfo.value.code == "invalid_card"
    assert any(item["field"] == "attributes.POW" for item in excinfo.value.details)
    assert list_entries(url, "") == []


def test_inspect_never_writes(tmp_path: Path):
    url = fresh_db(tmp_path)
    result = inspect_payload(url, "", valid_card())
    assert result["ok"] is True
    assert result["preview"]["name"] == "测试调查员"
    assert list_entries(url, "") == []


def test_envelope_version_and_format_are_checked(tmp_path: Path):
    url = fresh_db(tmp_path)
    bad_format = inspect_payload(url, "", {"format": "coc7-card", "card": valid_card()})
    assert bad_format["ok"] is False
    assert any(error["field"] == "format" for error in bad_format["errors"])
    bad_version = inspect_payload(
        url, "", {"format": CARD_FORMAT, "format_version": 99, "card": valid_card()}
    )
    assert any(error["field"] == "format_version" for error in bad_version["errors"])
    ok = inspect_payload(
        url,
        "",
        {"format": CARD_FORMAT, "format_version": CARD_FORMAT_VERSION, "card": valid_card()},
    )
    assert ok["ok"] is True
    bare = inspect_payload(url, "", valid_card())
    assert bare["ok"] is True
    assert any("未声明格式版本" in warning for warning in bare["warnings"])


def test_export_envelope_roundtrip_without_identity(tmp_path: Path):
    url = fresh_db(tmp_path)
    entry = create_entry(url, "user_a", valid_card())["entry"]
    from src.gameplay.character_library import get_export_entry

    envelope = get_export_entry(url, "user_a", entry["id"])
    assert envelope["format"] == CARD_FORMAT
    assert envelope["format_version"] == CARD_FORMAT_VERSION
    assert "owner_user_id" not in json_dumps(envelope)
    assert envelope["card"]["name"] == "测试调查员"
    # 信封可原样再导入（换 owner），不产生半成品
    imported = create_entry(url, "user_b", envelope)
    assert imported["entry"]["name"] == "测试调查员"


def json_dumps(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- 开局解析归属


def _seed_world(url: str, world_id: str, created_by: str) -> None:
    with session_scope(url) as session:
        session.add(
            World(id=world_id, module_name="mansion_of_madness", created_by=created_by)
        )


def test_resolve_library_card_local_owner_scope(tmp_path: Path):
    url = fresh_db(tmp_path)
    user_a = create_user(url, "lib_local_a", "password-a-123")
    entry = create_entry(url, "", valid_card())["entry"]
    context = SimpleNamespace(database_url=url, world_id="local-world")
    with patch.dict(os.environ, {"TRPG_REQUIRE_AUTH": "0"}):
        card = resolve_library_card(entry["id"], context=context)
        assert card is not None and card["name"] == "测试调查员"
    # 本地模式拒绝解析带账号归属的条目（如备份恢复进来的云端行）
    other = create_entry(url, user_a.id, valid_card())["entry"]
    with patch.dict(os.environ, {"TRPG_REQUIRE_AUTH": "0"}):
        assert resolve_library_card(other["id"], context=context) is None


def test_resolve_library_card_cloud_requires_world_owner(tmp_path: Path):
    url = fresh_db(tmp_path)
    user_a = create_user(url, "lib_user_a", "password-a-123")
    user_b = create_user(url, "lib_user_b", "password-b-123")
    entry = create_entry(url, user_a.id, valid_card())["entry"]
    _seed_world(url, "world-a", user_a.id)
    _seed_world(url, "world-b", user_b.id)
    with patch.dict(os.environ, {"TRPG_REQUIRE_AUTH": "1"}):
        mine = resolve_library_card(
            entry["id"], context=SimpleNamespace(database_url=url, world_id="world-a")
        )
        assert mine is not None
        assert (
            resolve_library_card(
                entry["id"],
                context=SimpleNamespace(database_url=url, world_id="world-b"),
            )
            is None
        )


def _fake_local_context(tmp_path: Path, url: str) -> SimpleNamespace:
    """characters.py 需要的最小目录字段；library 组只读 database_url。"""
    runtime_root = tmp_path / "runtime"
    return SimpleNamespace(
        database_url=url,
        world_id="local-world",
        module_name="mansion_of_madness",
        project_root=tmp_path,
        runtime_root=runtime_root,
        custom_characters_dir=runtime_root / "characters" / "custom",
        profiles_dir=runtime_root / "profiles",
        player_profile_file=runtime_root / "profiles" / "player_profile.json",
        default_characters_dir=tmp_path / "no-defaults",
        module_dir=tmp_path / "no-module",
    )


def test_resolve_character_dispatches_library_source(tmp_path: Path):
    url = fresh_db(tmp_path)
    entry = create_entry(url, "", valid_card())["entry"]
    context = _fake_local_context(tmp_path, url)
    with patch.dict(os.environ, {"TRPG_REQUIRE_AUTH": "0"}):
        char, normalized = resolve_character(
            {"source": "library", "id": entry["id"]}, context=context
        )
    assert char is not None
    assert normalized == {
        "source": "library",
        "id": entry["id"],
        "path": f"character_library#{entry['id']}",
    }


def test_list_character_options_includes_library_group(tmp_path: Path):
    url = fresh_db(tmp_path)
    create_entry(url, "", valid_card())
    context = _fake_local_context(tmp_path, url)
    options = list_character_options(
        "mansion_of_madness", context=context, include_personal=False, library_scope="local"
    )
    group = next(item for item in options["groups"] if item["id"] == "library")
    assert [char["name"] for char in group["characters"]] == ["测试调查员"]
    assert group["characters"][0]["ref"]["source"] == "library"
    # 不带 scope 时（多人房间）不出现角色库分组内容
    options_multi = list_character_options(
        "mansion_of_madness", context=context, include_personal=False
    )
    multi = next(item for item in options_multi["groups"] if item["id"] == "library")
    assert multi["characters"] == []


def test_game_start_snapshots_library_character_and_stays_independent(tmp_path: Path):
    """库角色经真实引擎开局物化为世界快照；此后编辑/删除库条目不回写。"""
    from src.app.config import PROJECT_ROOT
    from src.app.engine import GameEngine
    from src.app.runtime import RuntimeContext

    context = RuntimeContext.local(
        "mansion_of_madness", project_root=PROJECT_ROOT, runtime_root=tmp_path
    )
    entry = create_entry(context.database_url, "", valid_card())["entry"]
    options = list_character_options(context=context, library_scope="local")
    library_group = next(
        group for group in options["groups"] if group["id"] == "library"
    )
    assert [char["id"] for char in library_group["characters"]] == [entry["id"]]

    engine = GameEngine.__new__(GameEngine)
    engine.context = context
    selected = engine.reset({"source": "library", "id": entry["id"]})
    assert selected["name"] == "测试调查员"
    pc = context.world_store.load()["pc"]
    assert pc["name"] == "测试调查员"
    assert pc["skills"]["library_use"] == 60

    edited = valid_card()
    edited["name"] = "改名后的调查员"
    edited["skills"]["library_use"] = 99
    update_entry(context.database_url, "", entry["id"], edited)
    pc_after_edit = context.world_store.load()["pc"]
    assert pc_after_edit["name"] == "测试调查员"
    assert pc_after_edit["skills"]["library_use"] == 60

    delete_entry(context.database_url, "", entry["id"])
    assert context.world_store.load()["pc"]["name"] == "测试调查员"


# ---------------------------------------------------------------- HTTP 面（本地模式）


@pytest.fixture
def local_client(tmp_path: Path):
    import server

    url = fresh_db(tmp_path)
    with patch.object(server, "DATABASE_URL", url):
        with TestClient(server.app) as client:
            yield client, url


def test_http_local_full_lifecycle(local_client):
    client, _url = local_client
    inspected = client.post("/api/character-library/inspect", json=valid_card())
    assert inspected.status_code == 200
    assert inspected.json()["ok"] is True

    created = client.post("/api/character-library", json=valid_card())
    assert created.status_code == 201
    entry = created.json()["entry"]

    listed = client.get("/api/character-library").json()["entries"]
    assert [item["id"] for item in listed] == [entry["id"]]

    card = valid_card()
    card["occupation"] = "私家侦探"
    updated = client.put(f"/api/character-library/{entry['id']}", json=card)
    assert updated.status_code == 200
    assert updated.json()["entry"]["occupation"] == "私家侦探"

    duplicated = client.post(f"/api/character-library/{entry['id']}/duplicate")
    assert duplicated.status_code == 201

    exported = client.get(f"/api/character-library/{entry['id']}/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers.get("content-disposition", "")
    assert exported.json()["format"] == CARD_FORMAT

    deleted = client.delete(f"/api/character-library/{entry['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/character-library/{entry['id']}").status_code == 404


def test_http_rejects_oversize_and_invalid_json(local_client):
    client, _url = local_client
    too_big = client.post(
        "/api/character-library/inspect",
        content=b"{}",
        headers={"Content-Length": str(1024 * 1024)},
    )
    assert too_big.status_code == 413
    bad_json = client.post(
        "/api/character-library",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert bad_json.status_code == 400
    invalid = client.post("/api/character-library", json={"name": "只有名字"})
    assert invalid.status_code == 400
    assert invalid.json()["error_code"] == "invalid_card"
    assert any(
        item["field"] == "attributes" for item in invalid.json()["details"]
    )


# ---------------------------------------------------------------- HTTP 面（云端隔离）

CLOUD_ENV = {
    "TRPG_REQUIRE_AUTH": "1",
    "TRPG_ALLOW_REGISTRATION": "1",
    "TRPG_ALLOWED_ORIGINS": "https://testserver",
    "TRPG_WRITE_COMPAT_EXPORTS": "0",
}
ORIGIN = {"origin": "https://testserver"}


@pytest.fixture
def cloud_clients(tmp_path: Path):
    import server

    url = fresh_db(tmp_path)
    with (
        patch.dict(os.environ, {**CLOUD_ENV, "TRPG_DATABASE_URL": url}),
        patch.object(server, "DATABASE_URL", url),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "owner_a", "password": "password-a-123"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/auth/register",
                json={"username": "owner_b", "password": "password-b-123"},
            ).status_code
            == 201
        )
        # 注册会写入自己的会话 cookie：先登出再以 owner_a 登录，保证起点确定。
        assert client.post("/api/auth/logout", headers=ORIGIN).status_code == 204
        _logout_then_login(client, "owner_a", "password-a-123")
        yield client, url


def _logout_then_login(client: TestClient, username: str, password: str) -> None:
    # logout 不是公开路径：云端 gate 对 POST 强制 Origin 白名单。
    client.post("/api/auth/logout", headers=ORIGIN)
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


def test_cloud_library_is_scoped_per_user(cloud_clients):
    client, _url = cloud_clients
    created = client.post("/api/character-library", json=valid_card(), headers=ORIGIN)
    assert created.status_code == 201
    entry_id = created.json()["entry"]["id"]

    _logout_then_login(client, "owner_b", "password-b-123")
    assert client.get("/api/character-library").json()["entries"] == []
    assert client.get(f"/api/character-library/{entry_id}").status_code == 404
    assert (
        client.put(
            f"/api/character-library/{entry_id}", json=valid_card(), headers=ORIGIN
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/character-library/{entry_id}", headers=ORIGIN).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/character-library/{entry_id}/duplicate", headers=ORIGIN
        ).status_code
        == 404
    )

    _logout_then_login(client, "owner_a", "password-a-123")
    assert len(client.get("/api/character-library").json()["entries"]) == 1


def test_cloud_solo_options_include_library_multiplayer_excludes(cloud_clients):
    client, url = cloud_clients
    entry_id = client.post(
        "/api/character-library", json=valid_card(), headers=ORIGIN
    ).json()["entry"]["id"]
    # 造房：solo 与多人各一（直接写控制面行，避免走房间生命周期）
    with session_scope(url) as session:
        from src.storage.database import User

        user_a = session.query(User).filter_by(username="owner_a").one()
        for world_id, play_mode in (("world-solo", "solo"), ("world-multi", "multiplayer")):
            session.add(
                World(
                    id=world_id,
                    module_name="mansion_of_madness",
                    created_by=user_a.id,
                    metadata_json={"name": world_id, "room_status": "lobby", "play_mode": play_mode},
                )
            )
            session.add(
                WorldMember(
                    id=new_id("member"),
                    world_id=world_id,
                    user_id=user_a.id,
                    role="owner",
                )
            )

    solo = client.get("/api/worlds/world-solo/investigators/options")
    assert solo.status_code == 200
    library_group = next(
        group for group in solo.json()["groups"] if group["id"] == "library"
    )
    assert [char["id"] for char in library_group["characters"]] == [entry_id]

    multi = client.get("/api/worlds/world-multi/investigators/options")
    multi_group = next(
        group for group in multi.json()["groups"] if group["id"] == "library"
    )
    assert multi_group["characters"] == []

    # solo 房间可认领库角色（认领后 ref 由服务端给出，不经客户端伪造）
    claimed = client.post(
        "/api/worlds/world-solo/investigators/claim",
        json={"character_key": entry_id},
        headers=ORIGIN,
    )
    assert claimed.status_code == 200
    # 多人房间认领库角色被拒（不在该房间的候选列表中）
    rejected = client.post(
        "/api/worlds/world-multi/investigators/claim",
        json={"character_key": entry_id},
        headers=ORIGIN,
    )
    assert rejected.status_code == 400
