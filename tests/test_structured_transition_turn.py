"""守秘人主持的叙事型过渡回合（structured_v1 的结构化运行时协议）。

覆盖产品目标：「表达意愿 → 正常叙事过渡 → 追问 → 决定 → 执行」，
以及必须反向成立的性质：

1. 意愿不执行：`我想先看看尸体` 只得到叙事与持久待办，位置不变。
2. 等待是真的停止：本轮 runner 结束且不后台续跑；待办不是指令，后续运行不会自动执行。
3. 待办可追问/可改主意/可坚持：下一轮能看到「原想做什么、已告知什么、上一轮说了什么」。
4. 执行前重核：只有主持在新一轮发出的命令才改变世界；执行后旧待办收尾，不会二次触发。
5. 隐私与幂等：待办只对本人与主持可见；重复运行不重复挂起。

模型全部为脚本化调用者（fake caller）——真实模型验收需单独授权，见交付记录。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.storage.database import EventOutbox, PlayerRequest, session_scope
from src.storage.database_store import DatabaseWorldStore
from src.structured.agent import KeeperAgentRunner
from src.structured.errors import StructuredError
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal, take_control
from src.structured.service import StructuredPlayService


class _ScriptedCaller:
    """按脚本逐次返回模型输出，并记录每次收到的上下文。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append(user)
        if not self._script:
            raise RuntimeError("脚本耗尽")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item  # 脚本里放异常即为「这一次模型调用失败」
        return item


