"""日志脱敏边界：消息、格式化参数、嵌套数据、异常堆栈与多条输出出口。"""

from __future__ import annotations

import io
import json
import logging

import pytest

from src.app.logger import RedactingFilter, install_log_redaction, redact


@pytest.fixture(autouse=True)
def _install_redaction():
    """与应用启动一致：过滤器装在 logger 与 handler 两级。"""
    install_log_redaction()

FAKE_KEY = "sk-fake-abcdef1234567890"
FAKE_ANT_KEY = "sk-ant-fake-0987654321"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.abcdefghijklmnop"
FAKE_COOKIE = "trpg_session=fake-session-value"
FAKE_LOCAL_TOKEN = "fake-local-launch-token"


def test_sk_tokens_are_scrubbed():
    assert FAKE_KEY not in redact(f"auth failed for {FAKE_KEY}")
    assert FAKE_ANT_KEY not in redact(f"anthropic key {FAKE_ANT_KEY}")


def test_authorization_cookie_and_jwt_are_scrubbed():
    assert "fake-session-value" not in redact(f"Cookie: {FAKE_COOKIE}")
    assert FAKE_JWT not in redact(f"Authorization: Bearer {FAKE_JWT}")
    assert "fake-local-launch-token" not in redact(
        f"X-TRPG-Local-Token: {FAKE_LOCAL_TOKEN}"
    )


def test_api_key_assignments_are_scrubbed():
    scrubbed = redact('{"api_key": "super-secret-value", "model": "e2e-model"}')
    assert "super-secret-value" not in scrubbed
    assert "e2e-model" in scrubbed


def test_non_credential_diagnostics_survive():
    """错误类别、状态码与请求 ID 必须保留，否则排障失去意义。"""
    text = "ERROR 模型调用失败 class=auth status=401 request_id=req_abc123 model=gpt-x"
    assert redact(text) == text


def test_plain_story_text_is_untouched():
    text = "雨幕笼罩着阿卡姆，调查员翻开笔记本。"
    assert redact(text) == text


def _capture(logger_name: str):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    return logger, handler, stream


def test_filter_redacts_message_and_format_args():
    logger, handler, stream = _capture("trpg")
    try:
        logger.error("token %s rejected (status=%s)", FAKE_KEY, 401)
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert FAKE_KEY not in output
    assert "status=401" in output


def test_filter_redacts_nested_payload():
    logger, handler, stream = _capture("trpg")
    payload = {"headers": {"Authorization": f"Bearer {FAKE_KEY}"}, "model": "x"}
    try:
        logger.warning("request failed: %s", json.dumps(payload))
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert FAKE_KEY not in output
    assert '"model": "x"' in output


def test_filter_redacts_exception_stack():
    logger, handler, stream = _capture("trpg")
    try:
        raise RuntimeError(f"provider rejected key {FAKE_KEY}")
    except RuntimeError:
        try:
            logger.error("调用失败", exc_info=True)
        finally:
            logger.removeHandler(handler)
    output = stream.getvalue()
    assert FAKE_KEY not in output
    assert "RuntimeError" in output


def test_filter_covers_third_party_loggers():
    logger, handler, stream = _capture("uvicorn.access")
    try:
        logger.warning('127.0.0.1 - "GET /x?token=%s" 200', FAKE_KEY)
    finally:
        logger.removeHandler(handler)
    assert FAKE_KEY not in stream.getvalue()


def test_every_trpg_handler_has_the_filter():
    import src.app.logger as logger_module

    logger_module.get()  # 确保 handler 已初始化
    logger = logging.getLogger("trpg")
    assert logger.handlers
    for handler in logger.handlers:
        assert any(isinstance(item, RedactingFilter) for item in handler.filters)


def test_install_is_idempotent():
    install_log_redaction()
    install_log_redaction()
    logger = logging.getLogger("trpg")
    assert sum(isinstance(item, RedactingFilter) for item in logger.filters) == 1


def test_filter_survives_unformattable_record():
    """记录本身参数不匹配时，过滤器不得抛出（日志链路不能因脱敏而断）。"""
    record = logging.LogRecord(
        "trpg", logging.ERROR, __file__, 1, "bad %s %s", (FAKE_KEY,), None
    )
    assert RedactingFilter().filter(record) is True
