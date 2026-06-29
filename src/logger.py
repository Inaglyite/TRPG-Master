"""TRPG 日志 —— 基于 Python logging 模块，零外部依赖。"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
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


def _brief(d: dict) -> str:
    """压缩 dict 为一行的简短描述。"""
    parts = []
    for k, v in d.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return " | ".join(parts[:4])
