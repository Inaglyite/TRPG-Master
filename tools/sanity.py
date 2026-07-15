#!/usr/bin/env python3
"""TRPG 理智值工具 —— COC 第七版三阶段疯狂系统"""

import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime import RuntimeContext  # noqa: E402

CONTEXT = RuntimeContext.from_env()
STORE = CONTEXT.world_store
_TRANSACTION_STATE = None


def _load_state():
    if _TRANSACTION_STATE is not None:
        return _TRANSACTION_STATE
    return STORE.load()


def _save_state(data):
    if _TRANSACTION_STATE is not None:
        if data is not _TRANSACTION_STATE:
            _TRANSACTION_STATE.clear()
            _TRANSACTION_STATE.update(data)
        return
    STORE.restore(data)


# 疯狂发作表
BOUT_OF_MADNESS = [
    "失忆 — 最近发生的事情完全记不起来，持续1D10轮",
    "假性残疾 — 某个肢体或感官暂时失效（失明/失聪/瘫痪），持续1D10轮",
    "暴力倾向 — 对最近的威胁源进行无差别攻击，持续1D10轮",
    "偏执妄想 — 确信有人/物在监视和跟踪自己，持续1D10小时",
    "人际依赖 — 对最近接触的人产生极度依赖，持续1D10小时",
    "昏厥 — 当场晕倒，1D10轮后苏醒",
    "逃避行为 — 不顾一切逃跑，持续1D10轮",
    "歇斯底里 — 无法控制地大笑/哭泣/尖叫，持续1D10轮",
    "恐惧症 — 从恐惧症表中随机选择一项恐惧源（即使不存在也想象存在），持续1D10轮",
    "躁狂症 — 从躁狂症表中随机选择一项躁狂诱因，持续1D10轮"
]

# COC 7e 恐惧症表（规则书第八章 + 模组主题扩展）
PHOBIAS = [
    "恐高症（Acrophobia）— 对高处的恐惧",
    "幽闭恐惧（Claustrophobia）— 对密闭空间的恐惧",
    "广场恐惧（Agoraphobia）— 对开阔空间的恐惧",
    "恐血症（Hemophobia）— 对血液的恐惧",
    "黑夜恐惧（Nyctophobia）— 对黑暗的恐惧",
    "恐水症（Hydrophobia）— 对水的恐惧",
    "火焰恐惧（Pyrophobia）— 对火的恐惧",
    "死亡恐惧（Thanatophobia）— 对死亡的恐惧",
    "活埋恐惧（Taphephobia）— 对被活埋的恐惧",
    "社交恐惧（Social Phobia）— 对人群和社交场合的恐惧",
    "昆虫恐惧（Entomophobia）— 对昆虫的恐惧",
    "爬行动物恐惧（Herpetophobia）— 对蛇和蜥蜴的恐惧",
    "疯狂恐惧（Dementophobia）— 对发疯的恐惧",
    "被注视恐惧（Scopophobia）— 对被人注视的恐惧",
    "不洁恐惧（Mysophobia）— 对污染和细菌的恐惧",
    "雷声恐惧（Brontophobia）— 对雷声和闪电的恐惧",
    "异物恐惧（Xenophobia）— 对陌生人和异物的恐惧",
    "独处恐惧（Monophobia）— 对独自一人的恐惧",
    "怪物恐惧（Teratophobia）— 对怪物的恐惧",
    "触手恐惧 — 对触手和黏滑物体的恐惧",
    # 模组主题扩展
    "幽灵恐惧（Phasmophobia）— 对鬼魂和灵体的恐惧",
    "影子恐惧（Sciophobia）— 对异常阴影的恐惧",
    "镜子恐惧（Catoptrophobia）— 对镜子和反射面的恐惧",
    "文本恐惧（Bibliophobia）— 对古书、手稿和禁忌文字的恐惧",
    "墨水恐惧 — 对墨迹、书写液体和字迹的恐惧",
    "腐败恐惧（Seplophobia）— 对腐烂物体的恐惧",
    "非人恐惧 — 对看似人但非人之物的恐惧（恐怖谷）",
    "低语恐惧 — 对无法辨认来源的低语声的恐惧",
]

