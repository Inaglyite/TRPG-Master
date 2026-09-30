import json
from pathlib import Path
from types import SimpleNamespace

from src.web.asset_payload import speaker_payload


class StubStore:
    def __init__(self, state: dict):
        self.state = state

    def load(self) -> dict:
        return self.state


def test_speaker_identity_uses_module_catalog_before_handout_reveal(tmp_path: Path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "bryce.png").write_bytes(b"portrait")
    initial_state_file = tmp_path / "world_state_initial.json"
    initial_state_file.write_text(
        json.dumps(
            {
                "asset_map": {
                    "npcs": {
                        "bryce_fallon": {
                            "file": "bryce.png",
                            "label": "布莱斯·法伦",
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state = {
        "npcs": [{"id": "bryce_fallon", "name": "布莱斯·法伦"}],
        "asset_map": {"npcs": {}},
        "seen_handouts": {"npcs": []},
    }
    context = SimpleNamespace(
        world_store=StubStore(state),
        initial_state_file=initial_state_file,
        assets_dir=assets_dir,
        module_name="猩红文档",
    )

    payload = speaker_payload("bryce_fallon", SimpleNamespace(context=context))

    assert payload is not None
    assert payload["name"] == "布莱斯·法伦"
    assert payload["avatar"]["asset_url"].endswith("/bryce.png")
    assert payload["avatar"]["asset_data_uri"].startswith("data:image/png;base64,")
    assert state["seen_handouts"]["npcs"] == []


def test_speaker_payload_prefers_public_display_name(tmp_path: Path):
    """模组声明 display_name 的 NPC：发言气泡载荷用公开称呼，不泄露真名。"""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    initial_state_file = tmp_path / "world_state_initial.json"
    initial_state_file.write_text("{}", encoding="utf-8")
    state = {
        "npcs": [
            {
                "id": "bai_meigui",
                "name": "白玫瑰",
                "display_name": "红歌星",
            }
        ],
        "asset_map": {"npcs": {}},
    }
    context = SimpleNamespace(
        world_store=StubStore(state),
        initial_state_file=initial_state_file,
        assets_dir=assets_dir,
        module_name="m",
    )

    payload = speaker_payload("bai_meigui", SimpleNamespace(context=context))

    assert payload is not None
    assert payload["name"] == "红歌星"
    assert "白玫瑰" not in json.dumps(payload, ensure_ascii=False)
