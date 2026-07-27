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

    script_text = script.read_text(encoding="utf-8")
    assert 'GNUPGHOME="${GNUPGHOME:-$work/gnupg}"' in script_text
    assert "--pinentry-mode loopback" in script_text
    assert "sha256sum database.dump runtime.tar.gz > SHA256SUMS" in script_text
    assert 'sha256sum "$work/database.dump"' not in script_text


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

    assert "schemas rules deploy frontend/dist" in workflow
    assert "scp deploy/install-staging-release.sh" in workflow
    assert "sudo bash '/tmp/trpg-install-staging-$RELEASE_SHA.sh'" in workflow
    assert "/usr/local/sbin/trpg-install-staging-release" not in workflow
    assert "TRPG_BACKUP_ROOT=/var/backups/trpg-master-staging" in service
    assert "TRPG_BACKUP_RUNTIME_ROOT=/var/lib/trpg-master-staging" in service
    assert "EnvironmentFile=/etc/trpg-master/staging.env" in service
    assert "Unit=trpg-master-staging-backup.service" in timer


def test_production_release_is_same_origin_complete_and_resource_bounded() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "deploy-azure.yml").read_text(
        encoding="utf-8"
    )
    quality = (PROJECT_ROOT / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )
    service = (PROJECT_ROOT / "deploy" / "trpg-master.service").read_text(
        encoding="utf-8"
    )
    nginx = (PROJECT_ROOT / "deploy" / "nginx-trpg-master.conf").read_text(
        encoding="utf-8"
    )

    assert "schemas rules deploy frontend/dist" in workflow
    assert "VITE_TRPG_BACKEND_ORIGIN" not in workflow
    assert "scp deploy/install-release.sh" in workflow
    assert "sudo bash '/tmp/trpg-install-release-$RELEASE_SHA.sh'" in workflow
    assert "/usr/local/sbin/trpg-install-release" not in workflow
    assert "TRPG_TEST_POSTGRES_URL" in quality
    assert "alembic upgrade head" in quality
    assert "TRPG_MAX_ACTIVE_ROOMS=2" in service
    assert "TRPG_DB_POOL_SIZE=3" in service
    assert "--workers 1" in service
    assert "auth_basic" not in nginx
    assert "client_max_body_size 64m" in nginx
    assert "limit_req zone=trpg_auth" in nginx


def test_production_deploy_provenance_and_manual_dispatch_quality_gate() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "deploy-azure.yml").read_text(
        encoding="utf-8"
    )

    assert "branches: [master]" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.head_branch == 'master'" in workflow
    assert (
        "github.event.workflow_run.head_repository.full_name == github.repository"
        in workflow
    )
    assert "github.ref == 'refs/heads/master'" in workflow
    assert "postgres:17-alpine" in workflow
    assert 'python-version: "3.12"' in workflow
    assert "pip install -r requirements-dev.txt" in workflow
    assert "ruff check ." in workflow
    assert "python tools/check_architecture.py" in workflow
    assert 'TRPG_DATABASE_URL="$TRPG_TEST_POSTGRES_URL" alembic upgrade head' in workflow
    assert "python -m pytest -q" in workflow
    assert "npm test" in workflow
    assert "npm run format:check" in workflow
    assert "npm run build" in workflow
    assert "playwright install --with-deps chromium" in workflow
    assert "xvfb-run --auto-servernum npm run test:e2e" in workflow


def test_release_installers_stage_atomically_and_install_managed_assets() -> None:
    cases = (
        (
            "install-release.sh",
            "trpg-master.service",
            "trpg-master-backup.service",
            "trpg-master-backup.timer",
            "nginx-trpg-master.conf",
            "trpg-install-release",
            "http://127.0.0.1:8765/api/ready",
        ),
        (
            "install-staging-release.sh",
            "trpg-master-staging.service",
            "trpg-master-staging-backup.service",
            "trpg-master-staging-backup.timer",
            "nginx-trpg-master-staging.conf",
            "trpg-install-staging-release",
            "http://127.0.0.1:8766/api/ready",
        ),
    )

    for (
        installer_name,
        app_service,
        backup_service,
        backup_timer,
        nginx_config,
        installed_name,
        health_url,
    ) in cases:
        installer_path = PROJECT_ROOT / "deploy" / installer_name
        installer = installer_path.read_text(encoding="utf-8")

        assert installer_path.stat().st_mode & 0o111
        assert 'exec 9>"$root/.install.lock"' in installer
        assert 'mktemp -d "$root/releases/.install-$release_id-XXXXXX"' in installer
        assert '.release-complete' in installer
        assert 'mv -- "$candidate" "$release"' in installer
        assert "moving incomplete" in installer
        assert "rollback_release" in installer
        assert "restore_managed_path" in installer
        assert "install_managed_file" in installer
        assert f'deploy/{app_service}"' in installer
        assert f'deploy/{backup_service}"' in installer
        assert f'deploy/{backup_timer}"' in installer
        assert f'deploy/{nginx_config}"' in installer
        assert f"/usr/local/sbin/{installed_name}" in installer
        assert "systemctl daemon-reload" in installer
        assert "nginx -t" in installer
        assert 'systemctl enable --now "$backup_timer"' in installer
        assert health_url in installer

        result = subprocess.run(
            ["bash", "-n", str(installer_path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_desktop_launcher_keeps_electron_sandbox_enabled() -> None:
    launcher = (PROJECT_ROOT / "start_desktop.sh").read_text(encoding="utf-8")

    assert "--no-sandbox" not in launcher
    assert "node_modules/.bin/electron ." in launcher


def test_windows_packaging_enforces_python_312() -> None:
    script = (PROJECT_ROOT / "packaging" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert "Install Python 3.12+ first." in script
    assert '[version]$PythonVersion -lt [version]"3.12"' in script
    assert "Python 3.11" not in script
