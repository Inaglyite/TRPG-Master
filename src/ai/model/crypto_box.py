"""用户模型配置凭据的对称加密（Fernet）。

主密钥解析顺序：
1. 环境变量 ``TRPG_CONFIG_MASTER_KEY``（urlsafe base64 32 字节，运营注入）；
2. 运行目录下 ``config_master.key``（0600，首次自动生成）——主密钥独立于
   数据库与存档文件，数据库泄露时不直接暴露凭据。

本地桌面部署同样走第 2 条；`.env.json` 的进程级凭据维持原样不动。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ...app.config import RUNTIME_ROOT

_ENV_KEY = "TRPG_CONFIG_MASTER_KEY"
_KEY_FILENAME = "config_master.key"

_fernet: Fernet | None = None
_lock = threading.Lock()


class ConfigKeyError(RuntimeError):
    """主密钥缺失/非法或密文无法解开。"""


def _key_path() -> Path:
    """惰性解析主密钥文件位置，测试可通过 TRPG_RUNTIME_ROOT 隔离。"""
    root = os.environ.get("TRPG_RUNTIME_ROOT", "").strip()
    base = Path(root).resolve() if root else Path(RUNTIME_ROOT)
    return base / _KEY_FILENAME


def _load_or_create_key_file(path: Path) -> bytes:
    if path.exists():
        raw = path.read_bytes().strip()
        _validate_key(raw)
        return raw
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(key + b"\n")
    except FileExistsError:
        return _load_or_create_key_file(path)
    return key


def _validate_key(raw: bytes) -> None:
    try:
        Fernet(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigKeyError(f"{_ENV_KEY} 不是合法的 Fernet 密钥") from exc


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    with _lock:
        if _fernet is not None:
            return _fernet
        env_value = os.environ.get(_ENV_KEY, "").strip()
        if env_value:
            raw = env_value.encode("ascii")
            _validate_key(raw)
        else:
            raw = _load_or_create_key_file(_key_path())
        _fernet = Fernet(raw)
        return _fernet


def encrypt_secret(plaintext: str) -> str:
    """加密凭据，返回 Fernet token 字符串。"""
    if not plaintext:
        raise ConfigKeyError("不能加密空凭据")
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """解密凭据；密文损坏或主密钥不匹配时抛 ConfigKeyError。"""
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ConfigKeyError("配置凭据无法解密（主密钥不匹配或密文损坏）") from exc


def master_key_source() -> str:
    """当前主密钥来源："env"（运营注入）或 "file"（运行目录自动生成）。

    自动生成文件可用但需人工备份：丢失即全部 BYOK 凭据不可解密。
    """
    if os.environ.get(_ENV_KEY, "").strip():
        return "env"
    return "file"


def key_file_path() -> Path:
    """自动生成主密钥的文件位置（供运维提示/备份指引引用）。"""
    return _key_path()


def reset_cache_for_tests() -> None:
    """测试用：清除缓存的 Fernet 实例（主密钥切换后调用）。"""
    global _fernet
    with _lock:
        _fernet = None
