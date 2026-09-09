"""settings_service：本地/房间/单人三作用域的读写、权限与测试连接门禁。"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.ai.model import crypto_box
from src.app.settings_service import (
    SettingsScope,
    apply_update,
    build_payload,
    restore_default,
    run_test_connection,
)
from src.storage.database import Base, User, get_engine, session_scope
from src.storage.model_config_store import resolve_cloud_settings

CUSTOM_FORM = {
    "mode": "custom",
    "service": {
        "label": "我的网关",
        "provider_kind": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-room-owner-key",
        "model_id": "deepseek-v4-flash",
        "window_tokens": 65536,
    },
}


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("TRPG_CONFIG_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    crypto_box.reset_cache_for_tests()
    yield
    crypto_box.reset_cache_for_tests()


@pytest.fixture(autouse=True)
def clear_test_cooldowns():
    import src.app.settings_service as service

    service._test_cooldowns.clear()
    service._test_daily.clear()
    yield
    service._test_cooldowns.clear()
    service._test_daily.clear()


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(User(id="owner-1", username="owner", password_hash="x"))
        session.add(User(id="member-1", username="member", password_hash="x"))
    return url


def room_scope(db_url: str, user_id: str, role: str) -> SettingsScope:
    return SettingsScope(
        mode="room",
        can_edit=role == "owner",
        user_id=user_id,
        owner_user_id="owner-1",
        world_id="world-1",
        db_url=db_url,
    )


class TestRoomPermissions:
    def test_member_cannot_edit_but_reads_sanitized(self, db):
        owner = room_scope(db, "owner-1", "owner")
        apply_update(
            owner,
            {
                "narrative": CUSTOM_FORM,
                "judgement": {"mode": "default"},
                "confirm_data_sharing": True,
            },
        )
        member = room_scope(db, "member-1", "player")
        with pytest.raises(PermissionError, match="仅房主可修改"):
            apply_update(
                member, {"narrative": {"mode": "default"}, "judgement": {"mode": "default"}}
            )
        with pytest.raises(PermissionError):
            restore_default(member, {})

        view = build_payload(member)
        assert view["can_edit"] is False
        assert view["mode"] == "room"
        assert view["narrative"]["mode"] == "custom"
        # 成员只见服务商与目的地主机名：无完整 URL、无凭据
        assert view["narrative"]["service"]["base_url"] is None
        assert view["narrative"]["service"]["base_url_host"] == "api.deepseek.com"
        assert "sk-room-owner-key" not in str(view)

    def test_owner_view_reveals_full_url_for_editing(self, db):
        owner = room_scope(db, "owner-1", "owner")
        apply_update(
            owner,
            {
                "narrative": CUSTOM_FORM,
                "judgement": {"mode": "default"},
                "confirm_data_sharing": True,
            },
        )
        view = build_payload(owner)
        assert view["can_edit"] is True
        assert view["narrative"]["service"]["base_url"] == "https://api.deepseek.com/v1"
        assert "sk-room-owner-key" not in str(view)
        # 世界覆盖行对当前房主可解析
        resolved = resolve_cloud_settings(db, owner_user_id="owner-1", world_id="world-1")
        assert resolved.narrative.mode == "custom"

    def test_restore_default_clears_room_override(self, db):
        owner = room_scope(db, "owner-1", "owner")
        apply_update(
            owner,
            {
                "narrative": CUSTOM_FORM,
                "judgement": {"mode": "default"},
                "confirm_data_sharing": True,
            },
        )
        result = restore_default(owner, {})
        assert result["saved"] is True
        assert result["narrative"]["mode"] == "default"
        assert result["world_override"] is False


class TestSoloScopes:
    def test_account_default_and_world_override(self, db):
        solo = SettingsScope(
            mode="solo",
            can_edit=True,
            user_id="owner-1",
            owner_user_id="owner-1",
            world_id="world-9",
            db_url=db,
        )
        apply_update(
            solo,
            {
                "narrative": CUSTOM_FORM,
                "judgement": {"mode": "default"},
                "confirm_data_sharing": True,
            },
        )
        view = build_payload(solo)
        assert view["scope_label"].startswith("当前账号")
        assert view["world_override"] is False

        apply_update(
            solo,
            {
                "narrative": {"mode": "default"},
                "judgement": {
                    "mode": "custom",
                    "service": {**CUSTOM_FORM["service"], "model_id": "deepseek-v4-pro"},
                },
                "scope": "world",
                "confirm_data_sharing": True,
            },
        )
        view = build_payload(solo)
        assert view["world_override"] is True
        assert "已单独覆盖" in view["scope_label"]
        assert view["judgement"]["model_id"] == "deepseek-v4-pro"
        # 叙述仍走账号默认覆盖
        assert view["narrative"]["model_id"] == "deepseek-v4-flash"


class TestLocalScope:
    def test_local_update_and_restore(self, tmp_path):
        path = tmp_path / "model_settings.local.json"
        scope = SettingsScope(mode="local", can_edit=True, local_path=path)
        form = {
            "narrative": {
                "mode": "custom",
                "service": {
                    "provider_kind": "openai_compatible",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "api_key": "sk-local",
                    "model_id": "qwen3:32b",
                },
            },
            "judgement": {"mode": "default"},
            "confirm_data_sharing": True,
        }
        result = apply_update(scope, form)
        assert result["saved"] is True
        assert result["scope_label"].startswith("本地默认")
        assert path.exists()

        restored = restore_default(scope, {})
        assert restored["narrative"]["mode"] == "default"
        assert not path.exists()


class TestConnectionGates:
    def test_requires_edit_permission(self, db):
        member = room_scope(db, "member-1", "player")
        with pytest.raises(PermissionError):
            run_test_connection(member, {"role": "narrative"})

    def test_unsaved_form_requires_consent(self, db):
        owner = room_scope(db, "owner-1", "owner")
        with pytest.raises(ValueError, match="确认"):
            run_test_connection(owner, {"role": "narrative", "service": CUSTOM_FORM["service"]})

    def test_cloud_default_binding_reports_unconfigured(self, db, monkeypatch):
        """云端 BYOK-only：默认绑定的测试连接不探测平台模型，直接报未配置。"""
        owner = room_scope(db, "owner-1", "owner")
        import src.app.settings_service as service

        def _platform_probe():  # 平台兜底被调用即失败
            raise AssertionError("云端测试连接不得触碰平台默认 client")

        monkeypatch.setattr(service.route_service, "server_default_client", _platform_probe)
        result = run_test_connection(owner, {"role": "narrative"})
        assert result["ok"] is False
        assert result["target_host"] == "未配置"
        assert result["checks"][0]["name"] == "configured"
        assert "未配置" in result["checks"][0]["detail"]

    def test_cooldown_blocks_rapid_repeat(self, db, monkeypatch):
        owner = room_scope(db, "owner-1", "owner")
        apply_update(
            owner,
            {
                "narrative": CUSTOM_FORM,
                "judgement": {"mode": "default"},
                "confirm_data_sharing": True,
            },
        )
        # 探针本身不触网：把 chat/models 探针替换为成功桩
        import src.app.settings_service as service

        monkeypatch.setattr(service, "_probe_chat", lambda client, model: None)
        fake_client = type(
            "C", (), {"models": type("M", (), {"list": lambda self, timeout: []})()}
        )()
        monkeypatch.setattr(service, "_probe_client", lambda *args, **kwargs: fake_client)
        first = run_test_connection(owner, {"role": "narrative"})
        assert first["ok"] is True
        with pytest.raises(ValueError, match="间隔过短"):
            run_test_connection(owner, {"role": "narrative"})

    def test_daily_limit(self, db, monkeypatch):
        owner = room_scope(db, "owner-1", "owner")
        apply_update(
            owner,
            {
                "narrative": CUSTOM_FORM,
                "judgement": {"mode": "default"},
                "confirm_data_sharing": True,
            },
        )
        import src.app.settings_service as service

        monkeypatch.setattr(service, "TEST_COOLDOWN_SECONDS", 0)
        monkeypatch.setattr(service, "_probe_chat", lambda client, model: None)
        fake_client = type(
            "C", (), {"models": type("M", (), {"list": lambda self, timeout: []})()}
        )()
        monkeypatch.setattr(service, "_probe_client", lambda *args, **kwargs: fake_client)
        for _ in range(service.TEST_DAILY_LIMIT):
            assert run_test_connection(owner, {"role": "narrative"})["ok"] is True
        with pytest.raises(ValueError, match="每日上限"):
            run_test_connection(owner, {"role": "narrative"})
