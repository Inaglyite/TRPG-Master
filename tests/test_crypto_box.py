"""crypto_box：配置凭据 Fernet 加密与主密钥解析。"""

from __future__ import annotations

import stat

import pytest
from cryptography.fernet import Fernet

from src.ai.model import crypto_box
from src.ai.model.crypto_box import ConfigKeyError, decrypt_secret, encrypt_secret


@pytest.fixture(autouse=True)
def reset_fernet(monkeypatch, tmp_path):
    monkeypatch.setenv("TRPG_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.delenv("TRPG_CONFIG_MASTER_KEY", raising=False)
    crypto_box.reset_cache_for_tests()
    yield
    crypto_box.reset_cache_for_tests()


def test_roundtrip_with_env_master_key(monkeypatch):
    monkeypatch.setenv("TRPG_CONFIG_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    crypto_box.reset_cache_for_tests()
    token = encrypt_secret("sk-test-1234567890")
    assert token != "sk-test-1234567890"
    assert decrypt_secret(token) == "sk-test-1234567890"


def test_key_file_autocreated_with_0600(tmp_path):
    token = encrypt_secret("sk-local")
    key_file = tmp_path / "config_master.key"
    assert key_file.exists()
    mode = stat.S_IMODE(key_file.stat().st_mode)
    assert mode == 0o600
    assert decrypt_secret(token) == "sk-local"


def test_wrong_master_key_fails_closed(monkeypatch):
    token = encrypt_secret("sk-secret")
    monkeypatch.setenv("TRPG_CONFIG_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    crypto_box.reset_cache_for_tests()
    with pytest.raises(ConfigKeyError, match="无法解密"):
        decrypt_secret(token)


def test_invalid_env_key_rejected(monkeypatch):
    monkeypatch.setenv("TRPG_CONFIG_MASTER_KEY", "not-a-fernet-key")
    crypto_box.reset_cache_for_tests()
    with pytest.raises(ConfigKeyError, match="Fernet"):
        encrypt_secret("sk-x")


def test_empty_secret_rejected():
    with pytest.raises(ConfigKeyError):
        encrypt_secret("")
