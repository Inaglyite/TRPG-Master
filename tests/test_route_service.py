"""route_service：绑定解析、本地存储、路由解析与回合冻结。"""

from __future__ import annotations

import stat

import pytest

from src.ai.model.route_service import (
    EffectiveSettings,
    LocalRouteResolver,
    RoleBinding,
    apply_pending_routes,
    client_for_role,
    default_settings,
    load_local_settings,
    model_for_role,
    parse_role_binding,
    resolve_routes,
    route_public_info,
    save_local_settings,
)

LOCAL_SERVICE = {
    "label": "本机推理",
    "provider_kind": "openai_compatible",
    "base_url": "http://127.0.0.1:11434/v1",
    "api_key": "sk-local-test",
    "model_id": "qwen3:32b",
    "window_tokens": 32768,
    "max_output_tokens": 2048,
}


def custom_binding(**overrides) -> dict:
    service = {**LOCAL_SERVICE, **overrides}
    return {"mode": "custom", "service": service}


class TestParse:
    def test_default_mode(self):
        binding = parse_role_binding({"mode": "default"}, role_label="叙述模型", allow_private=True)
        assert binding.mode == "default" and binding.service is None

    def test_custom_local_private_ok(self):
        binding = parse_role_binding(custom_binding(), role_label="叙述模型", allow_private=True)
        assert binding.service is not None
        assert binding.service.model_id == "qwen3:32b"
        assert binding.service.window_tokens == 32768
        assert binding.service.allow_private is True

    def test_cloud_scope_forbids_private_even_if_payload_asks(self):
        payload = custom_binding()
        payload["service"]["allow_private"] = True  # 用户载荷里的同名字段无效
        with pytest.raises(ValueError, match="https"):
            parse_role_binding(payload, role_label="叙述模型", allow_private=False)

    def test_invalid_provider_kind(self):
        with pytest.raises(ValueError, match="服务类型"):
            parse_role_binding(
                custom_binding(provider_kind="evil"), role_label="叙述模型", allow_private=True
            )

    def test_invalid_model_id(self):
        with pytest.raises(ValueError, match="模型 ID"):
            parse_role_binding(
                custom_binding(model_id="bad model!"), role_label="裁决模型", allow_private=True
            )

    def test_window_out_of_range(self):
        with pytest.raises(ValueError, match="上下文窗口"):
            parse_role_binding(
                custom_binding(window_tokens=1024), role_label="叙述模型", allow_private=True
            )

    def test_missing_key_without_existing_rejected(self):
        payload = custom_binding()
        payload["service"]["api_key"] = ""
        with pytest.raises(ValueError, match="API Key"):
            parse_role_binding(payload, role_label="叙述模型", allow_private=True)

    def test_blank_key_reuses_existing(self):
        existing = parse_role_binding(
            custom_binding(), role_label="叙述模型", allow_private=True
        ).service
        payload = custom_binding(model_id="qwen3:14b")
        payload["service"]["api_key"] = ""
        binding = parse_role_binding(
            payload, role_label="叙述模型", allow_private=True, existing=existing
        )
        assert binding.service.api_key == "sk-local-test"
        assert binding.service.model_id == "qwen3:14b"


class TestLocalStore:
    def test_roundtrip_keeps_secrets_and_permissions(self, tmp_path):
        path = tmp_path / "model_settings.local.json"
        settings = EffectiveSettings(
            narrative=parse_role_binding(
                custom_binding(), role_label="叙述模型", allow_private=True
            ),
            judgement=RoleBinding(mode="default", service=None),
            revision=3,
        )
        save_local_settings(path, settings)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        loaded = load_local_settings(path)
        assert loaded.revision == 3
        assert loaded.narrative.service.api_key == "sk-local-test"
        assert loaded.judgement.mode == "default"

    def test_missing_file_is_default(self, tmp_path):
        loaded = load_local_settings(tmp_path / "absent.json")
        assert loaded.revision == 0
        assert loaded.narrative.mode == "default"

    def test_corrupted_file_readable_error(self, tmp_path):
        path = tmp_path / "model_settings.local.json"
        path.write_text("{oops", encoding="utf-8")
        with pytest.raises(ValueError, match="本地模型配置损坏"):
            load_local_settings(path)

    def test_resolver_caches_by_mtime(self, tmp_path):
        path = tmp_path / "model_settings.local.json"
        resolver = LocalRouteResolver(path)
        assert resolver().revision == 0
        save_local_settings(
            path,
            EffectiveSettings(
                narrative=RoleBinding(mode="default", service=None),
                judgement=parse_role_binding(
                    custom_binding(), role_label="裁决模型", allow_private=True
                ),
                revision=7,
            ),
        )
        assert resolver().revision == 7


