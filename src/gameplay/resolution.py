"""Private, durable action plans and independently reproducible random draws.

The seed is stored outside WorldState/public turn records. A failed attempt
against the same snapshot reuses its plan and randomness; a committed action
advances WorldState and therefore creates a new resolution on the next input.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from src.storage.database import AuditEvent, World, session_scope


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class ResolutionSession:
    id: str
    seed: str = field(repr=False)
    context: Any = field(repr=False)
    plan: dict | None = None

    def freeze_plan(self, plan: dict) -> dict:
        if self.plan is not None:
            return copy.deepcopy(self.plan)
        db_url = getattr(self.context, "database_url", None)
        if db_url:
            with session_scope(db_url) as db:
                db.scalar(select(World).where(World.id == self.context.world_id).with_for_update())
                key = f"rp_{self.id}"
                row = db.get(AuditEvent, key)
                if row is None:
                    db.add(AuditEvent(id=key, world_id=self.context.world_id, event_type="resolution.plan", success=True, details={"plan": copy.deepcopy(plan)}))
                else:
                    plan = row.details["plan"]
        self.plan = copy.deepcopy(plan)
        return copy.deepcopy(plan)


_CURRENT: ContextVar[ResolutionSession | None] = ContextVar("rule_resolution", default=None)


def current_resolution() -> ResolutionSession | None:
    return _CURRENT.get()


def resolution_rng(domain: str, identity: Any = "") -> Any:
    current = _CURRENT.get()
    if current is None:
        return random
    return random.Random(_digest([current.seed, domain, identity]))


def begin_resolution(context: Any, world: dict, player_input: str | None) -> ResolutionSession:
    resolution_id = _digest([getattr(context, "world_id", "memory"), world, player_input])[:40]
    seed = secrets.token_hex(32)
    plan = None
    db_url = getattr(context, "database_url", None)
    if db_url:
        with session_scope(db_url) as db:
            db.scalar(select(World).where(World.id == context.world_id).with_for_update())
            key = f"rs_{resolution_id}"
            row = db.get(AuditEvent, key)
            if row is None:
                db.add(AuditEvent(id=key, world_id=context.world_id, event_type="resolution.started", success=True, details={"seed": seed}))
            else:
                seed = row.details["seed"]
            plan_row = db.get(AuditEvent, f"rp_{resolution_id}")
            if plan_row is not None:
                plan = copy.deepcopy(plan_row.details["plan"])
    return ResolutionSession(resolution_id, seed, context, plan)


@contextmanager
def resolution_scope(session: ResolutionSession):
    token = _CURRENT.set(session)
    try:
        yield session
    finally:
        _CURRENT.reset(token)
