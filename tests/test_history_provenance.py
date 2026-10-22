"""Stable history identity and atomic compaction boundaries, without model calls."""

import copy
import json
from types import SimpleNamespace as NS

import pytest

from src.ai.context.context_summary import validate_compacted_surface
from src.ai.context.history_compactor import build_summary_batches
from src.ai.context.message_provenance import provider_message
from src.ai.context.narrative_history import restore_tagged_speakers
from src.ai.context.prompt_composition import describe_prompt
from src.ai.model.dialogue_output import DIALOGUE_INSTRUCTION
from src.storage.turn_journal import serialize_messages


def record(turn_id, speaker):
    return {"turn_id": turn_id, "narrative": "请进。", "narrative_segments": [
        {"kind": "speech", "npc_id": speaker, "text": "请进。"}]}


@pytest.mark.parametrize("source,speaker", [("t1", "jin"), ("t2", "su")])
def test_same_text_uses_exact_turn_identity(source, speaker):
    messages = [{"role": "assistant", "content": "请进。", "_trpg_turn_id": source}]
    before = copy.deepcopy(messages)
    result = restore_tagged_speakers(messages, NS(list_completed=lambda **_: [record("t1", "jin"), record("t2", "su")]))
    assert f"【npc:{speaker}】" in result[0]["content"]
    assert messages == before
    assert serialize_messages(result)[0]["_trpg_turn_id"] == source
    assert not any(key.startswith("_trpg_") for key in provider_message(result[0]))


@pytest.mark.parametrize("metadata", [{}, {"_trpg_turn_id": "other-branch"}])
def test_ambiguous_or_foreign_turn_is_reference_not_format_example(metadata):
    message = {"role": "assistant", "content": "请进。", **metadata}
    result = restore_tagged_speakers([message], NS(list_completed=lambda **_: [record("t1", "jin"), record("t2", "su")]))[0]
    assert result["content"] == "请进。"
    assert result["_trpg_history_kind"] == "legacy_unattributed"
    transmitted = provider_message(result)
    assert transmitted["role"] == "user"
    assert "归属未知" in transmitted["content"]


def test_summary_and_tool_batch_are_not_split_or_promoted_to_dialogue():
    calls = [{"id": f"c{n}", "function": {"name": "inspect", "arguments": "{}"}} for n in range(2)]
    messages = [
        {"role": "user", "content": "旧摘要事项", "_trpg_history_kind": "summary"},
        {"role": "assistant", "tool_calls": calls, "content": None},
        {"role": "tool", "tool_call_id": "c0", "content": "任意工具输出"},
        {"role": "tool", "tool_call_id": "c1", "content": "任意工具输出"},
        {"role": "assistant", "tool_calls": None, "content": "完成"},
    ]
    batches = build_summary_batches(messages, max_chars=10)
    assert "previous_summary_reference" in batches[0]
    paired = [batch for batch in batches if "inspect" in batch]
    assert len(paired) == 1
    assert "任意工具输出" not in "\n".join(batches)
    assert validate_compacted_surface(messages) is None


@pytest.mark.parametrize("messages", [
    [{"role": "tool", "tool_call_id": "orphan", "content": "x"}],
    [{"role": "assistant", "tool_calls": [{"id": "same"}, {"id": "same"}]}],
    [{"role": "assistant", "content": "【npc:jin】x【npc:su】y【/npc】【/npc】"}],
])
def test_invalid_compaction_surface_is_rejected(messages):
    assert validate_compacted_surface(messages)


def test_prompt_composition_measures_sources_without_prompt_bodies():
    secret = "PRIVATE_FACTS_AND_KEY"
    result = describe_prompt([
        {"role": "system", "content": f"{secret}\n\n---\n\n{secret}\n" + DIALOGUE_INSTRUCTION},
        {"role": "user", "content": "[引擎权威状态｜仅供守秘人，不得复述]" + secret},
    ])
    assert result["duplicate_block_count"] == 1
    assert result["system_parts"][-1]["copies"] == 1
    assert result["history_parts"]["authority"]["chars"] > 0
    assert secret not in json.dumps(result)


def test_summary_excludes_authority_suffix_from_player_action():
    messages = [{"role": "user", "content": "[玩家行动] 研究摹本\n\n[引擎权威状态｜仅供守秘人，不得复述]PRIVATE_MODULE_FACT"}]
    batches = build_summary_batches(messages)
    assert "研究摹本" in batches[0]
    assert "PRIVATE_MODULE_FACT" not in batches[0]


def test_compiled_module_embedded_frozen_skill_is_loaded_once(tmp_path, monkeypatch):
    from src.storage import persistence

    frozen = "# Keeper notes\n\n" + "不可变的模组事实。" * 160
    (tmp_path / "module.md").write_text("# 模组\n\n" + frozen)
    ctx = NS(module_dir=tmp_path)
    monkeypatch.setattr(persistence, "_catalog_skill_parts", lambda *a, **kw: (["platform"], [frozen], object()))
    monkeypatch.setattr(persistence, "_on_demand_catalog_section", lambda *a: "")
    prompt = persistence.load_system_prompt(ctx, profile="full")
    assert prompt.count(frozen) == 1
    # A different frozen version must remain even when disk embeds newer prose.
    older = frozen + "\n旧规则也必须保留。"
    monkeypatch.setattr(persistence, "_catalog_skill_parts", lambda *a, **kw: (["platform"], [older], object()))
    assert older in persistence.load_system_prompt(ctx, profile="full")


def test_compiled_guide_sections_deduplicate_without_removing_unique_pin_text(tmp_path, monkeypatch):
    from src.storage import persistence

    shared = "# 模组线索章节\n" + "不可变的模组事实。" * 160
    unique = "冻结版本特有的裁决限制。"
    frozen = "<!-- trpg-master:prompt-role=spine -->\n# 旧指南\n" + unique + "\n\n---\n\n" + shared
    (tmp_path / "module.md").write_text("# 作者指南\n\n" + shared)
    monkeypatch.setattr(persistence, "_catalog_skill_parts", lambda *a, **kw: (["platform"], [frozen], object()))
    monkeypatch.setattr(persistence, "_on_demand_catalog_section", lambda *a: "")
    prompt = persistence.load_system_prompt(NS(module_dir=tmp_path), profile="full")
    assert prompt.count(shared) == 1
    assert unique in prompt


def test_summarizer_prompt_declares_the_same_shape_as_its_validator():
    from src.ai.context.history_compactor import HistoryCompactor

    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return NS(choices=[NS(message=NS(content='{"events":[],"known_facts":[],"open_threads":[],"current_scene":"书房","checks":[]}'))])
    client = NS(chat=NS(completions=NS(create=create)))
    result = HistoryCompactor.try_model(client, "deepseek-flash", "[user]: 研究摹本")
    from src.ai.context.context_summary import validate_summary_shape

    assert validate_summary_shape(result).allowed
    prompt = captured["messages"][-1]["content"]
    assert all(key in prompt for key in ("events", "known_facts", "open_threads", "current_scene", "checks"))
    assert captured["extra_body"]["thinking"]["type"] == "disabled"
