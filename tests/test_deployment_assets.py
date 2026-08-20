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


def test_context_gc_units_are_scheduled_and_release_managed() -> None:
    """H2 GC cannot be a forgotten local command after a release rollback."""
    cases = (
        (
            "trpg-master",
            "install-release.sh",
            "/opt/trpg-master/current",
            "/etc/trpg-master/trpg-master.env",
            "/var/lib/trpg-master",
        ),
        (
            "trpg-master-staging",
            "install-staging-release.sh",
            "/opt/trpg-master-staging/current",
            "/etc/trpg-master/staging.env",
            "/var/lib/trpg-master-staging",
        ),
    )
    for name, installer_name, release_root, environment_file, runtime_root in cases:
        service = (PROJECT_ROOT / "deploy" / f"{name}-context-gc.service").read_text(
            encoding="utf-8"
        )
        timer = (PROJECT_ROOT / "deploy" / f"{name}-context-gc.timer").read_text(
            encoding="utf-8"
        )
        installer = (PROJECT_ROOT / "deploy" / installer_name).read_text(encoding="utf-8")
        timer_name = f"{name}-context-gc.timer"

        assert "Type=oneshot" in service
        assert "User=trpgdeploy" in service
        assert f"EnvironmentFile={environment_file}" in service
        assert f"TRPG_RUNTIME_ROOT={runtime_root}" in service
        assert (
            f"ExecStart={release_root}/.venv/bin/python "
            f"{release_root}/tools/maintain_context_events.py" in service
        )
        assert "NoNewPrivileges=true" in service
        assert "ProtectSystem=strict" in service
        assert "Persistent=true" in timer
        assert f"Unit={name}-context-gc.service" in timer
        assert f"context_gc_timer={timer_name}" in installer
        assert f'deploy/{name}-context-gc.service"' in installer
        assert f'deploy/{name}-context-gc.timer"' in installer
        assert 'systemctl enable --now "$context_gc_timer"' in installer

    launcher = (PROJECT_ROOT / "deploy" / "trpg-start-staging").read_text(encoding="utf-8")
    assert "trpg-master-staging-context-gc.timer" in launcher


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
    assert "archive_path=\"/var/lib/trpg-master-release/incoming/" in workflow
    assert "sudo -n /usr/local/sbin/trpg-activate-release '$RELEASE_SHA'" in workflow
    assert "scp deploy/install-release.sh" not in workflow
    assert "sudo bash '/tmp/trpg-install-release-$RELEASE_SHA.sh'" not in workflow
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


