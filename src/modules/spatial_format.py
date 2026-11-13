"""Spatial v1 author contract: finite predicates/effects, no script execution."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
Scalar = bool | int | str


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Always(Strict):
    op: Literal["always"] = "always"
    value: bool = True


class Group(Strict):
    op: Literal["all", "any"]
    conditions: list[Condition] = Field(min_length=1, max_length=16)


class Negation(Strict):
    op: Literal["not"]
    condition: Condition


class Flag(Strict):
    op: Literal["flag"]
    flag_id: Identifier
    value: Scalar


class Item(Strict):
    op: Literal["item"]
    item_id: Identifier
    quantity: int = Field(default=1, ge=1, le=100)
    minimum_states: dict[Identifier, int] = Field(default_factory=dict)


class Clue(Strict):
    op: Literal["clue"]
    clue_id: Identifier


class Clock(Strict):
    op: Literal["clock"]
    clock_id: Identifier
    minimum: int = Field(ge=0, le=100000)


class Completed(Strict):
    op: Literal["completed"]
    point_id: Identifier
    action_id: Identifier


Condition = Annotated[
    Always | Group | Negation | Flag | Item | Clue | Clock | Completed, Field(discriminator="op")
]
Group.model_rebuild()
Negation.model_rebuild()


class GrantClue(Strict):
    kind: Literal["grant_clue"]
    clue_id: Identifier


class GiveItem(Strict):
    kind: Literal["give_item"]
    item_id: Identifier
    quantity: int = Field(default=1, ge=1, le=100)


class AdjustItem(Strict):
    kind: Literal["adjust_item_state"]
    item_id: Identifier
    state: Identifier
    delta: int = Field(ge=-100000, le=100000)


class SetFlag(Strict):
    kind: Literal["set_flag"]
    flag_id: Identifier
    value: Scalar


class RevealScene(Strict):
    kind: Literal["reveal_scene"]
    scene_id: Identifier


class Unlock(Strict):
    kind: Literal["unlock_connection"]
    connection_id: Identifier


class AdvanceClock(Strict):
    kind: Literal["advance_clock"]
    clock_id: Identifier
    amount: int = Field(ge=1, le=100)


Effect = Annotated[
    GrantClue | GiveItem | AdjustItem | SetFlag | RevealScene | Unlock | AdvanceClock,
    Field(discriminator="kind"),
]


class NumericState(Strict):
    initial: int = 0
    minimum: int = 0
    maximum: int = Field(default=100, ge=0, le=100000)

    @model_validator(mode="after")
    def bounded(self):
        if not self.minimum <= self.initial <= self.maximum:
            raise ValueError("物品状态初值不在声明范围内")
        return self


class ItemDefinition(Strict):
    label: str = Field(min_length=1, max_length=160)
    states: dict[Identifier, NumericState] = Field(default_factory=dict)


class Node(Strict):
    kind: Literal["container", "location"] = "location"
    parent_scene_id: Identifier | None = None
    entry_scene_id: Identifier | None = None
    hidden: bool = False


class Connection(Strict):
    source_scene_id: Identifier
    target_scene_id: Identifier
    bidirectional: bool = False
    visible_when: Condition = Field(default_factory=Always)
    requires: Condition = Field(default_factory=Always)
    remember_unlock: bool = False
    costs: list[AdjustItem] = Field(default_factory=list, max_length=8)


class Check(Strict):
    skill: Identifier
    difficulty: Literal["regular", "hard", "extreme"] = "regular"


class Action(Strict):
    intent: Literal["examine", "search", "read", "take", "talk", "enter", "use"]
    aliases: list[str] = Field(default_factory=list, max_length=16)
    requires: Condition = Field(default_factory=Always)
    check: Check | None = None
    repeat: bool = False
    success: list[Effect] = Field(default_factory=list, max_length=16)
    failure: list[Effect] = Field(default_factory=list, max_length=16)


class Point(Strict):
    scene_id: Identifier
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1200)
    visible_when: Condition = Field(default_factory=Always)
    actions: dict[Identifier, Action] = Field(min_length=1, max_length=16)


class SpatialDefinition(Strict):
    version: Literal[1] = 1
    nodes: dict[Identifier, Node] = Field(default_factory=dict, max_length=256)
    connections: dict[Identifier, Connection] = Field(default_factory=dict, max_length=1024)
    points: dict[Identifier, Point] = Field(default_factory=dict, max_length=1024)
    items: dict[Identifier, ItemDefinition] = Field(default_factory=dict, max_length=256)
