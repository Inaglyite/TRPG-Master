#!/usr/bin/env python3
"""角色长期记忆与上下文组装的真实模型验收（B–F 专项，带完整轨迹）。

对应「结构化上下文与角色长期记忆」批次的真实模型验收矩阵：

    B 长期回忆：事实被挤出近期窗口后，主持应通过记忆检索回答，而不是说「没说过」。
    C 知识隔离：不在场 NPC 的私密记忆不得自动注入；扮演者不得把它演成在场 NPC 的知识。
    D 传闻与事实：rumor 必须带类型出场且叙事保留不确定性；experienced 可当事实陈述。
    E 缺失信息查询：查无依据时承认不确定，不编造权威事实、不反复追问。
    F 预算与无关记忆：自动注入区块受条数/字符双预算约束，必需区（snapshot/
      open_threads/pending_requests）不被挤掉；相关记忆靠 queries 检索找回。

判定原则：能对轨迹下手的断言全用轨迹（上下文内容、预算、查询次数、命令记录）；
叙事只做标记串包含/排除判断。运行未正常结束（paused/failed）一律 FAIL。

隔离：复用 transition_real_model_check 的世界构建（临时 runtime root，真实模组，
不碰任何真实存档）；种子数据走真实命令通道（keeper record_memory/publish_message）。

用法：
    env -u PYTHONPATH .venv/bin/python tools/memory_real_model_check.py \
        --report /tmp/memory_trace.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# transition_real_model_check 的模块级初始化负责 .env.json → env 与临时
# TRPG_RUNTIME_ROOT（必须先于 src 导入，见该文件头部说明）。
from transition_real_model_check import (  # noqa: E402
    _Collector,
    _scene_of,
    build_agent_world,
    install_recorder,
    wait_for_run,
)

from src.storage.database import session_scope  # noqa: E402
from src.structured.gateway import StructuredGateway  # noqa: E402
from src.structured.principal import Principal, bind_agent_control  # noqa: E402
from src.structured.service import StructuredPlayService  # noqa: E402

# 种子走 agent 控制通道：人类 keeper 接管会阻塞后续 agent 运行（单活动主持语义），
# 而 agent 运行在每次启动时自行绑定/轮换控制权，不与种子冲突。
SEED_PRINCIPAL = Principal(kind="agent", run_id="memory-acceptance-seed")


def _prepare_seeding(database_url: str, world_id: str) -> None:
    with session_scope(database_url) as session:
        bind_agent_control(session, world_id, SEED_PRINCIPAL.run_id)


# ---------------------------------------------------------------------------
# 种子与判据工具
# ---------------------------------------------------------------------------


def _seed(service: StructuredPlayService, world_id: str, kind: str, payload: dict, tag: str) -> dict:
    outcome = service.execute_command(
        world_id=world_id,
        principal=SEED_PRINCIPAL,
        kind=kind,
        payload=payload,
        command_id=f"seed-{tag}",
        expected_revision=None,
    )
    if outcome.get("status") != "committed":
        raise RuntimeError(f"种子命令失败 {kind}: {outcome}")
    return outcome


def _seed_memory(
    service: StructuredPlayService,
    world_id: str,
    *,
    character_id: str,
    knowledge_type: str,
    content: str,
    topics: list[str],
    tag: str,
    character_kind: str = "",
) -> None:
    _seed(
        service,
        world_id,
        "record_memory",
        {
            "character_id": character_id,
            "knowledge_type": knowledge_type,
            "content": content,
            "topics": topics,
            **({"character_kind": character_kind} if character_kind else {}),
        },
        tag,
    )


def _seed_filler(service: StructuredPlayService, world_id: str, count: int) -> None:
    for index in range(count):
        _seed(
            service,
            world_id,
            "publish_message",
            {
                "speaker": {"kind": "keeper"},
                "audience": {"kind": "public"},
                "text": f"（过场 {index + 1}）你们聊了聊天气、房租和旧报纸上的社会新闻，"
                f"与眼下的案子无关。第 {index + 1} 段无关紧要的对白到此结束。",
            },
            f"filler-{index:02d}",
        )


def _narrative(events: list[dict]) -> str:
    parts = []
    for envelope in events:
        if envelope.get("type") == "message_completed":
            parts.append(str((envelope.get("payload") or {}).get("text") or ""))
    return "\n".join(parts)


def _queries_of(calls: list[dict]) -> list[dict]:
    """从各次模型调用的决策 JSON 里收集 queries（只读检索的实际使用）。"""
    found: list[dict] = []
    for call in calls:
        content = str((call.get("response") or {}).get("content") or "")
        try:
            decision = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            continue
        for query in decision.get("queries") or []:
            if isinstance(query, dict):
                found.append(query)
    return found


def _context_of(call: dict) -> dict:
    messages = (call.get("request") or {}).get("messages") or []
    if len(messages) < 2:
        return {}
    try:
        return json.loads(messages[1].get("content") or "{}")
    except json.JSONDecodeError:
        return {}


def _base_entry(key: str, label: str, text: str) -> dict[str, Any]:
    return {"key": key, "label": label, "player_message": text, "notes": []}


# ---------------------------------------------------------------------------
# 五个场景
# ---------------------------------------------------------------------------


def scenario_b(service: StructuredPlayService, world_id: str) -> None:
    _seed_memory(
        service,
        world_id,
        character_id="pc",
        knowledge_type="told",
        content="法伦私下承认：他害怕医学院的地下室，天黑之后从不靠近那里。",
        topics=["法伦", "地下室"],
        tag="b-fact",
    )
    _seed_filler(service, world_id, 12)  # 把种子挤出最近 8 条的近期窗口


def judge_b(entry: dict, narrative: str, calls: list[dict], status: str) -> None:
    queries = _queries_of(calls)
    entry["notes"].append(f"queries={json.dumps(queries, ensure_ascii=False)}")
    if "地下室" in narrative:
        entry["verdict"] = "PASS"
    else:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "记忆未进入回应：叙事未提及地下室（长期回忆失败）"
    if not queries:
        entry["notes"].append("注意：未发起记忆查询就作答（可能走了自动注入或直接编造）")


def scenario_c(service: StructuredPlayService, world_id: str) -> None:
    _seed_memory(
        service,
        world_id,
        character_id="john_whitcroft",
        character_kind="npc",
        knowledge_type="experienced",
        content="惠特克罗夫特亲手把一枚蓝釉小瓶锁进了停尸房证物柜，从没登记。",
        topics=["证物"],
        tag="c-secret",
    )


def judge_c(entry: dict, narrative: str, calls: list[dict], status: str) -> None:
    # 机制断言：惠特克罗夫特不在场，他的记忆条目不得进入自动注入区块。
    # 注意不能用内容里的「蓝釉小瓶」判定——玩家问题本身提到它，Agent 自己
    # 的记录也会合法地带上这个词；泄漏标记是「他的条目被注入」与秘密细节。
    secret_owner_injected = False
    for call in calls:
        context = _context_of(call)
        for memory in context.get("character_memories") or []:
            if str(memory.get("character_id") or "") == "john_whitcroft":
                secret_owner_injected = True
    entry["notes"].append(f"absent_npc_memory_injected={secret_owner_injected}")
    # 行为断言：叙事不得泄露秘密细节（玩家问题本身含「蓝釉小瓶」，标记串取秘密部分）
    leaked_in_narrative = ("证物柜" in narrative) or ("从没登记" in narrative)
    entry["notes"].append(f"secret_in_narrative={leaked_in_narrative}")
    if secret_owner_injected:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "不在场 NPC 的记忆条目被自动注入模型上下文（机制缺陷）"
    elif leaked_in_narrative:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "叙事泄露了在场 NPC 不可能知道的秘密细节"
    else:
        entry["verdict"] = "PASS"


def scenario_d(service: StructuredPlayService, world_id: str) -> None:
    _seed_memory(
        service,
        world_id,
        character_id="bryce_fallon",
        character_kind="npc",
        knowledge_type="rumor",
        content="传闻：莱特死前最后一个周末去了港口区的私人拍卖会。",
        topics=["莱特"],
        tag="d-rumor",
    )
    _seed_memory(
        service,
        world_id,
        character_id="bryce_fallon",
        character_kind="npc",
        knowledge_type="experienced",
        content="法伦亲眼核对过：莱特的死亡证明由惠特克罗夫特签署。",
        topics=["莱特", "死亡证明"],
        tag="d-fact",
    )


def judge_d(entry: dict, narrative: str, calls: list[dict], status: str) -> None:
    hedges = ("传闻", "据说", "听说", "未经证实", "不确定", "可能", "传言", "说法")
    fact_stated = "惠特克罗夫特" in narrative
    rumor_flat = "拍卖会" in narrative and not any(word in narrative for word in hedges)
    entry["notes"].append(f"fact_stated={fact_stated} rumor_flat={rumor_flat}")
    if not fact_stated:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "亲历事实未被陈述（死亡证明签署人没答出来）"
    elif rumor_flat:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "传闻被当成确证事实平铺直叙（没有任何不确定性措辞）"
    else:
        entry["verdict"] = "PASS"


def scenario_e(service: StructuredPlayService, world_id: str) -> None:
    return  # 不种子任何东西：「银钥集会」在本世界不存在任何依据


def judge_e(entry: dict, narrative: str, calls: list[dict], status: str, attempts: list[dict]) -> None:
    uncertain = any(
        word in narrative
        for word in (
            "没听说", "不曾", "不知道", "没有印象", "没有听过", "并无", "没接触",
            "陌生", "不熟悉", "空白", "想不起", "没有提到", "从未", "毫无",
        )
    )
    fabricated_fact = any(
        attempt.get("kind") == "record_fact"
        and "银钥集会" in json.dumps(attempt.get("payload") or {}, ensure_ascii=False)
        for attempt in attempts
    )
    queries = _queries_of(calls)
    entry["notes"].append(
        f"uncertain={uncertain} fabricated_fact={fabricated_fact} queries={len(queries)}"
    )
    if fabricated_fact:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "对毫无依据的「银钥集会」提交了 record_fact（编造权威事实）"
    elif not uncertain:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "叙事没有承认不确定（疑似编造或答非所问）"
    else:
        entry["verdict"] = "PASS"


def scenario_f(service: StructuredPlayService, world_id: str) -> None:
    # 相关记忆先种、无关记忆后种：纯按时间序的注入会把相关条目挤出去，
    # 模型必须靠 queries（topic 检索）才能找回——正好覆盖预算与检索两条。
    _seed_memory(
        service,
        world_id,
        character_id="bryce_fallon",
        character_kind="npc",
        knowledge_type="experienced",
        content="关键记忆：莱特曾在深夜跟法伦提起「墨香与铁锈味」的说法。",
        topics=["莱特"],
        tag="f-relevant-1",
    )
    _seed_memory(
        service,
        world_id,
        character_id="bryce_fallon",
        character_kind="npc",
        knowledge_type="told",
        content="关键记忆：莱特把一枚徽章锁进了办公室抽屉。",
        topics=["莱特"],
        tag="f-relevant-2",
    )
    for index in range(30):
        _seed_memory(
            service,
            world_id,
            character_id="bryce_fallon",
            character_kind="npc",
            knowledge_type="experienced",
            content=f"无关紧要的记忆{index:02d}：去年秋天第{index}场雨的颜色。",
            topics=["天气"],
            tag=f"f-junk-{index:02d}",
        )


def judge_f(entry: dict, narrative: str, calls: list[dict], status: str) -> None:
    if not calls:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "没有任何模型调用"
        return
    context = _context_of(calls[0])
    memories = context.get("character_memories") or []
    block_chars = sum(len(str(m.get("content") or "")) + 40 for m in memories)
    budget_ok = len(memories) <= 8 and block_chars <= 800
    required_sections = all(
        key in context for key in ("snapshot", "open_threads", "pending_requests")
    )
    queries = _queries_of(calls)
    queried_topic = any(
        "莱特" in json.dumps(query, ensure_ascii=False) for query in queries
    )
    recalled = "墨香" in narrative or "徽章" in narrative
    entry["notes"].append(
        f"memory_entries={len(memories)} block_chars={block_chars} "
        f"required_sections={required_sections} queried_topic={queried_topic} recalled={recalled}"
    )
    if not budget_ok:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = f"自动注入区块超预算：{len(memories)} 条 / {block_chars} 字符"
    elif not required_sections:
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "必需区（snapshot/open_threads/pending_requests）被挤掉"
    elif not (recalled or queried_topic):
        entry["verdict"] = "FAIL"
        entry["verdict_note"] = "相关记忆既没被回忆起，模型也没按主题检索"
    else:
        entry["verdict"] = "PASS"


SCENARIOS: list[dict[str, Any]] = [
    {
        "key": "B_long_term_recall",
        "label": "长期回忆：事实挤出近期窗口后凭记忆回答",
        "seed": scenario_b,
        "text": "回想起来……法伦之前有没有提过，他自己害怕什么地方？",
        "judge": judge_b,
    },
    {
        "key": "C_knowledge_isolation",
        "label": "知识隔离：不在场 NPC 的私密记忆不泄漏",
        "seed": scenario_c,
        "text": "法伦，验尸的时候有没有发现过一只蓝釉小瓶？",
        "judge": judge_c,
    },
    {
        "key": "D_rumor_vs_fact",
        "label": "传闻与事实：传闻带不确定性，事实可直接陈述",
        "seed": scenario_d,
        "text": "法伦，莱特死前那个周末去了哪儿？他的死亡证明是谁签的？",
        "judge": judge_d,
    },
    {
        "key": "E_missing_info_query",
        "label": "缺失信息：查无依据时承认不确定，不编造",
        "seed": scenario_e,
        "text": "我们之前有没有听说过一个叫「银钥集会」的组织？",
        "judge": judge_e,
    },
    {
        "key": "F_budget_irrelevant",
        "label": "预算与无关记忆：必需区不被挤掉，相关记忆靠检索找回",
        "seed": scenario_f,
        "text": "法伦，关于莱特的事，你还记得什么？",
        "judge": judge_f,
    },
]


async def run(*, report_path: Path, timeout: float) -> dict:
    model_calls: list[dict] = []
    install_recorder(model_calls)
    from src.structured.service import StructuredPlayService as _SPS

    command_attempts: list[dict] = []
    original_execute = _SPS.execute_command

    def recording_execute(self: Any, **kwargs: Any) -> Any:
        entry = {"kind": kwargs.get("kind"), "payload": kwargs.get("payload")}
        try:
            outcome = original_execute(self, **kwargs)
        except Exception as exc:
            entry["rejected"] = {
                "code": getattr(exc, "code", type(exc).__name__),
                "message": str(getattr(exc, "message", exc))[:300],
            }
            command_attempts.append(entry)
            raise
        entry["result"] = outcome.get("result")
        command_attempts.append(entry)
        return outcome

    _SPS.execute_command = recording_execute  # type: ignore[method-assign]

    from transition_real_model_check import _code_fingerprint, _code_revision

    report: dict[str, Any] = {
        "acceptance": "memory-context-B-F",
        "code_revision": _code_revision(),
        "code_fingerprint": _code_fingerprint(),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scenarios": [],
    }

    for scenario in SCENARIOS:
        context, world_id, byok = build_agent_world()
        gateway = StructuredGateway(context.database_url)
        service = StructuredPlayService(context.database_url)

        _prepare_seeding(context.database_url, world_id)
        scenario["seed"](service, world_id)

        # 种子命令会推进 revision：帧的 expected_revision 必须取种子之后的快照，
        # 否则帧被拒（revision 冲突），请求根本不会建立。
        snapshot = gateway.snapshot_envelope(world_id=world_id, user_id=None)["payload"]
        investigator_id = str(snapshot.get("investigator_id") or "")
        entry = _base_entry(scenario["key"], scenario["label"], scenario["text"])
        entry["world_id"] = world_id
        entry["start_scene"] = _scene_of(context)
        print(f"\n=== {scenario['key']}｜{scenario['label']} ===\n玩家：{scenario['text']}")

        collector = _Collector()
        calls_before = len(model_calls)
        attempts_before = len(command_attempts)
        await gateway.handle_frame(
            world_id=world_id,
            user_id=None,
            frame={
                "type": "action_request",
                "protocol_version": 1,
                "world_id": world_id,
                "request_id": f"mem-{scenario['key']}",
                "expected_revision": int(snapshot["revision"]),
                "investigator_id": investigator_id,
                "action": {"kind": "freeform", "text": scenario["text"]},
            },
            deliver=collector.send,
        )
        status = await wait_for_run(context.database_url, world_id, f"mem-{scenario['key']}", timeout=timeout)
        calls = model_calls[calls_before:]
        attempts = command_attempts[attempts_before:]
        narrative = _narrative(collector.events)
        entry.update(
            {
                "request_status": status,
                "narrative": narrative,
                "model_calls": calls,
                "command_attempts": attempts,
                "usage_tokens": sum(
                    int((call.get("usage") or {}).get("total_tokens") or 0) for call in calls
                ),
            }
        )
        if status in {"paused", "failed", "missing"} or status.startswith("timeout"):
            entry["verdict"] = "FAIL"
            entry["verdict_note"] = f"运行未正常结束：{status}"
        else:
            if scenario["key"] == "E_missing_info_query":
                scenario["judge"](entry, narrative, calls, status, attempts)
            else:
                scenario["judge"](entry, narrative, calls, status)
        print(
            f"请求 {status}｜模型调用 {len(calls)}｜tokens {entry['usage_tokens']}｜"
            f"判定 {entry['verdict']} {entry.get('verdict_note') or ''}"
        )
        print(f"  守秘人：{narrative[:200]}")
        report["scenarios"].append(entry)

    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["total_tokens"] = sum(s["usage_tokens"] for s in report["scenarios"])
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="/tmp/memory_trace.json")
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    report = asyncio.run(run(report_path=Path(args.report), timeout=args.timeout))
    verdicts = {s["key"]: s["verdict"] for s in report["scenarios"]}
    print(f"\n报告：{args.report}")
    print(f"总 tokens：{report['total_tokens']}")
    print(f"总判定：{verdicts}")
    return 1 if any(v == "FAIL" for v in verdicts.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
