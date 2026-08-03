from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_backup_script_rejects_paths_outside_managed_roots() -> None:
    script = PROJECT_ROOT / "deploy" / "backup-trpg-master.sh"
    invalid_roots = (
        (
            {"TRPG_BACKUP_ROOT": "/tmp/not-an-approved-backup-root"},
            "unsafe backup root",
        ),
        (
            {"TRPG_BACKUP_ROOT": "/var/backups/trpg-master-../../tmp"},
            "unsafe backup root",
        ),
        (
            {
                "TRPG_BACKUP_ROOT": "/var/backups/trpg-master",
                "TRPG_BACKUP_RUNTIME_ROOT": "/var/lib/trpg-master-../../etc",
            },
            "unsafe runtime root",
        ),
    )
    for overrides, expected_error in invalid_roots:
        result = subprocess.run(
            ["bash", str(script)],
            cwd=PROJECT_ROOT,
            env={**os.environ, **overrides},
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert expected_error in result.stderr

    script_text = script.read_text(encoding="utf-8")
    assert 'export GNUPGHOME="$work/gnupg"' in script_text
    assert 'exec 9<"$backup_root"' in script_text
    assert "flock --exclusive 9" in script_text
    assert "TRPG_BACKUP_LOCK_HELD" not in script_text
    assert 'realpath -e -- "$path"' in script_text
    assert (
        'mktemp "$backup_root/.${backup_prefix}-${stamp}.partial.XXXXXX"'
        in script_text
    )
    assert '--decrypt "$partial"' in script_text
    assert 'mv --no-clobber -- "$partial" "$candidate"' in script_text
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
    assert "zone=trpg_api:10m rate=20r/s" in nginx
    assert "limit_req zone=trpg_api burst=60 nodelay" in nginx
    assert "limit_req_status 429" in nginx
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains" always' in nginx
    assert "X-Forwarded-For $remote_addr" in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx


def test_staging_nginx_global_rate_limit_and_hsts() -> None:
    nginx = (PROJECT_ROOT / "deploy" / "nginx-trpg-master-staging.conf").read_text(
        encoding="utf-8"
    )

    assert "zone=trpg_staging_api:10m rate=20r/s" in nginx
    assert "limit_req zone=trpg_staging_api burst=60 nodelay" in nginx
    assert "limit_req_status 429" in nginx
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains" always' in nginx
    assert "X-Forwarded-For $remote_addr" in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx


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
        service = (PROJECT_ROOT / "deploy" / app_service).read_text(
            encoding="utf-8"
        )
        backup_service_source = (
            PROJECT_ROOT / "deploy" / backup_service
        ).read_text(encoding="utf-8")

        assert installer_path.stat().st_mode & 0o111
        assert 'exec 9>"$root/.install.lock"' in installer
        assert "service_was_active=0" in installer
        assert (
            'systemctl is-active --quiet "$service_name"'
            in installer
        )
        assert 'mktemp -d "$root/releases/.install-$release_id-XXXXXX"' in installer
        assert 'mktemp "$root/releases/.archive-$release_id-XXXXXX"' in installer
        assert '.release-complete' in installer
        assert 'mv -- "$candidate" "$release"' in installer
        assert "moving incomplete" in installer
        assert "cleanup_stale_release_artifacts" in installer
        assert "-mmin +1440 -print0" in installer
        assert "rollback_release" in installer
        assert "previous release did not recover readiness" in installer.replace(
            "previous staging release did not recover readiness",
            "previous release did not recover readiness",
        )
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
        assert 'chown -R trpgdeploy:trpgdeploy "$candidate"' not in installer
        assert (
            'install -d -m 0700 -o trpgdeploy -g trpgdeploy "$candidate/.venv"'
            in installer
        )
        assert 'chown -R root:root "$candidate/.venv"' in installer
        assert "! -user root -o -perm /022" in installer
        assert "UMask=0077" in service
        assert "UMask=0077" in backup_service_source

        result = subprocess.run(
            ["bash", "-n", str(installer_path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def _installer_function(source: str, name: str, next_name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index(f"\n\n{next_name}() {{", start)
    return source[start:end] + "\n"


def _run_rollback_harness(
    tmp_path: Path,
    *,
    service_was_active: bool,
    ready: bool,
) -> subprocess.CompletedProcess[str]:
    source = (PROJECT_ROOT / "deploy" / "install-release.sh").read_text(
        encoding="utf-8"
    )
    wait_function = _installer_function(
        source,
        "wait_for_service_ready",
        "backup_managed_path",
    )
    rollback_function = _installer_function(
        source,
        "rollback_release",
        "on_exit",
    )
    root = tmp_path / "root"
    previous = root / "releases" / "abcdef0"
    previous.mkdir(parents=True)
    calls = tmp_path / "calls"
    harness = f"""
set -Eeuo pipefail
root="$1"
previous="$2"
CALLS="$3"
service_name=trpg-master.service
backup_timer=trpg-master-backup.timer
monitor_timer=trpg-master-monitor.timer
health_url=http://127.0.0.1:8765/api/ready
service_was_enabled=1
service_was_active={int(service_was_active)}
timer_was_enabled=1
timer_was_active=0
monitor_timer_was_enabled=1
monitor_timer_was_active=0
unit_dir=/tmp/unit
nginx_enabled=/tmp/nginx-enabled
nginx_available=/tmp/nginx-available
installer_target=/tmp/installer
restore_managed_path() {{ :; }}
systemctl() {{ printf 'systemctl %s\\n' "$*" >>"$CALLS"; }}
nginx() {{ return 0; }}
journalctl() {{ return 0; }}
sleep() {{ :; }}
curl() {{
    printf 'curl\\n' >>"$CALLS"
    return {0 if ready else 1}
}}
{wait_function}
{rollback_function}
rollback_release
"""
    return subprocess.run(
        ["bash", "-s", "--", str(root), str(previous), str(calls)],
        input=harness,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_rollback_restores_original_inactive_service_state(
    tmp_path: Path,
) -> None:
    result = _run_rollback_harness(
        tmp_path,
        service_was_active=False,
        ready=True,
    )
    calls = (tmp_path / "calls").read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "systemctl stop trpg-master.service" in calls
    assert "systemctl restart trpg-master.service" not in calls
    assert "curl" not in calls


def test_release_rollback_requires_previous_release_readiness(
    tmp_path: Path,
) -> None:
    result = _run_rollback_harness(
        tmp_path,
        service_was_active=True,
        ready=False,
    )
    calls = (tmp_path / "calls").read_text(encoding="utf-8")

    assert result.returncode == 1
    assert "systemctl restart trpg-master.service" in calls
    assert "curl" in calls
    assert "previous release did not recover readiness" in result.stderr
    assert "CRITICAL: release rollback was incomplete" in result.stderr


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


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _monitor_environment(tmp_path: Path) -> dict[str, str]:
    """Prepare stub tooling and a writable fake backup root for the monitor script."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_stub(
        bin_dir,
        "curl",
        """#!/usr/bin/env bash
prev=""
for arg in "$@"; do
    if [[ "$prev" == "--data-binary" ]]; then
        printf '%s\\n' "$arg" > "$WEBHOOK_LOG"
    fi
    prev="$arg"
done
if [[ "$*" == *"--data-binary"* ]]; then
    exit "${STUB_WEBHOOK_FAIL:-0}"
fi
exit "${STUB_READY_OK:-0}"
""",
    )
    _write_stub(
        bin_dir,
        "systemctl",
        """#!/usr/bin/env bash
if [[ "${1:-}" == "is-active" ]]; then
    exit "${STUB_SERVICE_OK:-0}"
fi
exit 0
""",
    )
    _write_stub(
        bin_dir,
        "openssl",
        """#!/usr/bin/env bash
printf 'notAfter=%s\\n' "${STUB_CERT_ENDDATE:-Jan  1 00:00:00 2038 GMT}"
""",
    )
    _write_stub(
        bin_dir,
        "df",
        """#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'
printf '/dev/stub 1000000 500000 500000 %s /\\n' "${STUB_DISK_PCT:-50}"
""",
    )

    backup_root = tmp_path / "backups" / "trpg-master"
    backup_root.mkdir(parents=True)
    (backup_root / "trpg-master-20260101T000000Z.tar.gpg").write_bytes(b"x")

    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TRPG_MONITOR_BACKUP_ROOT": str(backup_root),
        "TRPG_MONITOR_CERT_PATH": str(tmp_path / "fullchain.pem"),
        "TRPG_MONITOR_DISK_PATHS": f"{backup_root} /",
    }
    (tmp_path / "fullchain.pem").write_text("stub certificate", encoding="utf-8")
    return env


def test_monitor_script_all_checks_pass_without_webhook(tmp_path: Path) -> None:
    script = PROJECT_ROOT / "deploy" / "monitor-trpg-master.sh"
    result = subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        env=_monitor_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "[OK]   ready:" in result.stdout
    assert "[OK]   service:" in result.stdout
    assert "[OK]   backup:" in result.stdout
    assert "[OK]   certificate:" in result.stdout
    assert "[OK]   disk:/" in result.stdout
    assert "all checks passed" in result.stdout


def test_monitor_script_failures_exit_nonzero_and_send_webhook(
    tmp_path: Path,
) -> None:
    script = PROJECT_ROOT / "deploy" / "monitor-trpg-master.sh"
    env = _monitor_environment(tmp_path)
    webhook_log = tmp_path / "webhook.log"
    env.update(
        {
            "TRPG_MONITOR_WEBHOOK_URL": "https://webhook.invalid/trpg",
            "STUB_READY_OK": "1",
            "STUB_SERVICE_OK": "1",
            "STUB_CERT_ENDDATE": "Jan  1 00:00:00 2020 GMT",
            "STUB_DISK_PCT": "97",
            "WEBHOOK_LOG": str(webhook_log),
        }
    )
    backup_file = (
        tmp_path / "backups" / "trpg-master" / "trpg-master-20260101T000000Z.tar.gpg"
    )
    subprocess.run(["touch", "-d", "30 hours ago", str(backup_file)], check=True)

    result = subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "[FAIL] ready:" in result.stderr
    assert "[FAIL] service:" in result.stderr
    assert "[FAIL] backup:" in result.stderr
    assert "[FAIL] certificate:" in result.stderr
    assert "[FAIL] disk:/" in result.stderr
    assert "6 check(s) failed" in result.stderr
    assert webhook_log.exists()
    payload = webhook_log.read_text(encoding="utf-8")
    assert '"ok":false' in payload
    assert '"failed":6' in payload
    assert '"name":"ready"' in payload
    assert '"name":"certificate"' in payload


def test_monitor_script_rejects_unsafe_configuration(tmp_path: Path) -> None:
    script = PROJECT_ROOT / "deploy" / "monitor-trpg-master.sh"
    base_env = _monitor_environment(tmp_path)
    cases = (
        ({"TRPG_MONITOR_BACKUP_ROOT": "relative/backups"}, "backup root must be an absolute path"),
        (
            {"TRPG_MONITOR_DISK_MAX_PERCENT": "abc"},
            "invalid TRPG_MONITOR_DISK_MAX_PERCENT",
        ),
        (
            {"TRPG_MONITOR_BACKUP_MAX_AGE_HOURS": "0"},
            "invalid TRPG_MONITOR_BACKUP_MAX_AGE_HOURS",
        ),
        ({"TRPG_MONITOR_CERT_PATH": "relative.pem"}, "certificate path must be absolute"),
    )
    for overrides, expected_error in cases:
        result = subprocess.run(
            ["bash", str(script)],
            cwd=PROJECT_ROOT,
            env={**base_env, **overrides},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert expected_error in result.stderr


def test_monitor_script_splits_space_separated_disk_paths(tmp_path: Path) -> None:
    """Regression: TRPG_MONITOR_DISK_PATHS 是空格分隔列表，两条路径必须分别检查。"""
    script = PROJECT_ROOT / "deploy" / "monitor-trpg-master.sh"
    env = _monitor_environment(tmp_path)
    first = tmp_path / "first-disk-path"
    first.mkdir()
    backup_root = tmp_path / "backups" / "trpg-master"
    env["TRPG_MONITOR_DISK_PATHS"] = f"{first} {backup_root}"

    result = subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"[OK]   disk:{first}:" in result.stdout
    assert f"[OK]   disk:{backup_root}:" in result.stdout
    # 两条路径被分别检查，而不是被当成一个整体路径。
    assert result.stdout.count("[OK]   disk:") == 2
    # 若被错误合并为 "<first> <backup_root>" 单个路径，df 检查会失败（exit 1）。
    assert "disk:" not in result.stderr


def test_monitor_systemd_units_reference_release_script() -> None:
    service = (PROJECT_ROOT / "deploy" / "trpg-master-monitor.service").read_text(
        encoding="utf-8"
    )
    timer = (PROJECT_ROOT / "deploy" / "trpg-master-monitor.timer").read_text(
        encoding="utf-8"
    )

    assert "Type=oneshot" in service
    assert "User=root" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "ExecStart=/opt/trpg-master/current/deploy/monitor-trpg-master.sh" in service
    assert "TRPG_MONITOR_HEALTH_URL=http://127.0.0.1:8765/api/ready" in service
    assert "TRPG_MONITOR_SERVICE=trpg-master.service" in service
    assert "TRPG_MONITOR_BACKUP_ROOT=/var/backups/trpg-master" in service
    assert 'TRPG_MONITOR_DISK_PATHS="/ /var/backups/trpg-master"' in service
    assert "TRPG_MONITOR_CERT_PATH=/etc/letsencrypt/live/trpggame.xyz/fullchain.pem" in service
    assert "OnCalendar=*-*-* 04:20:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=trpg-master-monitor.service" in timer


def test_production_installer_installs_and_rolls_back_monitor_units(
    tmp_path: Path,
) -> None:
    installer = (PROJECT_ROOT / "deploy" / "install-release.sh").read_text(
        encoding="utf-8"
    )

    assert "monitor_timer=trpg-master-monitor.timer" in installer
    assert "monitor_timer_was_enabled=0" in installer
    assert "monitor_timer_was_active=0" in installer
    assert 'systemctl is-active --quiet "$monitor_timer"' in installer
    assert (
        'restore_managed_path "$unit_dir/trpg-master-monitor.timer" monitor-timer'
        in installer
    )
    assert (
        'restore_managed_path "$unit_dir/trpg-master-monitor.service" monitor-service'
        in installer
    )
    assert 'systemctl enable "$monitor_timer" || failed=1' in installer
    assert 'install_managed_file "$release/deploy/trpg-master-monitor.service"' in installer
    assert 'install_managed_file "$release/deploy/trpg-master-monitor.timer"' in installer
    assert 'systemctl enable --now "$monitor_timer"' in installer
    assert "deploy/trpg-master-monitor.service" in installer
    assert "deploy/trpg-master-monitor.timer" in installer
    assert "deploy/monitor-trpg-master.sh" in installer
    assert "deploy/restore-drill.sh" in installer
    assert '"$candidate/deploy/monitor-trpg-master.sh" \\' in installer
    assert '"$candidate/deploy/restore-drill.sh"' in installer

    result = _run_rollback_harness(
        tmp_path,
        service_was_active=False,
        ready=True,
    )
    calls = (tmp_path / "calls").read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "systemctl enable trpg-master-monitor.timer" in calls
    assert "systemctl stop trpg-master-monitor.timer" in calls


def test_restore_drill_requires_pg_restore() -> None:
    script = PROJECT_ROOT / "deploy" / "restore-drill.sh"
    result = subprocess.run(
        ["bash", str(script), "--dry-run"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "TRPG_PG_RESTORE": "/nonexistent/pg_restore"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "pg_restore is required" in result.stderr
    assert "TRPG_PG_RESTORE" in result.stderr


def test_restore_drill_refuses_production_database_target() -> None:
    script = PROJECT_ROOT / "deploy" / "restore-drill.sh"
    env = {
        **os.environ,
        "TRPG_PG_RESTORE": "/bin/true",
        "TRPG_BACKUP_PASSPHRASE_FILE": "/dev/null",
    }

    without_prefix = subprocess.run(
        ["bash", str(script), "--restore", "postgresql+psycopg://u:p@h/trpg_master"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert without_prefix.returncode == 2
    assert "does not start with the drill prefix" in without_prefix.stderr
    assert "trpg_drill_" in without_prefix.stderr

    same_as_production = subprocess.run(
        ["bash", str(script), "--restore", "postgresql+psycopg://u:p@h/trpg_drill_x"],
        cwd=PROJECT_ROOT,
        env={
            **env,
            "TRPG_PRODUCTION_DATABASE_URL": "postgresql+psycopg://u:p@h/trpg_drill_x",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert same_as_production.returncode == 2
    assert "is the configured production database" in same_as_production.stderr


def test_restore_drill_has_no_destructive_statements() -> None:
    import re

    script = (PROJECT_ROOT / "deploy" / "restore-drill.sh").read_text(
        encoding="utf-8"
    )

    # 只检查可执行语句行（排除注释与 echo/printf 提示文本，提示里会教运维
    # 手动清理演练库，但脚本自身绝不执行破坏性 SQL）。
    executable = [
        line
        for line in script.splitlines()
        if line.strip()
        and not line.lstrip().startswith(("#", "echo ", "printf "))
    ]
    joined = "\n".join(executable)
    assert not re.search(r"DROP\s+DATABASE", joined, re.IGNORECASE)
    assert not re.search(r"DROP\s+TABLE", joined, re.IGNORECASE)
    assert not re.search(r"\bTRUNCATE\b", joined, re.IGNORECASE)
    assert "--clean" not in joined
    assert "--if-exists" not in joined
    assert "--drop" not in joined
    assert "unset PGPASSWORD PGPASSFILE PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER" in script
    assert 'pg_restore_bin" --list "$work/database.dump"' in script
    assert 'pg_restore_bin" --no-owner --no-acl' in script


def test_restore_drill_dry_run_is_isolated_from_databases(tmp_path: Path) -> None:
    """Build a real backup bundle and stub gpg/pg_restore."""
    script = PROJECT_ROOT / "deploy" / "restore-drill.sh"
    backup_root = tmp_path / "backups" / "trpg-master"
    backup_root.mkdir(parents=True)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "database.dump").write_bytes(b"fake custom-format dump")
    (bundle / "runtime.tar.gz").write_bytes(b"runtime")
    with (bundle / "SHA256SUMS").open("w", encoding="utf-8") as handle:
        subprocess.run(
            ["sha256sum", "database.dump", "runtime.tar.gz"],
            cwd=bundle,
            stdout=handle,
            check=True,
        )
    archive = backup_root / "trpg-master-drill-test.tar.gpg"
    subprocess.run(["tar", "-czf", str(archive), "-C", str(bundle), "."], check=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "pg_restore_calls"
    _write_stub(
        bin_dir,
        "gpg",
        """#!/usr/bin/env bash
prev=""
for arg in "$@"; do
    if [[ "$prev" == "--decrypt" ]]; then
        cat "$arg"
        exit 0
    fi
    prev="$arg"
done
exit 2
""",
    )
    _write_stub(
        bin_dir,
        "pg_restore",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {calls}
printf 'table users\\ntable worlds\\n'
""",
    )
    passphrase = tmp_path / "passphrase"
    passphrase.write_text("secret", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(script), "--dry-run"],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TRPG_PG_RESTORE": str(bin_dir / "pg_restore"),
            "TRPG_BACKUP_PASSPHRASE_FILE": str(passphrase),
            "TRPG_BACKUP_ROOT": str(backup_root),
            "TRPG_BACKUP_PREFIX": "trpg-master",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "dry-run passed" in result.stdout
    assert "table users" in result.stdout
    pg_restore_call = calls.read_text(encoding="utf-8")
    assert "--list" in pg_restore_call
    assert "--dbname" not in pg_restore_call
    assert "-d" not in pg_restore_call.split()
