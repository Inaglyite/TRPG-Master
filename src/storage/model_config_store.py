"""云端模型服务配置存储：按账号/世界作用域读写 BYOK 绑定。

- api_key 一律以 Fernet 密文入库（crypto_box），主密钥独立于数据库；
- 解析顺序：世界/房间覆盖（行主必须仍是当前房主）→ 房主账号默认 → 未配置；
  成员个人默认配置永不作用于房间；
- 云端一律 BYOK-only：解析结果烧入 byok_required=True，缺绑定时
  resolve_routes 抛 RouteNotConfiguredError，绝不回落平台付费模型；
- 房主更换后原房间配置不失效于静默回退，而是阻断新调用并要求重新绑定。
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from ..ai.model import route_service
from ..ai.model.crypto_box import decrypt_secret, encrypt_secret
from ..ai.model.route_service import (
    EffectiveSettings,
    RoleBinding,
    RouteBlockedError,
    ServiceSpec,
    default_settings,
)
from .database import ModelServiceConfig, session_scope, utcnow

ACCOUNT_SCOPE = ""  # world_id 空串 = 账号默认作用域


def _service_to_row(service: ServiceSpec) -> dict:
    return {
        "label": service.label,
        "provider_kind": service.provider_kind,
        "base_url": service.base_url,
        "api_key_enc": encrypt_secret(service.api_key),
        "model_id": service.model_id,
        "window_tokens": service.window_tokens,
        "max_output_tokens": service.max_output_tokens,
        "capabilities": service.capabilities,
    }


def _role_to_row(binding: RoleBinding) -> dict:
    if binding.mode != "custom" or binding.service is None:
        return {"mode": "default"}
    return {"mode": "custom", "service": _service_to_row(binding.service)}


def _service_from_row(raw: dict) -> ServiceSpec:
    data = dict(raw)
    encrypted = str(data.pop("api_key_enc", "") or "")
    if not encrypted:
        raise ValueError("配置缺少加密凭据")
    data["api_key"] = decrypt_secret(encrypted)
    # 云端作用域永远不允许私网目标，无论历史行里写了什么。
    return route_service.parse_service_spec(data, role_label="自定义服务", allow_private=False)


def _settings_to_row(settings: EffectiveSettings) -> dict:
    return {
        "narrative": _role_to_row(settings.narrative),
        "judgement": _role_to_row(settings.judgement),
    }


def _settings_from_row(payload: dict, revision: int) -> EffectiveSettings:
    def role(raw: object) -> RoleBinding:
        if not isinstance(raw, dict) or raw.get("mode") != "custom":
            return RoleBinding(mode="default", service=None)
        return RoleBinding(mode="custom", service=_service_from_row(raw.get("service") or {}))

    return EffectiveSettings(
        narrative=role(payload.get("narrative")),
        judgement=role(payload.get("judgement")),
        revision=revision,
    )


def load_scope(url: str, owner_user_id: str, world_id: str) -> EffectiveSettings | None:
    """读取一个作用域的配置；不存在返回 None。凭据解密失败抛可读错误。"""
    with session_scope(url) as session:
        row = (
            session.query(ModelServiceConfig)
            .filter_by(owner_user_id=owner_user_id, world_id=world_id)
            .one_or_none()
        )
        if row is None:
            return None
        return _settings_from_row(dict(row.payload_json or {}), int(row.revision))


def save_scope(
    url: str, owner_user_id: str, world_id: str, settings_no_revision: EffectiveSettings
) -> EffectiveSettings:
    """写入作用域配置（revision 自增），返回带新 revision 的 EffectiveSettings。"""
    with session_scope(url) as session:
        row = (
            session.query(ModelServiceConfig)
            .filter_by(owner_user_id=owner_user_id, world_id=world_id)
            .one_or_none()
        )
        revision = int(row.revision) + 1 if row is not None else 1
        payload = _settings_to_row(settings_no_revision)
        if row is None:
            row = ModelServiceConfig(
                id=uuid.uuid4().hex,
                owner_user_id=owner_user_id,
                world_id=world_id,
                payload_json=payload,
                revision=revision,
                updated_at=utcnow(),
            )
            session.add(row)
        else:
            row.payload_json = payload
            row.revision = revision
            row.updated_at = utcnow()
    return EffectiveSettings(
        narrative=settings_no_revision.narrative,
        judgement=settings_no_revision.judgement,
        revision=revision,
    )


def delete_scope(url: str, owner_user_id: str, world_id: str) -> bool:
    """恢复默认：只删除覆盖行，不动存档与世界事实。"""
    with session_scope(url) as session:
        deleted = (
            session.query(ModelServiceConfig)
            .filter_by(owner_user_id=owner_user_id, world_id=world_id)
            .delete()
        )
        return bool(deleted)


def has_world_row(url: str, world_id: str) -> bool:
    """世界/房间是否存在任何配置覆盖行（不看归属，用于展示"已被覆盖"状态）。"""
    if not world_id:
        return False
    with session_scope(url) as session:
        return session.query(ModelServiceConfig.id).filter_by(world_id=world_id).first() is not None


def save_scope_same_revision(
    url: str, owner_user_id: str, world_id: str, settings: EffectiveSettings
) -> None:
    """能力标记回写：只更新 payload，不动 revision（不触发路由重解析）。"""
    with session_scope(url) as session:
        row = (
            session.query(ModelServiceConfig)
            .filter_by(owner_user_id=owner_user_id, world_id=world_id)
            .one_or_none()
        )
        if row is None:
            return
        row.payload_json = _settings_to_row(settings)
        row.updated_at = utcnow()


def resolve_cloud_settings(
    url: str, *, owner_user_id: str, world_id: str | None
) -> EffectiveSettings:
    """房间/在线单人共用解析：世界覆盖（房主校验）→ 房主账号默认 → 未配置。

    房主已更换但配置仍属原房主时阻断（不静默回落平台付费模型）。
    返回值一律烧入 byok_required=True：云端不存在平台兜底。
    """
    if world_id:
        row = _load_world_row(url, world_id)
        if row is not None:
            if row.owner_user_id != owner_user_id:
                raise RouteBlockedError(
                    "原房主的模型配置已随房主更换失效，需要当前房主重新绑定后再继续"
                )
            world_settings = _settings_from_row(dict(row.payload_json or {}), int(row.revision))
        else:
            world_settings = None
    else:
        world_settings = None
    account = load_scope(url, owner_user_id, ACCOUNT_SCOPE)
    if world_settings is None and account is None:
        return replace(default_settings(), byok_required=True)

    # 逐角色层叠：世界覆盖的角色用世界行，其余跟随账号默认，最后平台默认。
    def pick(slot: str) -> RoleBinding:
        for layer in (world_settings, account):
            if layer is None:
                continue
            binding = getattr(layer, slot)
            if binding.mode == "custom":
                return binding
        return RoleBinding(mode="default", service=None)

    revision = (world_settings.revision if world_settings else 0) + (
        account.revision if account else 0
    )
    return EffectiveSettings(
        narrative=pick("narrative"),
        judgement=pick("judgement"),
        revision=revision,
        byok_required=True,
    )


def current_world_owner_id(url: str, world_id: str) -> str | None:
    """世界的当前房主（WorldMember role=owner）；无成员体系的世界返回 None。"""
    from .database import WorldMember

    with session_scope(url) as session:
        row = session.query(WorldMember.user_id).filter_by(world_id=world_id, role="owner").first()
        return row[0] if row else None


def room_route_resolver(url: str, world_id: str):
    """房间/在线单人引擎的 route_resolver：每回合边界按当前房主重新解析。

    房主更换后原配置立即失效并阻断（RouteBlockedError），绝不静默回落。
    byok_only=True 标记供 GLM 旁路等平台凭据路径识别并自行关闭。
    """

    def resolve() -> EffectiveSettings:
        owner = current_world_owner_id(url, world_id)
        if owner is None:
            return replace(default_settings(), byok_required=True)
        return resolve_cloud_settings(url, owner_user_id=owner, world_id=world_id)

    resolve.byok_only = True  # type: ignore[attr-defined]
    return resolve


def _load_world_row(url: str, world_id: str) -> ModelServiceConfig | None:
    with session_scope(url) as session:
        return (
            session.query(ModelServiceConfig)
            .filter_by(world_id=world_id)
            .order_by(ModelServiceConfig.updated_at.desc())
            .first()
        )
