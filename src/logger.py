"""TRPG 日志 —— 基于 Python logging 模块，零外部依赖。"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(
    os.environ.get("TRPG_LOG_DIR", Path(__file__).resolve().parent.parent / "logs")
)
LOG_FILE = LOG_DIR / "trpg.log"
MAX_BYTES = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 3

_level = os.environ.get("TRPG_LOG_LEVEL", "INFO").upper()
_initialized = False


def _init():
    global _initialized
    if _initialized:
        return
    _initialized = True

    LOG_DIR.mkdir(exist_ok=True)

    logger = logging.getLogger("trpg")
    logger.setLevel(getattr(logging, _level, logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%m-%d %H:%M:%S"
    )

    fh = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # 开发模式下同时输出到 stderr
    if os.environ.get("TRPG_LOG_STDERR"):
        import sys
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)


def get():
    _init()
    return logging.getLogger("trpg")


def tool(name: str, args: dict, result_summary: str = ""):
    msg = f"TOOL  {name} | {_brief(args)}"
    if result_summary:
        msg += f" → {result_summary}"
    get().info(msg)


def san(pc_name: str, before: int, after: int, loss: int, trigger: str):
    get().info(f"SAN   {pc_name} {before}→{after} (-{loss}) | {trigger}")


def error(msg: str):
    get().error(f"ERROR {msg}")


def summary_event(model: str, result: str):
    get().info(f"SUM   {model} | {result}")


def tier_inject(round_num: int):
    get().info(f"TIER  注入 | 第{round_num}轮")


def game_event(event: str):
    get().info(f"GAME  {event}")


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
    get().info(" | ".join(parts))


def _brief(d: dict) -> str:
    """压缩 dict 为一行的简短描述。"""
    parts = []
    for k, v in d.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return " | ".join(parts[:4])
