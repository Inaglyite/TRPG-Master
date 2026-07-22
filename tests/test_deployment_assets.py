from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_backup_script_rejects_paths_outside_managed_roots() -> None:
    script = PROJECT_ROOT / "deploy" / "backup-trpg-master.sh"
    env = {**os.environ, "TRPG_BACKUP_ROOT": "/tmp/not-an-approved-backup-root"}

    result = subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unsafe backup root" in result.stderr


def test_staging_release_contains_isolated_backup_units() -> None:
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "deploy-multiplayer-staging.yml"
    ).read_text(encoding="utf-8")
    service = (
        PROJECT_ROOT / "deploy" / "trpg-master-staging-backup.service"
    ).read_text(encoding="utf-8")
    timer = (
        PROJECT_ROOT / "deploy" / "trpg-master-staging-backup.timer"
    ).read_text(encoding="utf-8")

    assert "schemas deploy frontend/dist" in workflow
    assert "TRPG_BACKUP_ROOT=/var/backups/trpg-master-staging" in service
    assert "TRPG_BACKUP_RUNTIME_ROOT=/var/lib/trpg-master-staging" in service
    assert "EnvironmentFile=/etc/trpg-master/staging.env" in service
    assert "Unit=trpg-master-staging-backup.service" in timer
