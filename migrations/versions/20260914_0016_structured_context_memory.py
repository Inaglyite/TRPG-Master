"""Structured play: interaction threads + character memories (protocol v1).

两张表支撑「结构化上下文与角色长期记忆」：

- ``interaction_threads``：当前交互状态（第 2 层）。跨请求存活的「已讨论/
  已约定目标」记录——稳定目标 ID、尚未执行的行动、已告知条件、等待谁回应、
  关联的请求链。请求进入终态后线程仍可保留；线程只是记录，不是执行授权。
- ``character_memories``：角色长期记忆（第 4 层）。按角色归属的追加式日志，
  带知识类型（亲历/被告知/传闻/推测）、来源引用（事件/命令）与失效关系；
  ``derivation_key`` 让事件派生可幂等补建；``created_revision``/``updated_revision``
  是读档回滚的截止依据。

与既有 ``memory_facts``（0010）的边界：那是 legacy 引擎按 (world, subject,
fact_type) 唯一的「当前事实」影子模型，且耦合 source_turn_id（结构化世界没有
Turn）。本表是结构化世界的追加式角色记忆，不试图接管旧表。

沿用 0008 以来的 adopt-or-create 约定：桌面构建用 Base.metadata.create_all
建表，迁移校验精确形状后 no-op；形状不符 fail closed。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260914_0016"
down_revision = "20260913_0015"
branch_labels = None
depends_on = None

THREADS = "interaction_threads"
MEMORIES = "character_memories"

THREAD_COLUMNS = (
    "id",
    "world_id",
    "thread_id",
    "investigator_id",
    "status",
    "pending_action",
    "disclosed",
    "waiting_on",
    "note",
    "origin_request_id",
    "last_request_id",
    "request_ids",
    "created_revision",
    "updated_revision",
    "created_sequence",
    "created_at",
    "updated_at",
)

MEMORY_COLUMNS = (
    "id",
    "world_id",
    "memory_id",
    "character_id",
    "character_kind",
    "knowledge_type",
    "content",
    "scene_id",
    "subjects",
    "topics",
    "derivation_key",
    "source",
    "status",
    "superseded_by",
    "created_revision",
    "updated_revision",
    "created_sequence",
    "created_at",
    "updated_at",
)

THREAD_UNIQUE = ("uq_interaction_thread_id",)
MEMORY_UNIQUE = ("uq_character_memory_id", "uq_character_memory_derivation")


def _json() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _validate_adopt_shape(
    bind, table: str, columns: tuple[str, ...], uniques: tuple[str, ...]
) -> None:
    inspector = sa.inspect(bind)
    actual_columns = {column["name"] for column in inspector.get_columns(table)}
    missing = sorted(set(columns) - actual_columns)
    if missing:
        raise RuntimeError(f"无法接管已存在的 {table}：缺少列 {', '.join(missing)}")
    extra = sorted(actual_columns - set(columns))
    if extra:
        raise RuntimeError(f"无法接管已存在的 {table}：存在未知列 {', '.join(extra)}")
    actual_unique = {u["name"] for u in inspector.get_unique_constraints(table)}
    missing_unique = sorted(set(uniques) - actual_unique)
    if missing_unique:
        raise RuntimeError(f"无法接管已存在的 {table}：缺少唯一约束 {', '.join(missing_unique)}")


def _create_threads() -> None:
    op.create_table(
        THREADS,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(160), nullable=False),
        sa.Column("investigator_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("pending_action", _json(), nullable=False),
        sa.Column("disclosed", _json(), nullable=False),
        sa.Column("waiting_on", sa.String(160), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("origin_request_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("last_request_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("request_ids", _json(), nullable=False),
        sa.Column("created_revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "thread_id", name="uq_interaction_thread_id"),
    )
    for column in ("world_id", "investigator_id", "status"):
        op.create_index(f"ix_{THREADS}_{column}", THREADS, [column])


def _create_memories() -> None:
    op.create_table(
        MEMORIES,
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column(
            "world_id",
            sa.String(160),
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("memory_id", sa.String(160), nullable=False),
        sa.Column("character_id", sa.String(160), nullable=False),
        sa.Column("character_kind", sa.String(20), nullable=False, server_default="investigator"),
        sa.Column("knowledge_type", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("scene_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("subjects", _json(), nullable=False),
        sa.Column("topics", _json(), nullable=False),
        # 派生幂等键；主持手工记录为 NULL（两种方言里 NULL 在唯一约束下互不相等）。
        sa.Column("derivation_key", sa.String(200), nullable=True),
        sa.Column("source", _json(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("superseded_by", sa.String(160), nullable=False, server_default=""),
        sa.Column("created_revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("world_id", "memory_id", name="uq_character_memory_id"),
        sa.UniqueConstraint("world_id", "derivation_key", name="uq_character_memory_derivation"),
    )
    for column in ("world_id", "character_id", "status", "scene_id"):
        op.create_index(f"ix_{MEMORIES}_{column}", MEMORIES, [column])


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if THREADS in tables:
        _validate_adopt_shape(bind, THREADS, THREAD_COLUMNS, THREAD_UNIQUE)
    else:
        _create_threads()
    if MEMORIES in tables:
        _validate_adopt_shape(bind, MEMORIES, MEMORY_COLUMNS, MEMORY_UNIQUE)
    else:
        _create_memories()


def downgrade() -> None:
    op.drop_table(MEMORIES)
    op.drop_table(THREADS)
