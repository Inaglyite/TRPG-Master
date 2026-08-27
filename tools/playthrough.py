#!/usr/bin/env python3
"""猩红文档全真机 playthrough 验收 harness。

用真实模型完整跑一遍主线（法伦 → 停尸房 → 研究生 → 莱特小屋 → 精神病院 →
三买家 → 古董店战斗 → truth_and_seal 结局），逐回合快照世界状态并按 beat 断言。
产物是一份 JSON 报告 + 控制台摘要，按 10 个验收面归类。

用法：
    env -u PYTHONPATH .venv/bin/python tools/playthrough.py
    env -u PYTHONPATH .venv/bin/python tools/playthrough.py --report /tmp/pt.json

注意：全程真实调用模型（有 token 成本）；建独立新世界，绝不碰已有存档。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    """复刻 server.py 的 .env.json → env 映射（必须在 import src 前完成）。"""
    env_file = ROOT / ".env.json"
    if not env_file.exists():
        return
    cfg = json.loads(env_file.read_text(encoding="utf-8"))
    mapping = {
        "api_key": "OPENAI_API_KEY",
        "base_url": "OPENAI_BASE_URL",
        "flash_model": "TRPG_FLASH_MODEL",
        "pro_model": "TRPG_PRO_MODEL",
        "narrative_model": "TRPG_NARRATIVE_MODEL",
        "judgement_model": "TRPG_JUDGEMENT_MODEL",
        "glm_api_key": "GLM_API_KEY",
        "glm_base_url": "GLM_BASE_URL",
        "glm_model": "GLM_MODEL",
        "context_window_tokens": "TRPG_CONTEXT_WINDOW_TOKENS",
        "max_output_tokens": "TRPG_MAX_OUTPUT_TOKENS",
    }
    for cfg_key, env_key in mapping.items():
        val = cfg.get(cfg_key)
        if val and env_key not in os.environ:
            os.environ[env_key] = str(val)


_load_env()

from src.config import PROJECT_ROOT, RUNTIME_ROOT  # noqa: E402
from src.engine import GameEngine  # noqa: E402
from src.engine_primitives import EngineCallbacks  # noqa: E402
from src.tools import execute_function  # noqa: E402
from src.world_branches import WorldBranchService  # noqa: E402

# ---------------------------------------------------------------------------
# 捕获与快照
# ---------------------------------------------------------------------------


@dataclass
class Capture:
    """一个 playthrough 全程捕获的引擎事件。"""

    narratives: list[str] = field(default_factory=list)
    segments: list[list[dict]] = field(default_factory=list)
    handouts: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    dice: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    game_over: dict | None = None
    turn_snapshots: list[dict] = field(default_factory=list)


def make_callbacks(capture: Capture, *, decision_strategy: str = "default") -> EngineCallbacks:
    """回调桩：全部事件落 Capture；确认门按策略自动应答。"""

    def on_decision(info: dict) -> str | None:
        capture.decisions.append(info)
        if info.get("kind") == "action_preview":
            return next(
                (
                    option.get("id")
                    for option in info.get("options", [])
                    if isinstance(option, dict) and option.get("id") == "continue_action"
                ),
                info.get("default_option"),
            )
        if decision_strategy == "confirm":
            options = [o for o in info.get("options", []) if isinstance(o, dict)]
            for option in options:
                if "确认" in str(option.get("label", "")) or option.get("id") == "confirm":
                    return option.get("id")
        return info.get("default_option")

    return EngineCallbacks(
        on_narrative=lambda text, npc_id=None: capture.narratives.append(text),
        on_narrative_segments=lambda segments: capture.segments.append(segments),
        on_handout=lambda info: capture.handouts.append(info),
        on_decision=on_decision,
        on_dice=lambda summary, roll_data=None: capture.dice.append(summary),
        on_error=lambda msg: capture.errors.append(msg),
        on_game_over=lambda t, title, s: setattr(
            capture, "game_over", {"ending_type": t, "title": title}
        ),
    )


def clue_ids(world: dict) -> set[str]:
    """clues_found 实际是按类别分组的 dict（investigation/event/task/npc…），
    每组是线索 dict 列表；拍平成 id 集合。"""
    raw = world.get("clues_found") or {}
    if isinstance(raw, list):  # 兼容旧形状
        ids: set[str] = set()
        for clue in raw:
            if isinstance(clue, dict) and clue.get("id"):
                ids.add(str(clue["id"]))
            elif isinstance(clue, str):
                ids.add(clue)
        return ids
    ids: set[str] = set()
    for group in raw.values():
        for clue in group or []:
            if isinstance(clue, dict) and clue.get("id"):
                ids.add(str(clue["id"]))
    return ids


def snapshot_world(world: dict) -> dict:
    """逐回合世界状态快照（只取验收相关字段）。"""
    scene = world.get("current_scene") or {}
    pc = world.get("pc") or {}
    combat = world.get("combat_state") or {}
    return {
        "scene": scene.get("id"),
        "npcs_present": scene.get("npcs_present"),
        "clues_found": sorted(clue_ids(world)),
        "flags": dict(world.get("flags") or {}),
        "hp": pc.get("hp"),
        "san": pc.get("san"),
        "inventory": list(pc.get("inventory") or []),
        "psychological": pc.get("psychological_profile"),
        "clocks": dict(world.get("case_clocks") or {}),
        "combat_active": bool(combat.get("active")),
    }


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------


@dataclass
class Check:
    """一条验收断言。fn 返回 True/False；area 对应 10 个验收面编号。"""

    area: int
    desc: str
    fn: object  # Callable[[dict, Capture], bool]
    result: bool | None = None


@dataclass
class Beat:
    key: str
    title: str
    inputs: list[str]
    goal: object  # Callable[[dict, Capture], bool]：beat 推进条件
    checks: list[Check] = field(default_factory=list)
    max_turns: int = 8


def scene_is(scene_id: str):
    return lambda world, _cap: world.get("current_scene", {}).get("id") == scene_id


def scene_reached(scene_id: str):
    """beat 期间任意回合到过该场景（终态可能已离开）。"""

    return lambda _world, cap: any(snap.get("scene") == scene_id for snap in cap.turn_snapshots)


def clue_found(clue_id: str):
    return lambda world, _cap: clue_id in clue_ids(world)


def check_scene(area: int, scene_id: str) -> Check:
    """beat 期间到达过即算：玩家可能在一个 beat 内穿行多个场景。"""
    return Check(
        area,
        f"场景到达 {scene_id}",
        lambda w, c: scene_reached(scene_id)(w, c),
    )


def check_clue(area: int, clue_id: str) -> Check:
    return Check(area, f"线索入册 {clue_id}", lambda w, c: clue_id in clue_ids(w))


def check_handout(area: int, needle: str) -> Check:
    return Check(
        area,
        f"handout 发放（{needle}）",
        lambda w, c: any(needle in json.dumps(h, ensure_ascii=False) for h in c.handouts),
    )


def check_flag(area: int, flag: str) -> Check:
    return Check(area, f"flag 置位 {flag}", lambda w, c: bool((w.get("flags") or {}).get(flag)))


def check_dice(area: int, desc: str = "检定/掷骰有执行") -> Check:
    return Check(area, desc, lambda w, c: bool(c.dice))


# ---------------------------------------------------------------------------
# 主线 beats（猩红文档）
# ---------------------------------------------------------------------------

MODULE = "猩红文档"


def scarlet_beats() -> list[Beat]:
    return [
        Beat(
            key="B0_opening",
            title="开局：法伦办公室",
            inputs=["__OPENING__"],
            goal=scene_is("miskatonic_university"),
            max_turns=2,
            checks=[
                check_scene(2, "miskatonic_university"),
                Check(
                    3,
                    "开场拿到莱特办公室/小屋钥匙",
                    lambda w, c: any(
                        "钥匙" in str(item) for item in (w.get("pc") or {}).get("inventory", [])
                    ),
                ),
            ],
        ),
        Beat(
            key="B1_morgue",
            title="停尸房：惠特克罗夫特与验尸",
            inputs=[
                "我想先看看莱特教授的尸体。",
                "请惠特克罗夫特带我查看莱特教授的遗体，我要仔细检查尸体",
                "我仔细验尸：检查莱特遗体的皮肤、指甲、躯干和任何异常痕迹",
            ],
            goal=lambda w, c: (
                scene_is("miskatonic_medical")(w, c) and clue_found("wright_body_evidence")(w, c)
            ),
            max_turns=10,
            checks=[
                check_scene(2, "miskatonic_medical"),
                Check(
                    2,
                    "惠特克罗夫特在场",
                    lambda w, c: any(
                        "john_whitcroft" in (s.get("npcs_present") or [])
                        for s in c.turn_snapshots
                        if s.get("scene") == "miskatonic_medical"
                    ),
                ),
                check_handout(3, "john_whitcroft"),
                check_clue(3, "wright_body_evidence"),
                check_dice(1, "验尸/侦查检定有执行"),
            ],
        ),
        Beat(
            key="B1b_threat",
            title="威胁无辜者确认门（威胁后放弃）",
            inputs=["我拔出左轮指着惠特克罗夫特，威胁他说出真相"],
            goal=lambda w, c: any(
                decision.get("kind") == "coercive_threat" for decision in c.decisions
            ),
            max_turns=2,
            checks=[
                Check(
                    4,
                    "威胁无辜 NPC 触发确认门",
                    lambda w, c: any(
                        decision.get("kind") == "coercive_threat"
                        for decision in c.decisions
                    ),
                ),
                Check(
                    4,
                    "放弃确认后无战斗状态",
                    lambda w, c: not (w.get("combat_state") or {}).get("active"),
                ),
            ],
        ),
        Beat(
            key="B2_students",
            title="两个研究生（历史系/学生公社）",
            inputs=[
                "前往历史系找艾米莉亚·考特，询问她和莱特教授的往来",
                "前往学生公社找安东尼·弗林德斯，问他对莱特之死知道什么",
                "继续追问在场的研究生，挖出他们隐瞒的事",
            ],
            goal=lambda w, c: (
                len(
                    set(s.get("scene") for s in c.turn_snapshots)
                    & {"miskatonic_history", "miskatonic_student_commons"}
                )
                >= 1
            ),
            max_turns=10,
            checks=[
                Check(
                    2,
                    "到达过历史系或学生公社",
                    lambda w, c: bool(
                        set(s.get("scene") for s in c.turn_snapshots)
                        & {"miskatonic_history", "miskatonic_student_commons"}
                    ),
                ),
            ],
        ),
        Beat(
            key="B3a_office",
            title="莱特办公室：私人日记（模组 source=wright_office）",
            inputs=[
                "前往莱特教授的办公室，用黄铜钥匙开门",
                "搜查办公室：书桌抽屉、夹层、文件柜，找到莱特的私人日记",
                "继续彻底搜查办公室，翻开每一本笔记和抽屉夹层",
            ],
            goal=clue_found("wright_private_diary"),
            max_turns=10,
            checks=[
                check_scene(2, "wright_office"),
                check_clue(3, "wright_private_diary"),
            ],
        ),
        Beat(
            key="B3b_cottage",
            title="莱特小屋：双重生活",
            inputs=[
                "前往镇外莱特的小屋，用黄铜钥匙开门进去",
                "搜查小屋：书桌、抽屉、床铺和任何藏东西的地方",
                "继续搜查小屋，找莱特藏起来的东西",
            ],
            goal=lambda w, c: bool((w.get("flags") or {}).get("cottage_searched")),
            max_turns=8,
            checks=[
                check_scene(2, "wright_cottage"),
                Check(
                    3,
                    "小屋搜查完成（cottage_searched）",
                    lambda w, c: bool((w.get("flags") or {}).get("cottage_searched")),
                ),
            ],
        ),
        Beat(
            key="B4_sanatorium",
            title="精神病院：塞西尔·亨特与三个买家",
            inputs=[
                "前往阿卡姆疗养院，看望被开除的学生塞西尔·亨特",
                "安抚亨特，请他讲述文档和他复制的内容",
                "继续追问亨特：都有谁想要收购这批文档",
                # 对齐 hunter_copy 确定性发现规则（talk/examine 双路）
                "向亨特询问他偷偷复制的那份复制件，请他交给我检查",
                "接过那份复制件，在烛光下仔细检查纸上的墨迹",
            ],
            goal=lambda w, c: (
                scene_is("arkham_sanatorium")(w, c) and clue_found("hunter_copy")(w, c)
            ),
            max_turns=10,
            checks=[
                check_scene(2, "arkham_sanatorium"),
                check_clue(3, "hunter_copy"),
            ],
        ),
        Beat(
            key="B5_buyers",
            title="分别调查三个买家",
            inputs=[
                "前往希布酒馆，打听想买莱特文档的买家消息",
                "前往洛奇办公室，调查哈兰德·洛奇与文档收购的关系",
                "前往轻率琐事古董店，观察店主维克",
            ],
            goal=lambda w, c: (
                len(
                    set(s.get("scene") for s in c.turn_snapshots)
                    & {"sheb_tavern", "miskatonic_lodge_office", "trivial_pursuits"}
                )
                >= 2
            ),
            max_turns=12,
            checks=[
                Check(
                    2,
                    "调查过至少两个买家相关地点",
                    lambda w, c: (
                        len(
                            set(s.get("scene") for s in c.turn_snapshots)
                            & {"sheb_tavern", "miskatonic_lodge_office", "trivial_pursuits"}
                        )
                        >= 2
                    ),
                ),
            ],
        ),
        Beat(
            key="B5b_clock",
            title="时间推进：多日监视古董店（案件时钟发酵）",
            inputs=[
                "接下来几天，我白天在古董店对面的咖啡馆监视进出的人，晚上回旅馆整理三个买家的线索",
                "继续监视：记录维克、费德曼兄妹和任何可疑访客的规律，留意夜里搬运的大件货物",
                "又盯了几天：留意店里夜里的异常动静，并打听镇上关于文档或失踪者的传闻",
                "把这些天的监视结果汇总：谁最可能已经把文档弄到手了？",
            ],
            goal=lambda w, c: (
                any(
                    (w.get("case_clocks") or {}).get(name, 0) >= 2
                    for name in ("monster_manifestation", "human_pressure", "clue_clarity")
                )
                or bool((w.get("flags") or {}).get("monster_manifested"))
            ),
            max_turns=6,
            checks=[
                Check(
                    2,
                    "多日调查中案件时钟有推进（monster/human/clue 其一 >0）",
                    lambda w, c: any(
                        (snap.get("clocks") or {}).get(name, 0) > 0
                        for snap in c.turn_snapshots
                        for name in ("monster_manifestation", "human_pressure", "clue_clarity")
                    ),
                ),
            ],
        ),
        Beat(
            key="B6a_basement",
            title="古董店：找到并进入地下室",
            inputs=[
                "进入古董店，以顾客身份浏览，暗中记下布局：柜台后的小门、北端的木质隔断和厨房的位置",
                "趁店员不注意，检查大厅北端那堵不起眼的木质隔断，看看它后面藏着什么",
                # 白天被店员/兄妹拦下是正确守门；顺应模型给出的选项改走夜闯。
                "打烊后深夜，从后街的装货侧门潜入古董店，摸黑搜查一楼和厨房",
                "在厨房附近仔细搜查隐蔽区域，寻找隐藏的机关或通往地下的活板门",
                "找到活板门后顺着木梯下去，查看维克藏在地下储藏室的东西",
            ],
            goal=lambda w, c: bool(
                (w.get("flags") or {}).get("wicks_shop_searched")
                or (w.get("flags") or {}).get("monster_manifested")
                or (w.get("combat_state") or {}).get("active")
            ),
            max_turns=10,
            checks=[
                Check(
                    2,
                    "古董店搜查推进（wicks_shop_searched / 显形 / 开战其一）",
                    lambda w, c: bool(
                        (w.get("flags") or {}).get("wicks_shop_searched")
                        or (w.get("flags") or {}).get("monster_manifested")
                        or any(s.get("combat_active") for s in c.turn_snapshots)
                    ),
                ),
            ],
        ),
        Beat(
            key="B6b_fight",
            title="古董店：战斗（弹药消耗）",
            inputs=[
                "继续向地下室深处搜查，留意任何非人的动静，手按在枪柄上保持警戒",
                "当面对质维克：我知道文档在你手里，也知道地下室藏着什么",
                "遭遇非人生物袭击时拔枪自卫，朝袭击我的东西射击",
                "继续开枪射击那个非人生物，直到它不再动弹",
                "掩护自己继续开枪，别让它近身",
            ],
            goal=lambda w, c: bool(
                any(s.get("combat_active") for s in c.turn_snapshots)
                and not (w.get("combat_state") or {}).get("active")
            ),
            max_turns=12,
            checks=[
                # 模组有战斗与徽章封印两条危机解决路径；模型选了哪条都算危机成立，
                # 都不沾才是问题。
                Check(
                    1,
                    "终局危机成立（战斗 / 显形 / 怪物被解决其一）",
                    lambda w, c: bool(
                        any(s.get("combat_active") for s in c.turn_snapshots)
                        or (w.get("flags") or {}).get("monster_manifested")
                        or (w.get("flags") or {}).get("monster_defeated")
                    ),
                ),
            ],
        ),
        Beat(
            key="B6c_documents",
            title="古董店：找回女巫审判文档",
            inputs=[
                "威胁解除后彻底搜查地下室：独立储藏室、锁箱、冷柜和砖井周围，找回那批女巫审判文档",
                "打开地下室右下角的独立储藏室，取回女巫审判文档",
                "把女巫审判文档全部收好带走",
            ],
            goal=lambda w, c: (
                clue_found("witch_trial_documents")(w, c)
                or bool((w.get("flags") or {}).get("documents_recovered"))
            ),
            max_turns=10,
            checks=[
                check_clue(3, "witch_trial_documents"),
            ],
        ),
        Beat(
            key="B6d_seal",
            title="封印怪物：银质徽章关闭通道",
            inputs=[
                "我用银质徽章覆上文档中央的几何图示，完成封印",
            ],
            goal=lambda w, c: bool((w.get("flags") or {}).get("monster_defeated")),
            max_turns=3,
            checks=[
                Check(
                    5,
                    "银质徽章封印落定（monster_defeated）",
                    lambda w, c: bool((w.get("flags") or {}).get("monster_defeated")),
                ),
            ],
        ),
        Beat(
            key="B8_ending",
            title="结局：truth_and_seal",
            inputs=[
                "文档已经找回、怪物已被消灭。我去找法伦，完成封印仪式，了结这个案子",
                "我用银质徽章覆上文档中央的几何图示，完成封印",
                "完成封印，结案",
            ],
            goal=lambda w, c: bool(c.game_over),
            max_turns=8,
            checks=[
                Check(5, "结局触发", lambda w, c: bool(c.game_over)),
                # on_game_over 回调给的是 ending_type(good/neutral/bad/secret)+标题，
                # 不是结局 id；模组里唯一的 good 结局即 truth_and_seal。
                Check(
                    5,
                    "结局为 truth_and_seal（good：真相大白，怪物被制伏）",
                    lambda w, c: bool(
                        c.game_over
                        and c.game_over.get("ending_type") == "good"
                        and "真相大白" in str(c.game_over.get("title") or "")
                    ),
                ),
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# 运行器
# ---------------------------------------------------------------------------


def _ammo_count(world: dict) -> int | None:
    """从 inventory 里解析枪械余弹（如「.38口径左轮手枪（6发）」）。"""
    import re

    for item in (world.get("pc") or {}).get("inventory", []):
        match = re.search(r"[（(](\d+)\s*发[）)]", str(item))
        if match:
            return int(match.group(1))
    return None


def _madness_check(world: dict) -> bool:
    profile = (world.get("pc") or {}).get("psychological_profile") or {}
    text = json.dumps(profile, ensure_ascii=False)
    return any(k in text for k in ("恐惧症", "躁狂", "疯狂", "phobia", "mania"))


def run_playthrough(*, report_path: Path, verbose: bool = True) -> dict:
    wb = WorldBranchService(PROJECT_ROOT, RUNTIME_ROOT)
    context = wb.create_root(MODULE)
    engine = GameEngine(context)
    capture = Capture()
    engine.cb = make_callbacks(capture)
    # reset 才会应用调查员角色与 module_starting_inventory（钥匙/配枪）；
    # 直接 handle_action 跑的是模组默认空 inventory 世界，整场验收都会歪。
    # 角色显式钉死为模组自带调查员：default_character_ref 会优先取玩家
    # profile 履历角色，inventory 不可控，harness 不能用它。
    engine.reset(
        {
            "source": "module",
            "file": "黄千陆.json",
            "path": f"mod/{MODULE}/characters/黄千陆.json",
            "module": MODULE,
        }
    )

    # profiles 是真实共享数据：备份，跑完恢复
    profile_file = PROJECT_ROOT / "profiles" / "player_profile.json"
    profile_backup = profile_file.read_bytes() if profile_file.exists() else None

    world_id = context.world_id
    report: dict = {
        "world_id": world_id,
        "module": MODULE,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "beats": [],
        "global_checks": [],
    }

    def log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    def turn(user_input: str | None) -> dict:
        engine.handle_action(user_input)
        world = context.world_store.load()
        snap = snapshot_world(world)
        snap["input"] = user_input
        capture.turn_snapshots.append(snap)
        log(
            f"  [回合{len(capture.turn_snapshots)}] scene={snap['scene']} "
            f"clues={len(snap['clues_found'])} san={snap['san']} hp={snap['hp']}"
        )
        return world

    try:
        for beat in scarlet_beats():
            log(f"■ {beat.key} {beat.title}")
            world = context.world_store.load()
            done = False
            for attempt in range(beat.max_turns):
                user_input = beat.inputs[min(attempt, len(beat.inputs) - 1)]
                world = turn(None if user_input == "__OPENING__" else user_input)
                if beat.goal(world, capture):
                    done = True
                    break
            beat_report = {
                "key": beat.key,
                "title": beat.title,
                "goal_met": done,
                "turns": attempt + 1,
                "checks": [],
            }
            for check in beat.checks:
                check.result = bool(check.fn(world, capture))
                beat_report["checks"].append(
                    {"area": check.area, "desc": check.desc, "pass": check.result}
                )
                log(f"    [{'✓' if check.result else '✗'}] ({check.area}) {check.desc}")
            report["beats"].append(beat_report)
            if beat.key == "B6c_documents":
                # B7 疯狂注入：战斗后把 SAN 压到阈值，验证疯狂链路。
                # catastrophic 是骰子结算，可能连续小损失；循环注到达标为止（封顶 6 次）。
                log("■ B7_madness 注入 SAN 损失（catastrophic 直至 san<30，封顶 6 次）")
                for _ in range(6):
                    execute_function("sanity_loss", {"severity": "catastrophic"}, context=context)
                    if (context.world_store.load().get("pc") or {}).get("san", 99) < 30:
                        break
                world = context.world_store.load()
                turn("我稳住心神，深呼吸，确认自己还清醒")
                mad_world = context.world_store.load()
                madness_ok = _madness_check(mad_world)
                report["beats"].append(
                    {
                        "key": "B7_madness",
                        "title": "疯狂注入验证",
                        "goal_met": madness_ok,
                        "turns": 1,
                        "checks": [
                            {
                                "area": 10,
                                "desc": "SAN 被压低",
                                "pass": (mad_world.get("pc") or {}).get("san", 99) < 30,
                            },
                            {"area": 10, "desc": "疯狂症状写入心理特质", "pass": madness_ok},
                        ],
                    }
                )
    finally:
        if profile_backup is not None:
            profile_file.write_bytes(profile_backup)

    # ---- 全局检查 --------------------------------------------------------
    catalog = json.loads(
        (PROJECT_ROOT / "mod" / MODULE / "world_state_initial.json").read_text(encoding="utf-8")
    )

    # 9. 弹药消耗：逐回合快照里余弹是否递减过。
    # 弹药递减机制本身由 tests/test_combat.py 确定性覆盖（6发→5发、0发边界）；
    # 这里是真机 e2e 证据——模型走徽章封印等非战斗路径时不开枪，不计为引擎缺陷。
    ammo_seq = [
        _ammo_count({"pc": {"inventory": snap.get("inventory") or []}})
        for snap in capture.turn_snapshots
    ]
    ammo_decreased = any(
        earlier is not None and later is not None and later < earlier
        for earlier, later in zip(ammo_seq, ammo_seq[1:], strict=False)
    )
    report["global_checks"].append(
        {"area": 9, "desc": "枪械弹药随射击递减", "pass": ammo_decreased, "ammo_seq": ammo_seq}
    )

    # 7. 保密：叙事不得泄露未揭示 NPC 的 secret 文本片段
    leaks = []
    for npc in catalog.get("npcs", []):
        if not isinstance(npc, dict):
            continue
        secret = str(npc.get("secret") or "")
        if len(secret) < 12:
            continue
        probe = secret[6:18]
        if any(probe in narrative for narrative in capture.narratives):
            leaks.append(npc.get("id"))
    report["global_checks"].append(
        {"area": 7, "desc": "叙事未泄露 NPC secret 原文", "pass": not leaks, "leaks": leaks}
    )

    # 8. 提前说出未知线索：叙事出现线索原文时该线索必须已入册
    early = []
    clue_texts = {
        cid: str(c.get("text") or "")[:16]
        for cid, c in (catalog.get("clue_catalog") or {}).items()
        if len(str(c.get("text") or "")) >= 16
    }
    for index, narrative in enumerate(capture.narratives):
        known = (
            set(
                (capture.turn_snapshots[min(index, len(capture.turn_snapshots) - 1)] or {}).get(
                    "clues_found"
                )
                or []
            )
            if capture.turn_snapshots
            else set()
        )
        for cid, probe in clue_texts.items():
            if probe and probe in narrative and cid not in known:
                early.append(cid)
    report["global_checks"].append(
        {"area": 8, "desc": "叙事不提前说出未知线索原文", "pass": not early, "early": early}
    )

    # 6. 结局后履历：profiles 已恢复，这里只记录结算是否发生
    report["global_checks"].append(
        {"area": 6, "desc": "结局结算触发（profile 写入已回滚）", "pass": bool(capture.game_over)}
    )

    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["errors"] = capture.errors[:20]
    report["handout_count"] = len(capture.handouts)
    report["decision_count"] = len(capture.decisions)
    report["decision_kinds"] = [
        str(decision.get("kind") or "") for decision in capture.decisions
    ]
    report["dice_count"] = len(capture.dice)
    report["turn_count"] = len(capture.turn_snapshots)
    report["turn_snapshots"] = capture.turn_snapshots
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台摘要 ------------------------------------------------------
    total = passed = 0
    for beat in report["beats"]:
        for chk in beat["checks"]:
            total += 1
            passed += 1 if chk["pass"] else 0
    for chk in report["global_checks"]:
        total += 1
        passed += 1 if chk["pass"] else 0
    print(f"\n===== playthrough 报告 {report_path} =====")
    print(
        f"world={world_id} 回合数={report['turn_count']} "
        f"handout={report['handout_count']} dice={report['dice_count']} "
        f"decision={report['decision_count']}"
    )
    print(f"断言通过 {passed}/{total}")
    for beat in report["beats"]:
        mark = "✓" if beat["goal_met"] else "✗"
        print(f" {mark} {beat['key']} {beat['title']}（{beat['turns']} 回合）")
        for chk in beat["checks"]:
            if not chk["pass"]:
                print(f"     ✗ ({chk['area']}) {chk['desc']}")
    for chk in report["global_checks"]:
        mark = "✓" if chk["pass"] else "✗"
        print(f" {mark} [全局] ({chk['area']}) {chk['desc']}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="猩红文档全真机 playthrough 验收")
    parser.add_argument(
        "--report",
        default=f"/tmp/playthrough-{time.strftime('%Y%m%d-%H%M%S')}.json",
        help="报告输出路径",
    )
    args = parser.parse_args()
    run_playthrough(report_path=Path(args.report))


if __name__ == "__main__":
    main()