# COC 7e 躁狂症表（规则书第八章 + 模组主题扩展）
MANIAS = [
    "纵火狂（Pyromania）— 对纵火的病态执念",
    "偷窃狂（Kleptomania）— 无法抑制的偷窃冲动",
    "说谎癖（Mythomania）— 病态性的编造谎言",
    "偏执狂（Paranoia）— 确信自己被阴谋针对",
    "宗教狂热（Religious Mania）— 认为自己是被选中的先知/救世主",
    "自虐倾向（Masochism）— 从痛苦中获得满足",
    "虐待倾向（Sadism）— 从施加痛苦中获得满足",
    "过度洁癖（Ablutomania）— 强迫性地反复清洗",
    "强迫性计数（Arithmomania）— 无法抑制地数一切东西",
    "饮食强迫（Sitomania）— 对特定食物的病态执念或厌恶",
    "写作强迫（Graphomania）— 无法抑制地记录或书写一切",
    "唱歌强迫（Melomania）— 无法抑制地唱歌或哼曲",
    "囤积癖（Hoarding）— 无法丢弃任何物品",
    "赌博成瘾（Gambling Mania）— 无法抑制的赌博冲动",
    "酗酒倾向（Dipsomania）— 无法控制地大量饮酒",
    "自杀倾向（Suicidal Mania）— 反复出现的自杀冲动",
    "幻觉癖（Hallucinomania）— 主动寻求幻觉体验",
    "权力妄想（Megalomania）— 相信自己拥有超自然力量或非凡身份",
    # 模组主题扩展
    "驱魔执念 — 坚信自己必须驱逐一切超自然存在，不管代价多大",
    "真理追寻狂 — 病态地追寻禁忌知识，明知危险也无法停下",
    "守护妄想 — 坚信自己是唯一能保护他人免受黑暗侵害的人",
    '献祭冲动 — 反复出现的「必须献祭某物才能平息」的念头',
    "记录强迫 — 无法抑制地记录一切超自然见闻，即使记录本身带来危险",
    "仪式偏执 — 坚信必须按特定顺序执行某些动作，否则灾祸将至",
]