class TestResolve:
    def test_default_routes_use_env_models(self):
        routes = resolve_routes(default_settings())
        assert routes.narrative.provider_kind == "server_default"
        assert routes.narrative.window_source == "legacy_default"
        assert routes.judgement.binding_id == "server_default"
        assert routes.revision == 0

    def test_custom_route_resolved_and_client_cached(self):
        settings = EffectiveSettings(
            narrative=parse_role_binding(
                custom_binding(), role_label="叙述模型", allow_private=True
            ),
            judgement=RoleBinding(mode="default", service=None),
            revision=5,
        )
        first = resolve_routes(settings)
        second = resolve_routes(settings)
        assert first.narrative.client is second.narrative.client  # client 缓存复用
        assert first.narrative.window_tokens == 32768
        assert first.narrative.window_source == "manual"
        assert first.narrative.max_output_tokens == 2048
        assert first.narrative.binding_id.startswith("svc_")
        assert first.judgement.provider_kind == "server_default"

    def test_unknown_window_when_unset(self):
        payload = custom_binding()
        payload["service"]["window_tokens"] = None
        settings = EffectiveSettings(
            narrative=parse_role_binding(payload, role_label="叙述模型", allow_private=True),
            judgement=RoleBinding(mode="default", service=None),
            revision=1,
        )
        routes = resolve_routes(settings)
        assert routes.narrative.window_tokens is None
        assert routes.narrative.window_source == "unknown"


class _FakeEngine:
    def __init__(self):
        import openai

        self.client = openai.OpenAI(api_key="sk-env", base_url="https://api.deepseek.com")
        self.narrative_model = "env-narrative"
        self.judgement_model = "env-judgement"


class TestTurnFreeze:
    def test_apply_only_when_revision_changes(self):
        engine = _FakeEngine()
        state = {"settings": default_settings()}
        engine.route_resolver = lambda: state["settings"]
        apply_pending_routes(engine)
        baseline_client = engine.client
        apply_pending_routes(engine)  # 同 revision：不重解析
        assert engine.client is baseline_client

        state["settings"] = EffectiveSettings(
            narrative=parse_role_binding(
                custom_binding(), role_label="叙述模型", allow_private=True
            ),
            judgement=RoleBinding(mode="default", service=None),
            revision=9,
        )
        apply_pending_routes(engine)
        assert engine.narrative_model == "qwen3:32b"
        assert engine.client is engine._model_routes.narrative.client
        assert engine.judgement_client is engine._model_routes.judgement.client

    def test_no_resolver_keeps_env_behavior(self):
        engine = _FakeEngine()
        apply_pending_routes(engine)
        assert engine.narrative_model == "env-narrative"
        assert not hasattr(engine, "_model_routes")

    def test_role_accessors_and_public_info(self):
        engine = _FakeEngine()
        engine.route_resolver = lambda: EffectiveSettings(
            narrative=RoleBinding(mode="default", service=None),
            judgement=parse_role_binding(
                custom_binding(model_id="deepseek-judge"), role_label="裁决模型", allow_private=True
            ),
            revision=4,
        )
        apply_pending_routes(engine)
        assert model_for_role(engine, "adjudication") == "deepseek-judge"
        assert model_for_role(engine, "story") != "deepseek-judge"
        assert client_for_role(engine, "audit") is engine._model_routes.judgement.client
        info = route_public_info(engine, "adjudication")
        assert info["config_revision"] == 4
        assert info["binding_id"].startswith("svc_")
        assert "api_key" not in info and "sk-local-test" not in str(info)

    def test_fallback_accessors_without_routes(self):
        engine = _FakeEngine()
        assert client_for_role(engine, "adjudication") is engine.client
        assert model_for_role(engine, "adjudication") == "env-judgement"
        assert route_public_info(engine, "story") == {}


