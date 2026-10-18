"""系统提示装配的单一输出格式契约回归。

dialogue_v1（JSON + choices + speaker_id）之后，旧正文格式契约（【npc:…】
发言标签、"你可以——" 正文选项菜单）不得再从任何来源进入最终请求，
包括旧 pin 冻结的 core skill 与已安装的旧版模组包。
"""

import json
import re

from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.storage.persistence import load_system_prompt, neutralize_legacy_format_contract

LEGACY_CONTRACT_SAMPLE = (
    "## 核心NPC扮演指南\n\n"
    "**发言标记（输出格式契约）：** NPC 以直接引语说话时，台词必须用\n"
    "`【npc:<npc_id>】…【/npc】` 包裹（如 `【npc:keeper】\"……\"【/npc】`）。\n"
    "仅用于真正说出口的台词；提及、回忆、转述时绝不使用。\n\n"
    "### 店主（keeper）\n\n"
    "- 调查员鲁莽时合作度下降：【npc:keeper】\"我给你们线索，不是给你们棺材。\"【/npc】\n"
)

# 旧 pin 里 core skill 的菜单模板整块（含“选项格式：”引导行）。
LEGACY_MENU_TEMPLATE = (
    "选项格式：\n```\n你可以——\n1. {选项一}\n2. {选项二}\n3. {选项三}\n"
    "4. [自由行动] 你决定做什么？\n```\n"
)


def test_tag_contract_block_removed_but_speech_samples_kept():
    out = neutralize_legacy_format_contract(LEGACY_CONTRACT_SAMPLE)
    assert "发言标记" not in out
    assert "【npc" not in out and "【/npc】" not in out
    # 扮演样本保留，只剥会被模型模仿的标签
    assert "我给你们线索，不是给你们棺材" in out
    assert "keeper：" in out


def test_menu_template_block_downgraded():
    out = neutralize_legacy_format_contract(LEGACY_MENU_TEMPLATE)
    assert "你可以——" not in out
    assert "输出格式契约" in out


def test_menu_instruction_inside_json_block_safely_rewritten():
    sample = (
        "```json\n{\n"
        '  "opening_prompt": "演开场。最后必须另起一个以\\"**你可以——**\\"'
        '开头的正文块，依次列出四个选项。"\n}\n```'
    )
    out = neutralize_legacy_format_contract(sample)
    assert "你可以——" not in out
    payload = re.search(r"```json\n(.*?)```", out, re.DOTALL).group(1)
    # 结构化数据块不整段删除，JSON 仍合法，选项内容保留
    assert json.loads(payload)["opening_prompt"].endswith("按当前输出格式契约给出依次列出四个选项。")


def test_module_facts_survive_neutralization():
    sample = (
        "### 店主（keeper）\n\n"
        "**visible_tags:** 五十岁、语速慢\n"
        "**disposition:** guarded\n\n"
        "- 提到旧事时压低声音\n"
    )
    assert neutralize_legacy_format_contract(sample) == sample


def test_plot_text_happening_to_mention_wrapping_is_not_deleted():
    """剧情正文恰好出现“必须/包裹”与台词样本，不是格式契约，不得整段删除。"""
    sample = "线索：钥匙必须用红布包裹。NPC说：【npc:jin_sanye】“钥匙在地窖。”【/npc】"
    out = neutralize_legacy_format_contract(sample)
    assert "钥匙必须用红布包裹" in out
    assert "钥匙在地窖" in out and "【npc" not in out


def test_plot_line_mentioning_tag_and_speech_is_not_deleted():
    """“台词”“标签”词共现的剧情句不得误删（作者线索保护）。"""
    sample = "金三爷的台词：“货物标签上的买家是奥斯古德。”"
    assert neutralize_legacy_format_contract(sample) == sample


def test_heading_and_embedded_json_block_survive():
    """带标题再接围栏 JSON 的段落按数据处理：不整段删除，键值完整保留。"""
    sample = (
        "# 结构化模组定义\n```json\n{\n"
        '  "scene_note": "证物标签必须存档",\n  "clue": "标签上的指纹"\n}\n```'
    )
    out = neutralize_legacy_format_contract(sample)
    assert "# 结构化模组定义" in out
    payload = json.loads(re.search(r"```json\n(.*?)```", out, re.DOTALL).group(1))
    assert payload == {"scene_note": "证物标签必须存档", "clue": "标签上的指纹"}


def test_old_pin_tag_instruction_block_and_fenced_example_removed():
    """旧 pin 的 core skill 残留：标题、指令、围栏示例、说明行要一并清掉。"""
    sample = (
        "### 发言标记（输出格式契约，强制）\n\n"
        "NPC 以直接引语说话时，必须用标签包裹其台词：\n\n"
        "```\n【npc:<npc_id>】\"台词原文\"【/npc】\n```\n\n"
        "- 标签内只放台词本身（保留引号）；动作神态放在标签之外。\n"
        "- 同一段叙述中可有多个 NPC 先后发言，各自的台词分别包裹。\n\n"
        "示例：\n他沉默片刻，避开你的目光。\n"
        "【npc:butler_gregory】\"有些东西……确实会留下来。\"【/npc】\n"
        "他说完，下意识看了一眼上锁的抽屉。\n"
    )
    out = neutralize_legacy_format_contract(sample)
    assert "【npc" not in out and "标签" not in out and "npc_id" not in out
    # 扮演示例的台词文本保留（以“id：”形式），只去掉标签写法
    assert "有些东西……确实会留下来。" in out
    assert "他沉默片刻" in out


def test_assembled_prompt_has_single_output_contract(tmp_path):
    context = RuntimeContext.create(
        "format-contract-world",
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )
    for profile in ("full", "opening"):
        prompt = load_system_prompt(context, profile=profile)
        assert "【npc:" not in prompt
        assert "你可以——" not in prompt
        assert "必须另起一个以" not in prompt