def apply_sanity_loss(severity: str = "moderate", source: str = "未知恐怖",
                      silent: bool = False) -> dict:
    """施加理智损失。格式: 成功损失X / 失败损失Y。

    severity 可以是 "minor/moderate/major/catastrophic" 或自定义格式如 "1/1D6"
    """
    state = _load_state()
    pc = state["pc"]
    current_san = pc.get("san", 65)

    # 解析 severity
    loss_formats = {
        "trivial": "0/1",
        "minor": "0/1D4",
        "moderate": "1/1D6+1",
        "major": "1D4/2D6+2",
        "catastrophic": "1D10/1D100"
    }
    loss_fmt = loss_formats.get(severity, severity)

    # 解析 X/Y 格式
    if "/" in loss_fmt:
        success_loss_str, failure_loss_str = loss_fmt.split("/", 1)
    else:
        success_loss_str, failure_loss_str = "0", loss_fmt

    def parse_loss(s: str) -> int:
        s = s.strip()
        if s == "0":
            return 0
        if "D" in s:
            if "+" in s:
                parts = s.split("+")
                d_part = parts[0]
                mod = int(parts[1])
            else:
                d_part = s
                mod = 0
            count, sides = d_part.split("D")
            return sum(random.randint(1, int(sides)) for _ in range(int(count))) + mod
        return int(s)

    success_loss = parse_loss(success_loss_str)
    failure_loss = parse_loss(failure_loss_str)

    # SAN 检定（d100 <= 当前SAN = 成功）
    san_roll = random.randint(1, 100)
    san_check_success = san_roll <= current_san

    if san_check_success:
        loss = success_loss
    else:
        loss = failure_loss

    new_san = max(0, current_san - loss)

    # 触发临时疯狂判断
    temp_insanity = False
    insanity_type = ""
    bout_index = -1
    if loss >= 5:  # 单次损失 >= 5
        int_val = pc.get("attributes", {}).get("INT", 50)
        int_roll = random.randint(1, 100)
        if int_roll <= int_val:
            temp_insanity = True
            bout_index = random.randint(0, len(BOUT_OF_MADNESS) - 1)
            bout_raw = BOUT_OF_MADNESS[bout_index]

            # #9 (index 8): 恐惧症
            if bout_index == 8:
                suggested = random.choice(PHOBIAS)
                profile = pc.setdefault("psychological_profile", {
                    "traits": [], "key_relationships": [],
                    "phobias": [], "manias": []
                })
                # 先写入一个建议条目（守秘人可以通过 set_psychological_trait 覆盖为更有创意的版本）
                profile["phobias"].append({
                    "name": suggested,
                    "trigger_context": f"SAN暴跌{loss}点——当前场景的恐怖源头",
                    "source": "suggested_by_table"
                })
                insanity_type = (
                    f"{bout_raw}\n"
                    f"  → 建议恐惧症：{suggested}\n"
                    f"  → 守秘人可根据创伤场景自由创作更有针对性的恐惧症"
                )
            # #10 (index 9): 躁狂症
            elif bout_index == 9:
                suggested = random.choice(MANIAS)
                profile = pc.setdefault("psychological_profile", {
                    "traits": [], "key_relationships": [],
                    "phobias": [], "manias": []
                })
                profile["manias"].append({
                    "name": suggested,
                    "trigger_context": f"SAN暴跌{loss}点——当前场景的心理冲击",
                    "source": "suggested_by_table"
                })
                insanity_type = (
                    f"{bout_raw}\n"
                    f"  → 建议躁狂症：{suggested}\n"
                    f"  → 守秘人可根据创伤场景自由创作更有针对性的躁狂症"
                )
            else:
                insanity_type = bout_raw

    # 触发不定疯狂判断
    indefinite_insanity = False
    one_day_loss = pc.get("_san_loss_today", 0) + loss
    pc["_san_loss_today"] = one_day_loss
    if one_day_loss >= current_san // 5:
        indefinite_insanity = True

    # 永久疯狂
    permanent = new_san <= 0

    pc["san"] = new_san
    _save_state(state)

    result = {
        "target": "pc",
        "severity": severity,
        "source": source,
        "loss_format": loss_fmt,
        "san_roll": san_roll,
        "san_check_success": san_check_success,
        "success_loss": success_loss,
        "failure_loss": failure_loss,
        "actual_loss": loss,
        "san_before": current_san,
        "san_after": new_san,
        "temporary_insanity": temp_insanity,
        "insanity_type": insanity_type,
        "indefinite_insanity": indefinite_insanity,
        "permanent_insanity": permanent,
    }

    print(json.dumps(result, ensure_ascii=False))

    if permanent:
        print("!!! SAN 降至 0 —— 永久疯狂！角色不再是可扮演角色。", file=sys.stderr)
    elif temp_insanity:
        print(f"!!! 临时疯狂：{insanity_type}", file=sys.stderr)
    elif indefinite_insanity:
        print("!!! 不定疯狂：累积损失触及阈值，症状将持续数周。", file=sys.stderr)

    return result


def sanity_restore(amount: int = 0):
    """恢复理智值"""
    state = _load_state()
    pc = state["pc"]
    current = pc.get("san", 65)
    max_san = 99 - pc.get("skills", {}).get("cthulhu_mythos", 0)
    new_san = min(max_san, current + amount)
    pc["san"] = new_san
    _save_state(state)
    print(json.dumps({"restore": amount, "san_before": current, "san_after": new_san, "max_san": max_san},
                     ensure_ascii=False))


