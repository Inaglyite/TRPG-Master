"""TRPG 日志 —— 基于 Python logging 模块，零外部依赖。

脱敏在**日志输出边界**统一实施：`RedactingFilter` 装在 handler 上，覆盖文件
与 stderr 两条出口，也覆盖第三方 logger（uvicorn/httpx/sqlalchemy 等）。调用点
仍然应当避免记录载荷本身（提示词、工具结果、请求体），这里是兜底而不是许可。

已知未覆盖的出口：直接 `print(..., file=sys.stderr)` 的调用点必须自行使用
`redact()`（server.py 的异常打印已改）；操作系统级 core dump、被第三方库直接
写入的其他文件描述符不在本模块控制范围内。
"""
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(
    os.environ.get("TRPG_LOG_DIR", Path(__file__).resolve().parents[2] / "logs")
)
LOG_FILE = LOG_DIR / "trpg.log"
MAX_BYTES = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 3

_level = os.environ.get("TRPG_LOG_LEVEL", "INFO").upper()
_initialized = False

# 需要覆盖的第三方 logger：它们自带 handler 且默认不向 root 传播。
_THIRD_PARTY_LOGGERS = (
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "httpcore",
    "openai",
    "sqlalchemy.engine",
)


def _init():
    global _initialized
    if _initialized:
        return
    _initialized = True

    LOG_DIR.mkdir(exist_ok=True)

    logger = logging.getLogger("trpg")
    logger.setLevel(getattr(logging, _level, logging.INFO))
    # 第三方库可能用 logging.config 整体停用已存在的日志器（例如 Alembic 的
    # fileConfig 默认 disable_existing_loggers=True）。应用要求记录日志时恢复。
    logger.disabled = False

    # 避免重复添加 handler
    if logger.handlers:
        install_log_redaction()
        return

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%m-%d %H:%M:%S"
    )

    fh = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(RedactingFilter())
    logger.addHandler(fh)

    # 开发模式下同时输出到 stderr
    if os.environ.get("TRPG_LOG_STDERR"):
        import sys
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.addFilter(RedactingFilter())
        logger.addHandler(sh)

    install_log_redaction()


def get():
    _init()
    return logging.getLogger("trpg")


# 供应商异常正文、请求头转储与审计记录都可能回显凭据；按形状统一脱敏，
# 不依赖每个调用点自己清洗。只识别形状、不读取真实密钥做比对。
_SECRET_PATTERNS = (
    # 常见供应商 Key 前缀（OpenAI/Stripe/Google/xAI/HuggingFace/GitHub/GitLab…）。
    re.compile(r"(?i)\b(?:sk|rk|pk|gsk|xai|hf|r8|glpat|ghp|gho|ghs|ghr|github_pat)[-_][A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # JWT / 会话令牌。
    re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}"),
    # Authorization: Bearer/Basic <token>（要求 token 里至少一个非字母，避免误伤英文散文）。
    re.compile(
        r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]*[0-9._~+/=\-][A-Za-z0-9._~+/=\-]{5,}"
    ),
    # 头部/配置形态：Authorization、Cookie、api_key、本地连接凭证。
    re.compile(
        r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key"
        r"|api[_-]?key|apikey|x-trpg-local-token|trpg_local_launch_token)\b"
        r"[\"']?\s*[:=]\s*[\"']?[^\s\"',;}\]]{4,}"
    ),
)
_REDACTION = "***"


def redact(text: str) -> str:
    """把文本里形似凭据的片段替换为 ***；非凭据内容原样保留。"""
    scrubbed = str(text)
    for pattern in _SECRET_PATTERNS:
        scrubbed = pattern.sub(_REDACTION, scrubbed)
    return scrubbed


