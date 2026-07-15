"""Transport-independent application use cases for game lifecycle commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class GameEnginePort(Protocol):
    def reset(self, character_ref: dict | None = None) -> dict | None: ...

    def load(self, slot_id: str | None = None) -> int | None: ...

    def append_control_instruction(self, content: str) -> None: ...

    def handle_action(self, user_content: str | None = None) -> Any: ...

    def rewrite_turn(self, turn_id: str) -> dict: ...

    def save(self, slot_id: str | None = None) -> str: ...

    def list_saves(self) -> list[dict]: ...


class ApplicationUseCaseError(RuntimeError):
    code = "application_error"


class SaveNotFoundError(ApplicationUseCaseError):
    code = "save_not_found"

    def __init__(self, slot_id: str | None):
        self.slot_id = slot_id
        super().__init__("未找到存档")


@dataclass(frozen=True)
class TurnIntent:
    """A validated request ready to enter the turn lifecycle."""

    kind: str
    engine_input: str | None
    player_input: str | None = None
    loaded_message_count: int | None = None
    slot_id: str | None = None


class StartGame:
    def __init__(self, engine: GameEnginePort):
        self.engine = engine

    def execute(self, character_ref: dict | None = None) -> TurnIntent:
        self.engine.reset(character_ref)
        return TurnIntent(kind="opening", engine_input=None)


class ResumeGame:
    RESUME_INSTRUCTION = (
        "继续游戏。之前的存档和世界状态已经恢复。"
        "请基于当前对话历史中的场景、NPC 状态和已发现线索，"
        "用 1-2 句话简述玩家当前位置和情况，然后提供行动选项。"
        "不要从头开场，不要重新介绍世界观。"
    )

    def __init__(self, engine: GameEnginePort):
        self.engine = engine

    def execute(self, slot_id: str | None = None) -> TurnIntent:
        count = self.engine.load(slot_id)
        if count is None:
            raise SaveNotFoundError(slot_id)
        self.engine.append_control_instruction(self.RESUME_INSTRUCTION)
        return TurnIntent(
            kind="resume",
            engine_input=None,
            loaded_message_count=count,
            slot_id=slot_id,
        )


class PerformAction:
    def __init__(self, engine: GameEnginePort):
        self.engine = engine

    def execute(self, content: object) -> TurnIntent:
        normalized = str(content or "").strip()
        if not normalized:
            raise ApplicationUseCaseError("行动内容不能为空")
        return TurnIntent(
            kind="action",
            engine_input=normalized,
            player_input=normalized,
        )


class RewriteTurn:
    def __init__(self, engine: GameEnginePort):
        self.engine = engine

    def execute(self, turn_id: object) -> dict:
        normalized = str(turn_id or "").strip()
        if not normalized:
            raise ApplicationUseCaseError("缺少需要重新叙述的回合 ID")
        return self.engine.rewrite_turn(normalized)


class ManageSaves:
    """Own deterministic slot allocation independently from transports."""

    def __init__(self, engine: GameEnginePort, *, auto_slot: str):
        self.engine = engine
        self.auto_slot = auto_slot

    def save(self, *, manual: bool) -> str:
        return self.engine.save(None if manual else self.auto_slot)

    def create_slot(self) -> str:
        used: set[int] = set()
        for save in self.engine.list_saves():
            slot_id = str(save.get("id") or "")
            if not slot_id.startswith("slot_") or slot_id == self.auto_slot:
                continue
            try:
                used.add(int(slot_id.split("_")[1]))
            except (ValueError, IndexError):
                continue
        number = 1
        while number in used:
            number += 1
        return self.engine.save(f"slot_{number:03d}")


@dataclass(frozen=True)
class GameApplication:
    """Application facade assembled once per engine/session."""

    start_game: StartGame
    resume_game: ResumeGame
    perform_action: PerformAction
    rewrite_turn: RewriteTurn
    manage_saves: ManageSaves

    @classmethod
    def for_engine(
        cls,
        engine: GameEnginePort,
        *,
        auto_slot: str = "slot_000",
    ) -> GameApplication:
        return cls(
            start_game=StartGame(engine),
            resume_game=ResumeGame(engine),
            perform_action=PerformAction(engine),
            rewrite_turn=RewriteTurn(engine),
            manage_saves=ManageSaves(engine, auto_slot=auto_slot),
        )