def psychoanalysis(target: str = "pc") -> dict:
    """精神分析治疗：进行 psychoanalysis 技能检定，成功恢复 1D3 SAN。
    同一目标同一天只能受益一次。可以暂时压制恐惧症副作用（1小时）。
    """
    state = _load_state()

    # 获取治疗者的 psychoanalysis 技能值
    if target == "pc":
        healer_skill = state["pc"]["skills"].get("psychoanalysis", 1)
        patient = state["pc"]
    else:
        # 对 NPC 使用：默认治疗者有基础技能
        healer_skill = 50
        npcs = state.get("npcs", [])
        patient = None
        for n in npcs:
            if n.get("id") == target:
                patient = n
                break
        if patient is None:
            print(json.dumps({"error": f"NPC '{target}' 不存在"}, ensure_ascii=False))
            return {"error": f"NPC '{target}' 不存在"}

    # 检定
    roll = random.randint(1, 100)
    if roll <= 1:
        level = "critical_success"
    elif roll <= max(1, healer_skill // 5):
        level = "extreme_success"
    elif roll <= max(1, healer_skill // 2):
        level = "hard_success"
    elif roll <= healer_skill:
        level = "regular_success"
    else:
        level = "failure"

    success = level != "failure"

    if success:
        restore_amount = random.randint(1, 3)
        if level in ("extreme_success", "critical_success"):
            restore_amount += 2  # 极难/大成功额外恢复
        current_san = patient.get("san", 65)
        max_san = 99 - patient.get("skills", {}).get("cthulhu_mythos", 0)
        new_san = min(max_san, current_san + restore_amount)
        patient["san"] = new_san
        _save_state(state)

        result = {
            "action": "psychoanalysis",
            "target": target,
            "roll": roll,
            "skill_value": healer_skill,
            "level": level,
            "success": True,
            "san_restored": restore_amount,
            "san_before": current_san,
            "san_after": new_san,
            "side_effect": "恐惧症/躁狂症副作用被暂时压制1小时（如适用）"
        }
    else:
        result = {
            "action": "psychoanalysis",
            "target": target,
            "roll": roll,
            "skill_value": healer_skill,
            "level": "failure",
            "success": False,
            "san_restored": 0,
            "note": "同一天内不可对同一目标再次尝试"
        }

    print(json.dumps(result, ensure_ascii=False))
    return result


def reality_check() -> dict:
    """现实认知检定：用于处于潜在疯狂期的调查员鉴别幻觉。
    SAN 检定（d100 ≤ 当前SAN）。仅限 PC。
    """
    state = _load_state()
    pc = state["pc"]
    current_san = pc.get("san", 65)
    roll = random.randint(1, 100)
    success = roll <= current_san

    if success:
        result = {
            "action": "reality_check",
            "roll": roll,
            "current_san": current_san,
            "success": True,
            "effect": "看穿幻觉——守秘人描述真实所见。获得暂时抗性，直到下一次SAN损失前免疫同类型幻觉。"
        }
    else:
        new_san = max(0, current_san - 1)
        pc["san"] = new_san
        _save_state(state)
        result = {
            "action": "reality_check",
            "roll": roll,
            "current_san": current_san,
            "success": False,
            "san_loss": 1,
            "san_after": new_san,
            "effect": "未能看穿幻觉——失去1点SAN。若处于潜在疯狂期，立刻触发疯狂发作。"
        }

    print(json.dumps(result, ensure_ascii=False))
    return result


def sanity_check():
    """查看当前理智状态"""
    state = _load_state()
    pc = state["pc"]
    san = pc.get("san", 65)
    max_san = 99 - pc.get("skills", {}).get("cthulhu_mythos", 0)
    result = {"san": san, "max_san": max_san, "ratio": round(san / max_san, 2)}
    print(json.dumps(result, ensure_ascii=False))


def _dispatch():
    if len(sys.argv) < 2:
        print("用法: python sanity.py loss <severity>")
        print("      python sanity.py restore <amount>")
        print("      python sanity.py check")
        print("      python sanity.py psychoanalysis [target]")
        print("      python sanity.py reality-check")
        print("")
        print("severity: trivial/minor/moderate/major/catastrophic")
        print("          或自定义 X/Y 格式如 '1/1D6'")
        sys.exit(1)

    action = sys.argv[1]

    if action == "loss":
        severity = sys.argv[2] if len(sys.argv) > 2 else "moderate"
        apply_sanity_loss(severity)
    elif action == "restore":
        amount = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        sanity_restore(amount)
    elif action == "check":
        sanity_check()
    elif action == "psychoanalysis":
        target = sys.argv[2] if len(sys.argv) > 2 else "pc"
        psychoanalysis(target)
    elif action == "reality-check":
        reality_check()
    else:
        print(f"未知动作: {action}")


def main():
    global _TRANSACTION_STATE
    with STORE.transaction() as state:
        _TRANSACTION_STATE = state
        try:
            _dispatch()
        finally:
            _TRANSACTION_STATE = None


if __name__ == "__main__":
    main()
