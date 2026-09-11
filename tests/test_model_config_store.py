"""model_config_store：云端账号/世界作用域存储、加密与房主校验。"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.ai.model import crypto_box
from src.ai.model.route_service import (
    EffectiveSettings,
    RoleBinding,
    RouteBlockedError,
    parse_role_binding,
)
from src.storage.database import (
    Base,
    ModelServiceConfig,
    User,
    get_engine,
    session_scope,
)
from src.storage.model_config_store import (
    ACCOUNT_SCOPE,
    delete_scope,
    load_scope,
    resolve_cloud_settings,
    save_scope,
)

CLOUD_SERVICE = {
    "label": "我的网关",
    "provider_kind": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-cloud-user-key",
    "model_id": "deepseek-flash",
    "window_tokens": 65536,
    "max_output_tokens": 4096,
}


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("TRPG_CONFIG_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    crypto_box.reset_cache_for_tests()
    yield
    crypto_box.reset_cache_for_tests()


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    Base.metadata.create_all(get_engine(url))
    with session_scope(url) as session:
        session.add(User(id="user-a", username="alice", password_hash="x"))
        session.add(User(id="user-b", username="bob", password_hash="x"))
    return url


def _custom_settings(model_id: str = "deepseek-flash") -> EffectiveSettings:
    return EffectiveSettings(
        narrative=parse_role_binding(
            {"mode": "custom", "service": {**CLOUD_SERVICE, "model_id": model_id}},
            role_label="叙述模型",
            allow_private=False,
        ),
        judgement=RoleBinding(mode="default", service=None),
        revision=0,
    )


def test_save_and_load_account_scope(db):
    saved = save_scope(db, "user-a", ACCOUNT_SCOPE, _custom_settings())
    assert saved.revision == 1
    loaded = load_scope(db, "user-a", ACCOUNT_SCOPE)
    assert loaded is not None
    assert loaded.narrative.mode == "custom"
    assert loaded.narrative.service.api_key == "sk-cloud-user-key"
    # 再次保存 revision 自增
    again = save_scope(db, "user-a", ACCOUNT_SCOPE, _custom_settings("deepseek-v4-pro"))
    assert again.revision == 2
    assert load_scope(db, "user-a", ACCOUNT_SCOPE).narrative.service.model_id == "deepseek-v4-pro"


def test_key_never_stored_plaintext(db):
    save_scope(db, "user-a", ACCOUNT_SCOPE, _custom_settings())
    with session_scope(db) as session:
        row = session.query(ModelServiceConfig).one()
        raw = str(row.payload_json)
    assert "sk-cloud-user-key" not in raw
    assert "api_key_enc" in raw


def test_account_isolation(db):
    save_scope(db, "user-a", ACCOUNT_SCOPE, _custom_settings())
    assert load_scope(db, "user-b", ACCOUNT_SCOPE) is None
    assert resolve_cloud_settings(db, owner_user_id="user-b", world_id=None).revision == 0


def test_world_override_beats_account_default(db):
    save_scope(db, "user-a", ACCOUNT_SCOPE, _custom_settings("deepseek-flash"))
    save_scope(db, "user-a", "world-1", _custom_settings("deepseek-v4-pro"))
    resolved = resolve_cloud_settings(db, owner_user_id="user-a", world_id="world-1")
    assert resolved.narrative.service.model_id == "deepseek-v4-pro"
    # 别的世界回落账号默认
    other = resolve_cloud_settings(db, owner_user_id="user-a", world_id="world-2")
    assert other.narrative.service.model_id == "deepseek-flash"


def test_owner_transfer_blocks_old_room_config(db):
    save_scope(db, "user-a", "world-1", _custom_settings())
    # 房主换成 user-b：阻断而不是静默回落
    with pytest.raises(RouteBlockedError, match="重新绑定"):
        resolve_cloud_settings(db, owner_user_id="user-b", world_id="world-1")


def test_restore_default_only_deletes_override(db):
    save_scope(db, "user-a", ACCOUNT_SCOPE, _custom_settings("deepseek-flash"))
    save_scope(db, "user-a", "world-1", _custom_settings("deepseek-v4-pro"))
    assert delete_scope(db, "user-a", "world-1") is True
    resolved = resolve_cloud_settings(db, owner_user_id="user-a", world_id="world-1")
    assert resolved.narrative.service.model_id == "deepseek-flash"
    assert delete_scope(db, "user-a", "world-1") is False


def test_cloud_row_never_allows_private_targets(db, monkeypatch):
    """即使行数据被篡改指向私网，读取解析时也按云端策略拒绝。"""
    save_scope(db, "user-a", ACCOUNT_SCOPE, _custom_settings())
    with session_scope(db) as session:
        row = session.query(ModelServiceConfig).one()
        payload = dict(row.payload_json)
        service = dict(payload["narrative"]["service"])
        service["base_url"] = "http://192.168.1.5:8080/v1"
        payload["narrative"] = {**payload["narrative"], "service": service}
        row.payload_json = payload
    with pytest.raises(ValueError, match="https"):
        load_scope(db, "user-a", ACCOUNT_SCOPE)
