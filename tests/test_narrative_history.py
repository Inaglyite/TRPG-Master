from __future__ import annotations

from src.ai.context.narrative_history import enrich_public_history_record


class _Engine:
    @staticmethod
    def is_valid_npc_id(npc_id: str) -> bool:
        return npc_id == "bryce_fallon"

    @staticmethod
    def log_unknown_npc_speaker(_npc_id: str) -> None:
        return None

    @staticmethod
    def npc_speaker_aliases() -> dict[str, str]:
        return {}


class _EngineWithFallonAlias(_Engine):
    @staticmethod
    def npc_speaker_aliases() -> dict[str, str]:
        return {"法伦": "bryce_fallon"}


def test_public_history_sanitizes_protocol_and_recovers_speaker_segments() -> None:
    source = {
        "turn_id": "turn-1",
        "narrative": (
            "守秘人旁白。"
            "【npc:bryce_fallon】法伦说话。【/npc】"
            '<|DSML|tool_calls><|DSML|invoke name="npc_reveal">'
            "秘密</|DSML|invoke></|DSML|tool_calls>"
        ),
        "narrative_segments": [{"kind": "narration", "text": "旧版未归因正文"}],
    }

    result = enrich_public_history_record(
        source,
        _Engine(),
        resolve_speaker=lambda npc_id: {
            "type": "npc",
            "id": npc_id,
            "name": "布莱斯·法伦",
        },
    )

    assert "DSML" not in result["narrative"]
    assert any(
        segment.get("kind") == "speech"
        and segment.get("npc_id") == "bryce_fallon"
        and segment.get("speaker", {}).get("name") == "布莱斯·法伦"
        for segment in result["narrative_segments"]
    )
    assert source["narrative_segments"] == [
        {"kind": "narration", "text": "旧版未归因正文"}
    ]


def test_public_history_does_not_promote_player_dialogue_to_npc_speech() -> None:
    narrative = "黄千陆向法伦说：“我是正义的警察。”"
    source = {
        "turn_id": "turn-player-dialogue",
        "narrative": narrative,
        "narrative_segments": [{"kind": "narration", "text": narrative}],
    }

    result = enrich_public_history_record(source, _EngineWithFallonAlias())

    assert len(result["narrative_segments"]) == 1
    segment = result["narrative_segments"][0]
    assert segment["kind"] == "narration"
    assert segment["text"] == narrative
    assert segment["speaker"]["type"] == "keeper"
    assert "npc_id" not in segment