def test_production_activation_uses_fixed_root_owned_entrypoint() -> None:
    entrypoint = (
        PROJECT_ROOT / "deploy" / "trpg-activate-release"
    ).read_text(encoding="utf-8")
    installer = (
        PROJECT_ROOT / "deploy" / "install-release-activation-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert entrypoint.startswith("#!/usr/bin/env python3")
    assert "RELEASE_PATTERN = re.compile(r\"^[0-9a-f]{40}$\")" in entrypoint
    assert 'os.O_NOFOLLOW' in entrypoint
    assert 'stat.S_IMODE(before.st_mode) & 0o077' in entrypoint
    assert 'before.st_nlink != 1' in entrypoint
    assert 'SPOOL_ROOT = Path("/var/lib/trpg-master-release")' in entrypoint
    assert 'INCOMING_DIR = SPOOL_ROOT / "incoming"' in entrypoint
    assert 'Path("/usr/local/sbin/trpg-install-release")' in entrypoint
    assert 'os.geteuid() != 0' in entrypoint
    assert "NOPASSWD: /usr/local/sbin/trpg-activate-release" in installer
    assert "visudo -cf" in installer
    assert "/tmp" not in installer


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

    # Manual dispatch is the only trigger; workflow_run must not be used.
    assert "workflow_dispatch:" in workflow
    assert "workflow_run" not in workflow
    assert "github.event.workflow_run" not in workflow

    # Job is still strictly gated on master.
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
        # Full release ids can make pip emit a two-line /bin/sh entry-point
        # wrapper instead of a direct shebang. Both forms must be relocated.
        assert (
            '-e "2s|$candidate/.venv/bin/python3|$release/.venv/bin/python3|"'
            in installer
        )
        # A first-install rollback may restore a unit to the missing state.
        assert (
            'elif systemctl cat "$service_name" >/dev/null 2>&1; then'
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
context_gc_timer=trpg-master-context-gc.timer
monitor_timer=trpg-master-monitor.timer
health_url=http://127.0.0.1:8765/api/ready
service_was_enabled=1
service_was_active={int(service_was_active)}
timer_was_enabled=1
timer_was_active=0
context_gc_timer_was_enabled=1
context_gc_timer_was_active=0
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


def test_monitor_records_backup_failure_when_all_backups_stale(
    tmp_path: Path,
) -> None:
    """Regression: 多份备份全部过期时 backup FAIL 必须被记录,不能被 SIGPIPE 中止。

    与 restore-drill 同根:`find … | sort -nr | head -1` 中 head 提前关闭管道
    使 sort 收到 SIGPIPE,`set -Eeuo pipefail` 下命令替换非零让整个监控脚本退出
    —— 备份不新鲜时监控崩溃而非发告警(告警静默丢失)。本测试构造 find 输出
    超过 64KiB 管道缓冲的过期归档集(确定性复现 SIGPIPE,而非竞态),断言
    backup FAIL 仍被记录且既有 webhook 告警路径照常发送。
    """
    script = PROJECT_ROOT / "deploy" / "monitor-trpg-master.sh"
    env = _monitor_environment(tmp_path)
    webhook_log = tmp_path / "webhook.log"
    env.update(
        {
            "TRPG_MONITOR_WEBHOOK_URL": "https://webhook.invalid/trpg",
            "WEBHOOK_LOG": str(webhook_log),
        }
    )
    backup_root = tmp_path / "backups" / "trpg-master"
    old_epoch = 1_700_000_000
    for path in backup_root.iterdir():
        os.utime(path, (old_epoch, old_epoch))
    for index in range(500):
        path = backup_root / f"trpg-master-{index:0150d}.tar.gpg"
        path.touch()
        os.utime(path, (old_epoch, old_epoch))

    result = subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # 未被 SIGPIPE 提前中止:backup 失败被记录,其余检查仍执行完毕。
    assert result.returncode == 1
    assert "[FAIL] backup:" in result.stderr
    assert "no backup newer than" in result.stderr
    assert "[OK]   ready:" in result.stdout
    assert "[OK]   certificate:" in result.stdout
    # 既有告警路径照常发送。
    assert webhook_log.exists()
    payload = webhook_log.read_text(encoding="utf-8")
    assert '"ok":false' in payload
    assert '"name":"backup"' in payload


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


def test_staging_installer_validates_host_nginx_override(tmp_path: Path):
    """主机级 override 契约：regular file + root + 不可 group/world writable。

    通过 PATH 注入 stub stat 模拟 owner/mode，使 root 归属与写位检查可在
    非 root CI 用户下精确验证；symlink 用真实文件系统构造。
    """
    installer = (PROJECT_ROOT / "deploy" / "install-staging-release.sh").read_text(
        encoding="utf-8"
    )
    function = _installer_function(
        installer,
        "validate_staging_nginx_override",
        "backup_managed_path",
    )
    override = tmp_path / "staging-nginx.conf"
    override.write_text("server {}\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(
        bin_dir,
        "stat",
        """#!/usr/bin/env bash
if [[ "$*" == *"-c %u"* ]]; then
    printf '%s\\n' "${STUB_UID:?}"
else
    printf '%s\\n' "${STUB_MODE:?}"
fi
""",
    )
    harness = f"""
set -Eeuo pipefail
staging_nginx_override="$1"
{function}
validate_staging_nginx_override
"""

    def run(path: Path, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-s", "--", str(path)],
            input=harness,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    # 不存在 -> 不生效，直接通过（Azure staging 行为不变）
    missing = run(tmp_path / "absent.conf")
    assert missing.returncode == 0, missing.stderr

    # root 且 0644 -> 通过
    good = run(override, STUB_UID="0", STUB_MODE="644")
    assert good.returncode == 0, good.stderr

    # 非 root -> 拒绝
    non_root = run(override, STUB_UID="1000", STUB_MODE="644")
    assert non_root.returncode == 1
    assert "must be owned by root" in non_root.stderr

    # root 但 group/world writable -> 拒绝
    writable = run(override, STUB_UID="0", STUB_MODE="666")
    assert writable.returncode == 1
    assert "must not be group or world writable" in writable.stderr

    # symlink -> 拒绝（真实文件系统，stat 之前就被拦截）
    link = tmp_path / "link.conf"
    link.symlink_to(override)
    symlink = run(link, STUB_UID="0", STUB_MODE="644")
    assert symlink.returncode == 1
    assert "must be a regular file, not a symlink" in symlink.stderr


def test_staging_installer_prefers_host_nginx_override_before_managed_writes():
    """override 存在时作为 nginx_available 来源；验证发生在任何管理文件写入前。"""
    installer = (PROJECT_ROOT / "deploy" / "install-staging-release.sh").read_text(
        encoding="utf-8"
    )
    production = (PROJECT_ROOT / "deploy" / "install-release.sh").read_text(
        encoding="utf-8"
    )

    assert "staging_nginx_override=/etc/trpg-master/staging-nginx.conf" in installer
    # 调用（非函数定义）位于 config_backup（管理文件写入起点）之前
    call_position = installer.index("\nvalidate_staging_nginx_override\n")
    backup_position = installer.index('config_backup="$(mktemp')
    assert call_position < backup_position
    # 默认沿用 release 模板；override 存在时才切换来源
    assert 'nginx_source="$release/deploy/nginx-trpg-master-staging.conf"' in installer
    assert (
        'if [[ -f "$staging_nginx_override" && ! -L "$staging_nginx_override" ]]; then'
        in installer
    )
    assert 'install_managed_file "$nginx_source" "$nginx_available" 0644' in installer
    # 不硬编码主机 IP；生产安装器完全不受该机制影响
    assert "staging_nginx_override" not in production
    assert "192.168" not in installer