def _decision(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


class TransitionTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url
        self.service = StructuredPlayService(self.db_url)
        self.alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
        self.bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
        self.keeper = Principal(kind="keeper", user_id="u-keeper")
        self.agent = Principal(kind="agent", run_id="run-test")

    def tearDown(self):
        self._temp.cleanup()

    # ---------------------------------------------------------------- 工具

    def state(self) -> dict:
        return DatabaseWorldStore(self.db_url, "sp-world", self.context.world_dir).snapshot().state

    def scene_id(self) -> str:
        return str((self.state().get("current_scene") or {}).get("id") or "")

    def request_row(self, request_id: str) -> PlayerRequest:
        with session_scope(self.db_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_id == request_id,
                )
            ).scalar_one()
            return row

    def submit(self, request_id: str, action: dict, principal: Principal | None = None) -> None:
        self.service.submit_action_request(
            world_id="sp-world",
            principal=principal or self.alice,
            request={
                "request_id": request_id,
                "investigator_id": (principal or self.alice).investigator_ids[0],
                "action": action,
            },
        )

    def events(self, outbox: list[dict]) -> list[tuple[str, dict]]:
        return [(e["type"], e["payload"]) for e in outbox]

    async def _run(self, caller, trigger: str, box: list[dict]):
        runner = KeeperAgentRunner(self.db_url, caller=caller)
        return await runner.run(
            world_id="sp-world",
            trigger_request_id=trigger,
            deliver=_deliver(box),
        )

    # ------------------------------------------------- 1) 意愿不执行 + 挂待办

    async def test_intent_narrates_and_parks_without_moving(self):
        """玩家说想去看遗体：只得到过渡叙事与持久待办，仍在原场景。"""
        start_scene = self.scene_id()
        self.submit("req-1", {"kind": "freeform", "text": "说实话，我想先看看莱特教授的尸体。"})
        box: list[dict] = []
        caller = _ScriptedCaller(
            [
                _decision(
                    assessment="玩家表达意愿；法伦会先谈停尸房的接待安排",
                    narration="法伦放下雪茄：“停尸房那边得先跟值班医生打个招呼，否则他们不会放人进去。”",
                    wait_for_player=True,
                    awaiting={
                        "pending_action": {
                            "kind": "move",
                            "destination_scene_id": "library",
                            "note": "尚未出发前往停尸房",
                        },
                        "disclosed": ["停尸房需要值班医生放行"],
                        "note": "等待玩家决定是否现在联系医生",
                    },
                    stop_reason="wait_player",
                )
            ]
        )
        result = await self._run(caller, "req-1", box)

        self.assertEqual("wait_player", result.stop_reason)
        self.assertTrue(result.awaiting_parked)
        self.assertEqual(1, result.model_calls)
        self.assertEqual(start_scene, self.scene_id(), "意愿回合不得移动位置")

        row = self.request_row("req-1")
        self.assertEqual("awaiting_player", row.status)
        awaiting = (row.payload or {}).get("awaiting") or {}
        self.assertEqual("move", awaiting["pending_action"]["kind"])
        self.assertEqual("library", awaiting["pending_action"]["destination_scene_id"])
        self.assertEqual(["停尸房需要值班医生放行"], awaiting["disclosed"])

        types = self.events(box)
        self.assertIn(("message_completed", None), [(t, None) for t, _ in types])
        statuses = [
            p
            for t, p in types
            if t == "action_status" and p.get("request_id") == "req-1"
        ]
        self.assertEqual(["awaiting_player"], [p["status"] for p in statuses])
        self.assertEqual("move", statuses[-1]["awaiting"]["pending_action"]["kind"])

    # --------------------------------------------- 2) 追问：看到待办与上文

    async def test_follow_up_reply_context_carries_todo_and_transcript(self):
        """玩家追问时，主持上下文里有：待办（未执行项/已告知）与上一轮对白。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看莱特教授的尸体。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="先谈接待安排",
                        narration="法伦说停尸房需要值班医生放行。",
                        wait_for_player=True,
                        awaiting={
                            "pending_action": {"kind": "move", "note": "尚未出发"},
                            "disclosed": ["停尸房需要值班医生放行"],
                        },
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )

        self.submit("req-2", {"kind": "freeform", "text": "那他和我们熟吗？"})
        caller = _ScriptedCaller(
            [
                _decision(
                    assessment="继续交谈",
                    narration="“不算熟，但他认我的名片。”",
                    wait_for_player=True,
                    awaiting={"pending_action": {"kind": "move", "note": "仍未出发"}},
                    stop_reason="wait_player",
                )
            ]
        )
        await self._run(caller, "req-2", [])

        context = json.loads(caller.calls[-1])
        pending = {item["request_id"]: item for item in context["pending_requests"]}
        self.assertIn("req-1", pending)
        self.assertEqual("awaiting_player", pending["req-1"]["status"])
        self.assertEqual(
            "move", pending["req-1"]["deferred_player_intent"]["pending_action"]["kind"]
        )
        self.assertEqual(
            ["停尸房需要值班医生放行"],
            pending["req-1"]["deferred_player_intent"]["disclosed"],
        )
        # 待办必须被明确标成「不是执行授权」，不能被读成已批准的任务
        self.assertFalse(pending["req-1"]["deferred_player_intent_is_authorization"])
        # 上一轮的主持对白（叙事）在上下文里，追问才接得上
        texts = [m["text"] for m in context["recent_public_messages"]]
        self.assertTrue(any("值班医生放行" in text for text in texts), texts)
        self.assertEqual("awaiting_player", self.request_row("req-1").status)

    # --------------------------------------- 3) 决定后执行：位置才改变

    async def test_player_decides_then_keeper_executes_once(self):
        """玩家明确坚持后，主持执行移动：位置改变且旧待办收尾，不会二次触发。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="先谈安排",
                        narration="法伦提醒停尸房要医生放行。",
                        wait_for_player=True,
                        awaiting={"pending_action": {"kind": "move", "note": "尚未出发"}},
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )
        start_scene = self.scene_id()

        self.submit("req-2", {"kind": "freeform", "text": "那麻烦你联系一下，我现在过去。"})
        box: list[dict] = []
        result = await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="玩家坚持，联系值班医生并出发",
                        commands=[
                            {
                                "kind": "publish_message",
                                "payload": {
                                    "speaker": {"kind": "npc", "id": "fallon"},
                                    "audience": {"kind": "public"},
                                    "text": "“我打过电话了，医生在等你们。”",
                                },
                            },
                            {
                                "kind": "move_party",
                                "payload": {"destination_scene_id": "library", "travel_minutes": 15},
                            },
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-1",
                                    "resolution": "completed",
                                    "outcome": "success",
                                },
                            },
                        ],
                        narration="你们动身前往停尸房。",
                        stop_reason="done",
                    )
                ]
            ),
            "req-2",
            box,
        )

        self.assertNotEqual(start_scene, self.scene_id(), "明确坚持后才允许移动")
        self.assertEqual("completed", self.request_row("req-1").status)
        # 执行后不再保留待办指纹：旧待办不可能日后再次执行
        self.assertNotIn("awaiting", self.request_row("req-1").payload or {})
        self.assertGreaterEqual(result.commands_committed, 3)

    # ------------------------------------------ 4) 待办不是指令：不自动执行

    async def test_pending_todo_never_auto_executes(self):
        """挂着待办时，一次纯叙事的运行不得移动；待办仍在等玩家。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="先谈安排",
                        narration="法伦提醒停尸房要医生放行。",
                        wait_for_player=True,
                        awaiting={
                            "pending_action": {
                                "kind": "move",
                                "destination_scene_id": "library",
                                "note": "尚未出发",
                            }
                        },
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )
        start_scene = self.scene_id()

        self.submit("req-2", {"kind": "freeform", "text": "嗯，我再想想。"})
        result = await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="玩家还在犹豫，不动",
                        narration="法伦点点头，把烟按灭在烟灰缸里。",
                        stop_reason="done",
                    )
                ]
            ),
            "req-2",
            [],
        )

        self.assertEqual(start_scene, self.scene_id(), "待办未被主持执行前不得移动")
        self.assertFalse(result.awaiting_parked)
        self.assertEqual("awaiting_player", self.request_row("req-1").status)

    # --------------------------------------------- 5) 改主意：替换旧待办

    async def test_change_of_mind_replaces_todo(self):
        """玩家改主意：旧待办被收尾，且绝不会在之后被执行。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先去图书馆。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="挂起前往图书馆的意愿",
                        narration="老看守说现在闭馆，得先打招呼。",
                        wait_for_player=True,
                        awaiting={
                            "pending_action": {
                                "kind": "move",
                                "destination_scene_id": "library",
                                "note": "尚未前往图书馆",
                            },
                            "disclosed": ["现在闭馆需要先打招呼"],
                        },
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )
        start_scene = self.scene_id()
        self.assertEqual("awaiting_player", self.request_row("req-1").status)

        self.submit("req-2", {"kind": "freeform", "text": "那算了，我改主意了，先在这儿看看书。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="玩家改主意，取消旧待办",
                        commands=[
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-1",
                                    "resolution": "cancelled",
                                    "note": "玩家改主意，不去图书馆了",
                                },
                            },
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-2",
                                    "resolution": "completed",
                                    "note": "留在书房翻书",
                                },
                            },
                        ],
                        narration="你留在书房翻看那些书。",
                        stop_reason="done",
                    )
                ]
            ),
            "req-2",
            [],
        )

        self.assertEqual(start_scene, self.scene_id(), "改主意后不得执行旧待办的移动")
        row = self.request_row("req-1")
        self.assertEqual("cancelled", row.status)
        self.assertNotIn("awaiting", row.payload or {}, "旧待办指纹必须被清掉")
        self.assertEqual("completed", self.request_row("req-2").status)

    # ------------------------------------ 6) 等待之后的命令不执行（结构保证）

    async def test_commands_after_await_are_not_executed(self):
        """主持把等待写进命令列表时，其后的命令一律不执行。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        start_scene = self.scene_id()
        box: list[dict] = []
        result = await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="先谈安排，不出发",
                        commands=[
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-1",
                                    "resolution": "awaiting_player",
                                    "pending_action": {
                                        "kind": "move",
                                        "destination_scene_id": "library",
                                        "note": "尚未出发",
                                    },
                                    "disclosed": ["需要医生放行"],
                                },
                            },
                            {
                                "kind": "move_party",
                                "payload": {"destination_scene_id": "library", "travel_minutes": 15},
                            },
                        ],
                        narration="法伦说先联系医生。",
                        wait_for_player=True,
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            box,
        )

        self.assertTrue(result.awaiting_parked)
        self.assertEqual(start_scene, self.scene_id(), "等待之后的移动命令不得执行")
        self.assertNotIn("scene_changed", [t for t, _ in self.events(box)])

    # ------------------------------------------- 7) 已终态请求不再挂起

    async def test_completed_intent_is_not_re_parked(self):
        """主持已把请求收尾为 completed 时，等待声明不会把它再挂起。"""
        self.submit("req-1", {"kind": "move", "destination_scene_id": "library"})
        result = await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="直接抵达",
                        commands=[
                            {
                                "kind": "move_party",
                                "payload": {"destination_scene_id": "library", "travel_minutes": 10},
                            },
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-1",
                                    "resolution": "completed",
                                    "outcome": "success",
                                },
                            },
                        ],
                        narration="你们走进图书馆。",
                        wait_for_player=True,
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )
        self.assertFalse(result.awaiting_parked)
        row = self.request_row("req-1")
        self.assertEqual("completed", row.status)
        self.assertNotIn("awaiting", row.payload or {})

    # ------------------------------- 7.5) 普通明确移动不强制多问一轮

    async def test_plain_clear_move_executes_without_extra_confirmation(self):
        """普通且无重要未告知条件的明确移动：一轮内直接执行，不产生等待待办。"""
        self.submit("req-1", {"kind": "move", "destination_scene_id": "library"})
        start_scene = self.scene_id()
        result = await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="明确前往且无障碍",
                        commands=[
                            {
                                "kind": "move_party",
                                "payload": {
                                    "destination_scene_id": "library",
                                    "travel_minutes": 10,
                                },
                            },
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-1",
                                    "resolution": "completed",
                                    "outcome": "success",
                                },
                            },
                        ],
                        narration="你们推开图书馆的门。",
                        stop_reason="done",
                    )
                ]
            ),
            "req-1",
            [],
        )
        self.assertNotEqual(start_scene, self.scene_id())
        self.assertFalse(result.awaiting_parked, "普通明确移动不该多出一个等待回合")
        self.assertEqual(1, result.model_calls)
        row = self.request_row("req-1")
        self.assertEqual("completed", row.status)
        self.assertNotIn("awaiting", row.payload or {})

    async def test_arrival_grants_nothing_by_itself(self):
        """抵达 ≠ 获准接见 ≠ 取得线索：移动只改位置，不发线索、不涨知识。"""
        before = self.state()
        clues_before = list((before.get("clues_found") or {}).keys())
        self.submit("req-1", {"kind": "move", "destination_scene_id": "library"})
        box: list[dict] = []
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="前往图书馆",
                        commands=[
                            {
                                "kind": "move_party",
                                "payload": {
                                    "destination_scene_id": "library",
                                    "travel_minutes": 10,
                                },
                            },
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-1",
                                    "resolution": "completed",
                                    "outcome": "success",
                                },
                            },
                        ],
                        narration="你们到了图书馆门口。",
                        stop_reason="done",
                    )
                ]
            ),
            "req-1",
            box,
        )
        after = self.state()
        self.assertEqual("library", (after.get("current_scene") or {}).get("id"))
        self.assertEqual(clues_before, list((after.get("clues_found") or {}).keys()))
        event_types = [t for t, _ in self.events(box)]
        self.assertNotIn("clue_granted", event_types)
        self.assertNotIn("handout_presented", event_types)

    async def test_model_failure_after_committed_fact_keeps_facts_and_todo(self):
        """过渡中已提交的叙事保留；模型失败后未执行的行动仍然没有发生。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="先谈安排",
                        commands=[
                            {
                                "kind": "publish_message",
                                "payload": {
                                    "speaker": {"kind": "npc", "id": "fallon"},
                                    "audience": {"kind": "public"},
                                    "text": "“停尸房得先联系值班医生。”",
                                },
                            }
                        ],
                        narration="法伦提醒要先联系医生。",
                        wait_for_player=True,
                        awaiting={"pending_action": {"kind": "move", "note": "尚未出发"}},
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )
        scene_before = self.scene_id()

        self.submit("req-2", {"kind": "freeform", "text": "医生这会儿在吗？"})
        result = await self._run(
            _ScriptedCaller([RuntimeError("模型不可用")]), "req-2", []
        )
        self.assertEqual("paused", result.status)
        self.assertTrue(result.stop_reason.startswith("model_error"))

        with session_scope(self.db_url) as session:
            texts = [
                str((row.payload or {}).get("text") or "")
                for row in session.execute(
                    select(EventOutbox).where(
                        EventOutbox.world_id == "sp-world",
                        EventOutbox.event_type == "message_completed",
                    )
                ).scalars()
            ]
        self.assertTrue(any("值班医生" in text for text in texts), texts)
        # 待办保留给主持接管，未执行的行动没有发生，位置不变。
        self.assertEqual("awaiting_player", self.request_row("req-1").status)
        self.assertEqual(scene_before, self.scene_id())
        self.assertEqual("paused", self.request_row("req-2").status)

    async def test_empty_model_output_pauses_once_with_actionable_reason(self):
        """模型输出为空（预算被推理耗尽）：立即 paused，不拿同样预算白重试。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        scene_before = self.scene_id()
        caller = _ScriptedCaller(["", "", ""])
        result = await self._run(caller, "req-1", [])
        self.assertEqual("paused", result.status)
        self.assertEqual("model_output_empty", result.stop_reason)
        self.assertEqual(1, result.model_calls, "空输出不该再重试同样长度的调用")
        self.assertEqual(scene_before, self.scene_id())
        row = self.request_row("req-1")
        self.assertEqual("paused", row.status)
        self.assertIn("输出", row.detail)

    # ------------------------- 7.6) 与检定、人工接管的关系

    async def test_check_does_not_disturb_awaiting_todo(self):
        """等待期间主持请求检定：检定独立待办，等待中的待办不被顶掉。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="挂起意愿",
                        narration="法伦说需要先联系医生。",
                        wait_for_player=True,
                        awaiting={"pending_action": {"kind": "move", "note": "尚未出发"}},
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )
        self.submit("req-2", {"kind": "freeform", "text": "那我先问问他医生的脾气。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="要求一次心理学检定",
                        commands=[
                            {
                                "kind": "request_check",
                                "payload": {
                                    "investigator_id": "inv-alice",
                                    "skill": "侦查",
                                    "difficulty": "regular",
                                    "attempt": "观察法伦的表情",
                                    "visibility": "public",
                                },
                            },
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-2",
                                    "resolution": "awaiting_player",
                                    "pending_action": {"kind": "freeform", "note": "等掷骰后再答"},
                                },
                            },
                        ],
                        narration="“你看出他在犹豫。”",
                        wait_for_player=True,
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-2",
            [],
        )
        # 两条待办各自独立：原待办的「尚未执行」没有被检定或新请求覆盖。
        row = self.request_row("req-1")
        self.assertEqual("awaiting_player", row.status)
        self.assertEqual("尚未出发", row.payload["awaiting"]["pending_action"]["note"])
        self.assertEqual("awaiting_player", self.request_row("req-2").status)

    async def test_human_takeover_stops_agent_and_keeps_todo(self):
        """人工接管后 agent 不再继续执行；等待中的待办保留给人类主持处理。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="挂起意愿",
                        narration="法伦说需要先联系医生。",
                        wait_for_player=True,
                        awaiting={"pending_action": {"kind": "move", "note": "尚未出发"}},
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )
        start_scene = self.scene_id()
        with session_scope(self.db_url) as session:
            take_control(session, "sp-world", self.keeper)
        self.submit("req-2", {"kind": "freeform", "text": "走吧。"})
        result = await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="不该轮到我",
                        commands=[
                            {
                                "kind": "move_party",
                                "payload": {"destination_scene_id": "library", "travel_minutes": 10},
                            }
                        ],
                        narration="不该出现的旁白",
                        stop_reason="done",
                    )
                ]
            ),
            "req-2",
            [],
        )
        self.assertEqual("blocked", result.status)
        self.assertEqual("human_in_control", result.stop_reason)
        self.assertEqual(0, result.commands_committed)
        self.assertEqual(start_scene, self.scene_id(), "接管后旧 agent 不得继续执行")
        self.assertEqual("awaiting_player", self.request_row("req-1").status)

    # ---------------- 7.7) 真实模型实测暴露的两处健壮性问题

    def test_string_audience_is_rejected_before_it_poisons_outbox(self):
        """模型把 audience 写成字符串时必须在写入前拒绝（否则投递/快照读取会崩）。"""
        db_url = self.db_url
        with self.assertRaises(StructuredError) as ctx:
            self.service.execute_command(
                world_id="sp-world",
                principal=self.keeper,
                kind="publish_message",
                payload={
                    "speaker": {"kind": "keeper"},
                    "audience": "public",  # 模型可能的写法
                    "text": "不该落库",
                },
                command_id="cmd-bad-audience",
                expected_revision=None,
            )
        self.assertEqual("invalid_action", ctx.exception.code)
        with session_scope(db_url) as session:
            rows = session.execute(
                select(EventOutbox).where(EventOutbox.world_id == "sp-world")
            ).scalars()
            self.assertEqual([], [r for r in rows if r.event_type == "message_completed"])

    def test_agent_context_tolerates_legacy_string_audience_rows(self):
        """历史/异常写入的字符串 audience 只跳过，不该让整轮 agent 运行崩掉。"""
        from src.structured.agent import KeeperAgentRunner

        with session_scope(self.db_url) as session:
            session.add(
                EventOutbox(
                    world_id="sp-world",
                    sequence=99,
                    revision=1,
                    event_type="message_completed",
                    payload={"message_id": "msg-legacy", "text": "旧数据"},
                    audience="public",
                    cause_request_id=None,
                )
            )
        runner = KeeperAgentRunner(self.db_url, caller=_ScriptedCaller([]))
        context = json.loads(runner._build_context("sp-world", "", []))
        texts = [m["text"] for m in context["recent_public_messages"]]
        self.assertNotIn("旧数据", texts)

    async def test_all_rejected_commands_do_not_silently_park_the_request(self):
        """一步命令全被拒时，不能什么都没做就把请求挂起等玩家。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        box: list[dict] = []
        result = await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="想收尾但 payload 写错了",
                        commands=[
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-1",
                                    "resolution": "completed",
                                    # outcome 只允许三选一：自由文本会被拒
                                    "outcome": "已确认遗体在医学院",
                                },
                            }
                        ],
                        wait_for_player=True,
                        stop_reason="wait_player",
                    ),
                    _decision(
                        assessment="按驳回理由修正后收尾",
                        commands=[
                            {
                                "kind": "resolve_intent",
                                "payload": {
                                    "request_id": "req-1",
                                    "resolution": "completed",
                                    "outcome": "success",
                                    "note": "已确认遗体在医学院",
                                },
                            }
                        ],
                        narration="法伦确认了遗体所在。",
                        stop_reason="done",
                    ),
                ]
            ),
            "req-1",
            box,
        )
        # 第二次调用是模型自己纠正的机会：请求被正常收尾，而不是静默挂起。
        self.assertEqual(2, result.model_calls)
        self.assertEqual("completed", self.request_row("req-1").status)
        self.assertFalse(result.awaiting_parked)
        self.assertIn("法伦确认了遗体所在", " ".join(
            str((e.get("payload") or {}).get("text") or "") for e in box
        ))

    # ---------------- 7.8) 第四轮收口：schema 一致性、失败分类、无关键词路径

    async def test_agent_command_failing_schema_is_rejected_before_execution(self):
        """Agent 生成的命令必须与客户端帧同一份 schema：坏 payload 进不了执行层。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        box: list[dict] = []
        result = await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="输出里 audience 写成了字符串",
                        commands=[
                            {
                                "kind": "publish_message",
                                "payload": {
                                    "speaker": {"kind": "keeper"},
                                    "audience": "public",
                                    "text": "不该落库",
                                },
                            }
                        ],
                        narration="",
                        stop_reason="done",
                    )
                ]
            ),
            "req-1",
            box,
        )
        self.assertEqual(0, result.commands_committed)
        self.assertEqual(0, len([e for e in box if e["type"] == "message_completed"]))
        with session_scope(self.db_url) as session:
            rows = session.execute(
                select(EventOutbox).where(
                    EventOutbox.world_id == "sp-world",
                    EventOutbox.event_type == "message_completed",
                )
            ).scalars()
            self.assertEqual([], list(rows))

    async def test_truncated_and_empty_output_are_classified_separately(self):
        """截断（finish_reason=length）与「stop 但空」必须分开，并带生效预算与 usage。"""
        from src.structured.agent import EmptyModelOutput

        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})

        async def truncated(system: str, user: str) -> str:
            raise EmptyModelOutput(
                finish_reason="length", max_tokens=16000, usage={"total_tokens": 6800}
            )

        result = await KeeperAgentRunner(self.db_url, caller=truncated).run(
            world_id="sp-world", trigger_request_id="req-1"
        )
        self.assertEqual("model_output_truncated", result.stop_reason)
        row = self.request_row("req-1")
        self.assertIn("finish_reason=length", row.detail)
        self.assertIn("max_tokens=16000", row.detail)
        self.assertIn("6800", row.detail)

        self.submit("req-2", {"kind": "freeform", "text": "那我等会儿再问。"})

        async def empty(system: str, user: str) -> str:
            raise EmptyModelOutput(
                finish_reason="stop", max_tokens=16000, usage={"total_tokens": 120}
            )

        result2 = await KeeperAgentRunner(self.db_url, caller=empty).run(
            world_id="sp-world", trigger_request_id="req-2"
        )
        self.assertEqual("model_output_empty", result2.stop_reason)
        self.assertIn("finish_reason=stop", self.request_row("req-2").detail)

    async def test_model_timeout_is_recorded_separately(self):
        """超时与解析失败、空输出区分开记录。"""
        from openai import APITimeoutError

        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})

        async def timeout(system: str, user: str) -> str:
            raise APITimeoutError(request=object())  # type: ignore[arg-type]

        result = await KeeperAgentRunner(self.db_url, caller=timeout).run(
            world_id="sp-world", trigger_request_id="req-1"
        )
        self.assertEqual("model_timeout", result.stop_reason)
        self.assertIn("超时", self.request_row("req-1").detail)

    def test_player_reply_alone_never_moves_the_world(self):
        """没有关键词判断器：玩家说「我现在过去」也不会自己移动，必须由主持命令落账。"""
        start_scene = self.scene_id()
        self.submit("req-1", {"kind": "freeform", "text": "那麻烦你联系一下，我现在过去。"})
        self.assertEqual(start_scene, self.scene_id())
        self.assertEqual("queued", self.request_row("req-1").status)
        # 唯一改变位置的通路是主持命令；直接调用领域层同样要显式指定目的地
        with session_scope(self.db_url) as session:
            from src.storage.database import EventOutbox as _Outbox

            self.assertEqual(
                [],
                [
                    r
                    for r in session.execute(
                        select(_Outbox).where(_Outbox.world_id == "sp-world")
                    ).scalars()
                    if r.event_type == "scene_changed"
                ],
            )

    async def test_deferred_intent_never_executes_itself(self):
        """待办的目标不是执行授权：挂着待办时一次纯等待运行不会移动。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先去图书馆。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="挂起前往图书馆的意愿",
                        narration="老看守说现在闭馆。",
                        wait_for_player=True,
                        awaiting={
                            "pending_action": {
                                "kind": "move",
                                "destination_scene_id": "library",
                                "note": "尚未前往图书馆",
                            }
                        },
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )
        start_scene = self.scene_id()
        self.submit("req-2", {"kind": "freeform", "text": "嗯……"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="玩家还没定，不移动",
                        narration="老看守等你拿主意。",
                        stop_reason="done",
                    )
                ]
            ),
            "req-2",
            [],
        )
        self.assertEqual(start_scene, self.scene_id())
        self.assertEqual("awaiting_player", self.request_row("req-1").status)

    async def test_fallback_command_id_is_not_reused_after_a_fully_rejected_step(self):
        """某步全部被拒时，下一步的兜底 command_id 不得复用，否则撞幂等键空转。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        box: list[dict] = []
        result = await self._run(
            _ScriptedCaller(
                [
                    # 第一步：模型没给 command_id，且 payload 会被 schema 拒
                    _decision(
                        assessment="坏 payload",
                        commands=[
                            {"kind": "publish_message", "payload": {"speaker": {"kind": "keeper"}}}
                        ],
                    ),
                    # 第二步：同样没给 command_id，这次合法——不能被当成重复命令
                    _decision(
                        assessment="修好了",
                        commands=[
                            {
                                "kind": "publish_message",
                                "payload": {
                                    "speaker": {"kind": "keeper"},
                                    "audience": {"kind": "public"},
                                    "text": "法伦点了点头。",
                                },
                            }
                        ],
                        narration="",
                        stop_reason="done",
                    ),
                ]
            ),
            "req-1",
            box,
        )
        self.assertEqual(1, result.commands_committed, "第二步的合法命令必须能提交")
        self.assertTrue(
            any(
                (envelope.get("payload") or {}).get("text") == "法伦点了点头。"
                for envelope in box
                if envelope.get("type") == "message_completed"
            )
        )

    # ------------------------------------------------------- 8) 隐私

    async def test_awaiting_todo_visible_only_to_owner_and_keeper(self):
        """待办只对本人与主持可见：另一位玩家的快照里没有它。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        await self._run(
            _ScriptedCaller(
                [
                    _decision(
                        assessment="挂起",
                        narration="法伦说需要医生放行。",
                        wait_for_player=True,
                        awaiting={"pending_action": {"kind": "move", "note": "尚未出发"}},
                        stop_reason="wait_player",
                    )
                ]
            ),
            "req-1",
            [],
        )

        own = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        other = self.service.session_snapshot(world_id="sp-world", principal=self.bob)
        keeper = self.service.session_snapshot(world_id="sp-world", principal=self.keeper)

        own_entry = next(r for r in own["requests"] if r["request_id"] == "req-1")
        self.assertEqual("awaiting_player", own_entry["status"])
        self.assertIn("awaiting", own_entry)
        self.assertNotIn("req-1", [r["request_id"] for r in other["requests"]])
        self.assertIn("req-1", [r["request_id"] for r in keeper["requests"]])

    # ------------------------------------------- 9) 幂等与人类主持同能力

    async def test_room_keeper_frame_parks_and_other_players_cannot_see_it(self):
        """云端/房间路径：守秘人用 `command_request` 挂起，另一位玩家看不到该待办。

        与本地路径共用同一个命令服务；这里验证帧经 schema 校验后落账，
        且快照按 principal 过滤（甲私有待办，乙不可见）。
        """
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        gateway = StructuredGateway(self.db_url)
        delivered: list[dict] = []
        await gateway.handle_frame(
            world_id="sp-world",
            user_id="u-keeper",
            frame={
                "type": "command_request",
                "protocol_version": 1,
                "world_id": "sp-world",
                "command_id": "cmd-await-room",
                "expected_revision": 1,
                "cause_id": "req-1",
                "kind": "resolve_intent",
                "payload": {
                    "request_id": "req-1",
                    "resolution": "awaiting_player",
                    "pending_action": {
                        "kind": "move",
                        "destination_scene_id": "library",
                        "note": "尚未出发前往图书馆",
                    },
                    "disclosed": ["图书馆现在闭馆，需要先打招呼"],
                    "note": "等玩家决定",
                },
            },
            deliver=_deliver(delivered),
        )

        errors = [e["payload"] for e in delivered if e["type"] == "request_error"]
        self.assertEqual([], errors, delivered)
        awaiting = [
            e["payload"]
            for e in delivered
            if e["type"] == "action_status"
            and e["payload"].get("status") == "awaiting_player"
        ]
        self.assertTrue(awaiting, delivered)
        self.assertEqual("move", awaiting[0]["awaiting"]["pending_action"]["kind"])
        self.assertEqual(["图书馆现在闭馆，需要先打招呼"], awaiting[0]["awaiting"]["disclosed"])
        self.assertEqual("awaiting_player", self.request_row("req-1").status)

        own = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        other = self.service.session_snapshot(world_id="sp-world", principal=self.bob)
        self.assertIn("req-1", [r["request_id"] for r in own["requests"]])
        self.assertNotIn("req-1", [r["request_id"] for r in other["requests"]])

    async def test_repeat_run_does_not_double_park(self):
        """同一触发请求重复运行：待办不重复、事件不重复。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        decision = _decision(
            assessment="挂起",
            narration="法伦说需要医生放行。",
            wait_for_player=True,
            awaiting={"pending_action": {"kind": "move", "note": "尚未出发"}},
            stop_reason="wait_player",
        )
        await self._run(_ScriptedCaller([decision]), "req-1", [])
        box: list[dict] = []
        await self._run(_ScriptedCaller([decision]), "req-1", box)
        statuses = [
            p
            for t, p in self.events(box)
            if t == "action_status" and p.get("request_id") == "req-1"
        ]
        self.assertEqual([], statuses, "重复运行不应再产出等待事件")
        self.assertEqual("awaiting_player", self.request_row("req-1").status)

    def test_human_keeper_await_without_pending_action_derives_from_request(self):
        """没写 pending_action 时从原请求派生待办：命令层与运行器同一口径。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={"request_id": "req-1", "resolution": "awaiting_player"},
            command_id="cmd-await-derived",
            expected_revision=None,
        )
        row = self.request_row("req-1")
        self.assertEqual("awaiting_player", row.status)
        derived = (row.payload or {}).get("awaiting")
        self.assertEqual("freeform", derived["pending_action"]["kind"])
        self.assertIn("我想先看看尸体", derived["pending_action"]["note"])

    def test_await_without_any_derivable_action_is_rejected(self):
        """请求本身没有可识别行动时，不许挂一条「没内容」的等待。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        with session_scope(self.db_url) as session:
            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_id == "req-1",
                )
            ).scalar_one()
            row.payload = {"action": {"kind": "被清空的行动"}}  # 无法派生
        with self.assertRaises(StructuredError):
            self.service.execute_command(
                world_id="sp-world",
                principal=self.keeper,
                kind="resolve_intent",
                payload={"request_id": "req-1", "resolution": "awaiting_player"},
                command_id="cmd-await-empty",
                expected_revision=None,
            )

    def test_human_keeper_parks_and_player_reply_resolves(self):
        """人类主持路径：挂起 → 玩家回应 → 主持收尾（不需要口令）。"""
        self.submit("req-1", {"kind": "freeform", "text": "我想先看看尸体。"})
        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={
                "request_id": "req-1",
                "resolution": "awaiting_player",
                "pending_action": {"kind": "move", "note": "尚未出发"},
                "disclosed": ["停尸房需要医生放行"],
                "note": "等玩家决定是否现在联系",
            },
            command_id="cmd-await-1",
            expected_revision=None,
        )
        row = self.request_row("req-1")
        self.assertEqual("awaiting_player", row.status)
        self.assertEqual("尚未出发", row.payload["awaiting"]["pending_action"]["note"])

        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={"request_id": "req-1", "resolution": "completed", "note": "玩家改天再去"},
            command_id="cmd-close-1",
            expected_revision=None,
        )
        self.assertEqual("completed", self.request_row("req-1").status)


def _deliver(box: list):
    async def deliver(envelope: dict) -> None:
        box.append(envelope)

    return deliver