class RedactingFilter(logging.Filter):
    """在 handler 出口对整条记录脱敏：消息、格式化参数与异常堆栈。

    记录里的错误类别、状态码、请求 ID 等非凭据信息原样保留。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # 记录本身坏了也不能让日志链路抛错
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = redact(
                    logging.Formatter().formatException(record.exc_info)
                )
            except Exception:
                record.exc_text = "***（异常堆栈脱敏失败，已省略）***"
        if record.stack_info:
            record.stack_info = redact(str(record.stack_info))
        return True


def install_log_redaction() -> None:
    """把脱敏过滤器挂到 root 与常见第三方 logger 上（幂等）。"""
    filter_instance = RedactingFilter()
    targets = [
        logging.getLogger(),
        logging.getLogger("trpg"),
        *(logging.getLogger(name) for name in _THIRD_PARTY_LOGGERS),
    ]
    for logger in targets:
        if not any(isinstance(item, RedactingFilter) for item in logger.filters):
            logger.addFilter(filter_instance)
        for handler in logger.handlers:
            if not any(isinstance(item, RedactingFilter) for item in handler.filters):
                handler.addFilter(filter_instance)


def tool(name: str, args: dict, result_summary: str = ""):
    msg = f"TOOL  {name} | {_brief(args)}"
    if result_summary:
        msg += f" → {result_summary}"
    get().info(redact(msg))


def san(pc_name: str, before: int, after: int, loss: int, trigger: str):
    get().info(f"SAN   {pc_name} {before}→{after} (-{loss}) | {trigger}")


def error(msg: str):
    get().error(redact(f"ERROR {msg}"))


def summary_event(model: str, result: str):
    get().info(redact(f"SUM   {model} | {result}"))


def tier_inject(round_num: int):
    get().info(f"TIER  注入 | 第{round_num}轮")


def game_event(event: str):
    get().info(redact(f"GAME  {event}"))


def model_call(
    model: str,
    role: str,
    elapsed: float,
    first_token: float | None,
    finish: str | None,
    tools: int,
    *,
    usage: dict | None = None,
    system_chars: int | None = None,
    tool_schema_chars: int | None = None,
    prompt_profile: str | None = None,
    thinking_mode: str | None = None,
):
    ttft = f"{first_token:.2f}s" if first_token is not None else "-"
    parts = [
        f"MODEL {role} | {model}",
        f"total={elapsed:.2f}s",
        f"first={ttft}",
        f"finish={finish or '-'}",
        f"tools={tools}",
    ]
    if prompt_profile:
        parts.append(f"prompt={prompt_profile}")
    if thinking_mode:
        parts.append(f"thinking={thinking_mode}")
    if system_chars is not None:
        parts.append(f"system_chars={system_chars}")
    if tool_schema_chars is not None:
        parts.append(f"tool_chars={tool_schema_chars}")
    if usage:
        prompt_tokens = usage.get("prompt_tokens")
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        if prompt_tokens is not None:
            parts.append(f"input_tokens={prompt_tokens}")
        if hit is not None:
            parts.append(f"cache_hit={hit}")
        if miss is not None:
            parts.append(f"cache_miss={miss}")
        if isinstance(hit, int) and isinstance(miss, int) and hit + miss:
            parts.append(f"cache_rate={hit / (hit + miss):.1%}")
    get().info(redact(" | ".join(parts)))


def _brief(d: dict) -> str:
    """Return metadata-only tool arguments for ordinary logs.

    Tool arguments may contain hidden NPC facts or player-private notes.  Full
    values belong in the durable, access-controlled turn record when needed,
    not the rotating operational log.
    """
    parts = []
    for k, v in d.items():
        if isinstance(v, bool | int | float):
            rendered = repr(v)
        elif isinstance(v, str):
            rendered = f"<str:{len(v)}>"
        elif isinstance(v, list):
            rendered = f"<list:{len(v)}>"
        elif isinstance(v, dict):
            rendered = f"<object:{len(v)}>"
        else:
            rendered = f"<{type(v).__name__}>"
        parts.append(f"{k}={rendered}")
    return " | ".join(parts[:4])
