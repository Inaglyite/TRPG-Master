from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.app.config import PROJECT_ROOT
from src.modules.module_registry import ModuleRegistry, build_package
from src.multiplayer.world_creation import create_owned_world
from src.storage.database import Base, User, World, get_engine, session_scope
from tests.test_module_packages import TEMPLATE
from tests.test_solo_play_mode import ORIGIN, cloud_client


def test_personal_module_supports_multiplayer_but_is_not_shared(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'personal-content.db'}"
    Base.metadata.create_all(get_engine(url))
    package = tmp_path / "personal.trpgmod"
    build_package(TEMPLATE, package)
    server, env_patch, db_patch = cloud_client(url)
    runtime = tmp_path / "runtime"
    registry = ModuleRegistry(PROJECT_ROOT, runtime)

    with (
        env_patch,
        db_patch,
        patch.object(server, "RUNTIME_ROOT", runtime),
        patch.object(server, "MODULE_REGISTRY", registry),
        TestClient(server.app, base_url="https://testserver") as client,
    ):
        assert client.post(
            "/api/auth/register",
            json={"username": "personal_a", "password": "password-a-123"},
        ).status_code == 201
        imported = client.post(
            "/api/me/modules/import",
            content=package.read_bytes(),
            headers={**ORIGIN, "Content-Type": "application/vnd.trpg-master.module+zip"},
        )
        assert imported.status_code == 201
        personal_id = imported.json()["module"]["id"]

        assert client.post(
            "/api/auth/register",
            json={"username": "personal_b", "password": "password-b-123"},
        ).status_code == 201
        assert personal_id not in {
            item["id"] for item in client.get("/api/modules").json()["modules"]
        }
        refused = client.post(
            "/api/worlds",
            json={"module": personal_id, "name": "越权房间", "max_players": 2},
            headers=ORIGIN,
        )
        assert refused.status_code == 404

        assert client.post("/api/auth/logout", headers=ORIGIN).status_code == 204
        assert client.post(
            "/api/auth/login",
            json={"username": "personal_a", "password": "password-a-123"},
        ).status_code == 200
        assert personal_id in {
            item["id"] for item in client.get("/api/modules").json()["modules"]
        }
        with session_scope(url) as session:
            creator_id = session.query(User).filter_by(username="personal_a").one().id
        created = asyncio.run(
            create_owned_world(
                database_url=url,
                creator_id=creator_id,
                creator_username="personal_a",
                data={"module": personal_id, "name": "个人模组多人房", "max_players": 2},
                module_registry=registry,
                default_module_name="mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=runtime,
            )
        )
        with session_scope(url) as session:
            world = session.get(World, created["world_id"])
            assert world is not None
            assert world.metadata_json["module_title"] == "低语档案馆"