class TestByokOnly:
    """云端 BYOK-only：缺绑定 fail-closed，绝不触碰平台凭据。"""

    def _byok_settings(self, *, narrative=None, judgement=None, revision=1):
        return EffectiveSettings(
            narrative=narrative or RoleBinding(mode="default", service=None),
            judgement=judgement or RoleBinding(mode="default", service=None),
            revision=revision,
            byok_required=True,
        )

    def test_readiness_error_lists_missing_roles(self):
        from src.ai.model.route_service import readiness_error

        assert readiness_error(default_settings()) is None  # 非 BYOK 不拦
        both_missing = readiness_error(self._byok_settings())
        assert "叙述模型" in both_missing and "裁决模型" in both_missing
        only_judgement = readiness_error(
            self._byok_settings(
                narrative=parse_role_binding(
                    custom_binding(), role_label="叙述模型", allow_private=True
                )
            )
        )
        assert "裁决模型" in only_judgement and "叙述模型" not in only_judgement
        ready = self._byok_settings(
            narrative=parse_role_binding(
                custom_binding(), role_label="叙述模型", allow_private=True
            ),
            judgement=parse_role_binding(
                custom_binding(), role_label="裁决模型", allow_private=True
            ),
        )
        assert readiness_error(ready) is None

    def test_resolve_routes_raises_when_unconfigured(self):
        from src.ai.model.route_service import RouteNotConfiguredError

        with pytest.raises(RouteNotConfiguredError, match="尚未配置"):
            resolve_routes(self._byok_settings())

    def test_apply_pending_routes_fails_closed(self):
        from src.ai.model.route_service import RouteNotConfiguredError

        engine = _FakeEngine()
        engine.route_resolver = lambda: self._byok_settings()
        engine.route_resolver.byok_only = True
        apply_pending_routes(engine)
        assert engine._model_routes is None
        assert "尚未配置" in engine._routes_blocked_error
        # 直接取用与角色取用都必须失败，不能穿透到构造期平台 client
        with pytest.raises(RouteNotConfiguredError):
            client_for_role(engine, "story")
        with pytest.raises(RouteNotConfiguredError):
            model_for_role(engine, "story")
        with pytest.raises(RouteNotConfiguredError):
            engine.client.chat  # noqa: B018 - 属性访问即抛错

    def test_blocked_stream_reports_readable_reason(self):
        """缺配置的流式调用必须给出可读原因，而不是"服务暂时不可用"。"""
        from types import SimpleNamespace

        from src.ai.model.llm_concurrency import ModelResponseError
        from src.app.engine import GameEngine

        engine = GameEngine.__new__(GameEngine)
        engine.route_resolver = lambda: self._byok_settings()
        engine.route_resolver.byok_only = True
        apply_pending_routes(engine)
        engine.messages = []
        errors: list[str] = []
        engine.cb = SimpleNamespace(on_narrative=lambda _text: None, on_error=errors.append)

        with pytest.raises(ModelResponseError, match="尚未配置"):
            engine._stream_llm("blocked-model")
        assert errors and "尚未配置" in errors[0]

    def test_blocked_state_recovers_after_configuration(self):
        engine = _FakeEngine()
        state = {"settings": self._byok_settings()}
        engine.route_resolver = lambda: state["settings"]
        apply_pending_routes(engine)
        assert engine._routes_blocked_error

        state["settings"] = self._byok_settings(
            narrative=parse_role_binding(
                custom_binding(), role_label="叙述模型", allow_private=True
            ),
            judgement=parse_role_binding(
                custom_binding(), role_label="裁决模型", allow_private=True
            ),
            revision=2,
        )
        apply_pending_routes(engine)
        assert engine._routes_blocked_error is None
        assert engine._model_routes is not None
        assert client_for_role(engine, "story") is engine._model_routes.narrative.client

    def test_engine_byok_only_marker(self):
        from src.ai.model.route_service import engine_byok_only

        engine = _FakeEngine()
        assert engine_byok_only(engine) is False
        engine.route_resolver = LocalRouteResolver.__new__(LocalRouteResolver)
        assert engine_byok_only(engine) is False
        resolver = lambda: default_settings()  # noqa: E731
        resolver.byok_only = True
        engine.route_resolver = resolver
        assert engine_byok_only(engine) is True


class TestKeyDestinationBinding:
    """Key 与目的地绑定：更换服务地址必须重新输入 Key。"""

    def _existing(self):
        return parse_role_binding(
            custom_binding(), role_label="叙述模型", allow_private=True
        ).service

    def test_blank_key_same_destination_reuses_saved(self):
        payload = custom_binding(model_id="qwen3:14b")
        payload["service"]["api_key"] = ""
        binding = parse_role_binding(
            payload, role_label="叙述模型", allow_private=True, existing=self._existing()
        )
        assert binding.service.api_key == "sk-local-test"
        assert binding.service.model_id == "qwen3:14b"

    def test_blank_key_changed_destination_rejected(self):
        payload = custom_binding(base_url="http://127.0.0.1:9999/v1")
        payload["service"]["api_key"] = ""
        with pytest.raises(ValueError, match="服务地址已变更"):
            parse_role_binding(
                payload, role_label="叙述模型", allow_private=True, existing=self._existing()
            )

    def test_blank_key_changed_provider_kind_rejected(self):
        payload = custom_binding(provider_kind="deepseek")
        payload["service"]["api_key"] = ""
        with pytest.raises(ValueError, match="服务类型已变更"):
            parse_role_binding(
                payload, role_label="叙述模型", allow_private=True, existing=self._existing()
            )

    def test_blank_key_invalid_provider_kind_reports_kind(self):
        payload = custom_binding(provider_kind="evil")
        payload["service"]["api_key"] = ""
        with pytest.raises(ValueError, match="服务类型必须是"):
            parse_role_binding(
                payload, role_label="叙述模型", allow_private=True, existing=self._existing()
            )

    def test_explicit_new_key_with_new_destination_ok(self):
        payload = custom_binding(base_url="http://127.0.0.1:9999/v1", api_key="sk-new")
        binding = parse_role_binding(
            payload, role_label="叙述模型", allow_private=True, existing=self._existing()
        )
        assert binding.service.api_key == "sk-new"
