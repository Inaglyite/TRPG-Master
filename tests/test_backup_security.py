from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = PROJECT_ROOT / "deploy" / "backup-trpg-master.sh"
_SCRIPT_PREPARE_LOCK = threading.Lock()


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _install_fake_backup_commands(fake_bin: Path) -> None:
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "install",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

target = Path(sys.argv[-1])
if not str(target).startswith("/var/backups/"):
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)
""",
    )
    _write_executable(
        fake_bin / "mktemp",
        """#!/usr/bin/env python3
import os
import tempfile
from pathlib import Path

template = os.sys.argv[-1]
if ".backup-" in template:
    work = Path(os.environ["FAKE_BACKUP_WORK"])
    work.mkdir(mode=0o700)
    print(work)
else:
    descriptor, name = tempfile.mkstemp(
        prefix=".backup-partial-",
        dir=os.environ["FAKE_PARTIAL_ROOT"],
    )
    os.close(descriptor)
    print(name)
""",
    )
    _write_executable(
        fake_bin / "flock",
        """#!/usr/bin/env python3
import fcntl
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
while arguments and arguments[0].startswith("-"):
    arguments.pop(0)
if len(arguments) != 1 or not arguments[0].isdecimal():
    raise SystemExit(2)
descriptor = int(arguments[0])
fcntl.flock(descriptor, fcntl.LOCK_EX)
report_path = Path(os.environ["FAKE_FLOCK_REPORT"])
with report_path.open("a", encoding="utf-8") as report:
    report.write(f"acquire {os.getpid()} fd={descriptor}\\n")
""",
    )
    _write_executable(
        fake_bin / "pg_dump",
        """#!/usr/bin/env python3
import json
import os
import stat
import sys
import time
from pathlib import Path

pgpass = Path(os.environ["PGPASSFILE"])
expected_pgpass = Path(os.environ["FAKE_EXPECTED_PGPASS"]).read_text(
    encoding="utf-8"
)
secret = Path(os.environ["FAKE_DATABASE_SECRET"]).read_text(encoding="utf-8")
report = {
    "argv": sys.argv[1:],
    "pgpass_matches": pgpass.read_text(encoding="utf-8") == expected_pgpass,
    "pgpass_mode": stat.S_IMODE(pgpass.stat().st_mode),
    "args_mode": stat.S_IMODE((pgpass.parent / "pg_dump.args").stat().st_mode),
    "database_url_present": "TRPG_DATABASE_URL" in os.environ,
    "pgpassword_present": "PGPASSWORD" in os.environ,
    "secret_in_argv": any(secret in argument for argument in sys.argv[1:]),
    "secret_in_environment": any(
        secret in value for value in os.environ.values()
    ),
}
Path(os.environ["FAKE_PG_DUMP_REPORT"]).write_text(
    json.dumps(report),
    encoding="utf-8",
)
if os.environ.get("FAKE_PG_DUMP_FAIL") == "1":
    raise SystemExit(9)
time.sleep(float(os.environ.get("FAKE_PG_DUMP_DELAY", "0")))
sys.stdout.buffer.write(b"fake-postgresql-dump")
""",
    )
    _write_executable(
        fake_bin / "tar",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
target = arguments[arguments.index("--file") + 1]
if "--list" in arguments:
    sys.stdin.buffer.read()
    if os.environ.get("FAKE_TAR_VERIFY_FAIL") == "1":
        raise SystemExit(19)
elif target == "-":
    sys.stdout.buffer.write(b"fake-backup-archive")
else:
    Path(target).write_bytes(b"fake-runtime-archive")
""",
    )
    _write_executable(
        fake_bin / "gpg",
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
report = Path(os.environ["FAKE_GPG_REPORT"])
if "--decrypt" in arguments:
    with report.open("a", encoding="utf-8") as handle:
        handle.write("decrypt\\n")
    if os.environ.get("FAKE_GPG_VERIFY_FAIL") == "1":
        raise SystemExit(17)
    sys.stdout.buffer.write(Path(arguments[-1]).read_bytes())
else:
    with report.open("a", encoding="utf-8") as handle:
        handle.write("encrypt\\n")
    archive = sys.stdin.buffer.read()
    if os.environ.get("FAKE_GPG_ENCRYPT_FAIL") == "1":
        raise SystemExit(11)
    sys.stdout.buffer.write(archive)
""",
    )
    _write_executable(
        fake_bin / "mv",
        """#!/usr/bin/env python3
import os
import shutil
import sys
from pathlib import Path

arguments = [argument for argument in sys.argv[1:] if not argument.startswith("-")]
source = Path(arguments[-2])
requested_target = Path(arguments[-1])
target = Path(os.environ["FAKE_FINAL_ROOT"]) / requested_target.name
if target.exists():
    raise SystemExit(0)
shutil.move(source, target)
with Path(os.environ["FAKE_MV_REPORT"]).open("a", encoding="utf-8") as report:
    report.write(f"{requested_target}\\n")
""",
    )
    _write_executable(
        fake_bin / "date",
        """#!/usr/bin/env python3
print("20260729T120000Z")
""",
    )
    _write_executable(
        fake_bin / "find",
        """#!/usr/bin/env python3
raise SystemExit(0)
""",
    )


def _pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _sandboxed_backup_script(tmp_path: Path) -> tuple[Path, Path, Path]:
    managed_root = tmp_path / "managed"
    backup_base = managed_root / "backups"
    runtime_base = managed_root / "lib"
    backup_root = backup_base / "trpg-master-security-test"
    runtime_root = runtime_base / "trpg-master-security-test"
    script = tmp_path / "backup-trpg-master-under-test.sh"
    with _SCRIPT_PREPARE_LOCK:
        backup_base.mkdir(parents=True, exist_ok=True)
        runtime_root.mkdir(parents=True, exist_ok=True)
        runtime_root.chmod(0o700)
        if not script.exists():
            source = BACKUP_SCRIPT.read_text(encoding="utf-8")
            source = source.replace("/var/backups", str(backup_base))
            source = source.replace("/var/lib", str(runtime_base))
            _write_executable(script, source)
    return script, backup_root, runtime_root


def _run_backup(
    tmp_path: Path,
    *,
    database_url: str,
    expected_pgpass_fields: tuple[str, str, str, str, str],
    database_secret: str,
    pg_dump_fails: bool = False,
    gpg_encrypt_fails: bool = False,
    gpg_verify_fails: bool = False,
    tar_verify_fails: bool = False,
    pg_dump_delay: float = 0,
    prepare: bool = True,
) -> tuple[
    subprocess.CompletedProcess[str],
    dict[str, object],
    Path,
    Path,
]:
    backup_script, backup_root, runtime_root = _sandboxed_backup_script(tmp_path)
    fake_bin = tmp_path / "bin"
    if prepare:
        _install_fake_backup_commands(fake_bin)

    work = tmp_path / "work"
    partial_root = tmp_path / "partial"
    partial_root.mkdir(exist_ok=True)
    final_root = tmp_path / "final"
    final_root.mkdir(exist_ok=True)
    output = final_root / "trpg-master-security-test-20260729T120000Z.tar.gpg"
    pg_dump_report = tmp_path / "pg-dump-report.json"
    gpg_report = tmp_path / "gpg-report.txt"
    mv_report = tmp_path / "mv-report.txt"
    flock_report = tmp_path / "flock-report.txt"
    expected_pgpass = tmp_path / "expected.pgpass"
    expected_pgpass.write_text(
        ":".join(_pgpass_escape(field) for field in expected_pgpass_fields) + "\n",
        encoding="utf-8",
    )
    secret_file = tmp_path / "database-secret"
    secret_file.write_text(database_secret, encoding="utf-8")
    passphrase_file = tmp_path / "backup-passphrase"
    passphrase_file.write_text("unrelated-test-passphrase", encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TRPG_BACKUP_ROOT": str(backup_root),
        "TRPG_BACKUP_RUNTIME_ROOT": str(runtime_root),
        "TRPG_BACKUP_PREFIX": "trpg-master-security-test",
        "TRPG_DATABASE_URL": database_url,
        "TRPG_BACKUP_PASSPHRASE_FILE": str(passphrase_file),
        "PGPASSWORD": "ambient-password-must-not-reach-pg-dump",
        "FAKE_BACKUP_WORK": str(work),
        "FAKE_PARTIAL_ROOT": str(partial_root),
        "FAKE_FINAL_ROOT": str(final_root),
        "FAKE_EXPECTED_PGPASS": str(expected_pgpass),
        "FAKE_DATABASE_SECRET": str(secret_file),
        "FAKE_PG_DUMP_REPORT": str(pg_dump_report),
        "FAKE_GPG_REPORT": str(gpg_report),
        "FAKE_MV_REPORT": str(mv_report),
        "FAKE_FLOCK_PATH": str(tmp_path / "backup.lock"),
        "FAKE_FLOCK_REPORT": str(flock_report),
        "FAKE_PG_DUMP_DELAY": str(pg_dump_delay),
    }
    if pg_dump_fails:
        env["FAKE_PG_DUMP_FAIL"] = "1"
    if gpg_encrypt_fails:
        env["FAKE_GPG_ENCRYPT_FAIL"] = "1"
    if gpg_verify_fails:
        env["FAKE_GPG_VERIFY_FAIL"] = "1"
    if tar_verify_fails:
        env["FAKE_TAR_VERIFY_FAIL"] = "1"

    result = subprocess.run(
        ["bash", str(backup_script)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    report = (
        json.loads(pg_dump_report.read_text(encoding="utf-8"))
        if pg_dump_report.exists()
        else {}
    )
    return result, report, work, output


def _run_backup_path_preflight(
    script: Path,
    backup_root: Path,
    runtime_root: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "TRPG_BACKUP_ROOT": str(backup_root),
            "TRPG_BACKUP_RUNTIME_ROOT": str(runtime_root),
            "TRPG_BACKUP_PREFIX": "trpg-master-security-test",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_backup_rejects_symlinked_managed_root_before_following_it(
    tmp_path: Path,
) -> None:
    script, backup_root, runtime_root = _sandboxed_backup_script(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_mode = outside.stat().st_mode
    backup_root.symlink_to(outside, target_is_directory=True)

    result = _run_backup_path_preflight(script, backup_root, runtime_root)

    assert result.returncode == 2
    assert "symbolic links are forbidden" in result.stderr
    assert outside.stat().st_mode == outside_mode
    assert list(outside.iterdir()) == []


def test_backup_rejects_runtime_directory_visible_to_other_users(
    tmp_path: Path,
) -> None:
    script, backup_root, runtime_root = _sandboxed_backup_script(tmp_path)
    runtime_root.chmod(0o755)

    result = _run_backup_path_preflight(script, backup_root, runtime_root)

    assert result.returncode == 2
    assert "unsafe runtime root ownership or permissions" in result.stderr


@pytest.mark.parametrize(
    "driver",
    ("postgresql", "postgresql+psycopg", "postgresql+psycopg2"),
)
def test_backup_keeps_percent_encoded_password_out_of_pg_dump_command_line(
    tmp_path: Path,
    driver: str,
) -> None:
    password = "s3cr:et\\value@/word"
    result, report, work, output = _run_backup(
        tmp_path,
        database_url=(
            f"{driver}://backup%3Auser:"
            "s3cr%3Aet%5Cvalue%40%2Fword@[2001:db8::7]:5544/trpg_prod"
        ),
        expected_pgpass_fields=(
            "2001:db8::7",
            "5544",
            "trpg_prod",
            "backup:user",
            password,
        ),
        database_secret=password,
    )

    assert result.returncode == 0, result.stderr
    assert report == {
        "argv": [
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--no-password",
            "--host",
            "2001:db8::7",
            "--port",
            "5544",
            "--dbname",
            "trpg_prod",
            "--username",
            "backup:user",
        ],
        "pgpass_matches": True,
        "pgpass_mode": 0o600,
        "args_mode": 0o600,
        "database_url_present": False,
        "pgpassword_present": False,
        "secret_in_argv": False,
        "secret_in_environment": False,
    }
    assert password not in result.stdout
    assert password not in result.stderr
    assert output.read_bytes() == b"fake-backup-archive"
    assert not work.exists()


def test_backup_supports_unix_socket_url_and_cleans_credentials(
    tmp_path: Path,
) -> None:
    password = "socket:password\\value"
    result, report, work, output = _run_backup(
        tmp_path,
        database_url=(
            "postgresql+psycopg2://backup:"
            "socket%3Apassword%5Cvalue@/game"
            "?host=%2Fvar%2Frun%2Fpostgresql&port=5433"
        ),
        expected_pgpass_fields=(
            "/var/run/postgresql",
            "5433",
            "game",
            "backup",
            password,
        ),
        database_secret=password,
    )

    assert result.returncode == 0, result.stderr
    assert report["argv"] == [
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--no-password",
        "--host",
        "/var/run/postgresql",
        "--port",
        "5433",
        "--dbname",
        "game",
        "--username",
        "backup",
    ]
    assert report["pgpass_matches"] is True
    assert report["pgpass_mode"] == 0o600
    assert report["database_url_present"] is False
    assert report["pgpassword_present"] is False
    assert report["secret_in_argv"] is False
    assert report["secret_in_environment"] is False
    assert output.exists()
    assert not work.exists()


@pytest.mark.parametrize(
    "database_url",
    (
        "sqlite:////tmp/trpg.db",
        "mysql://backup:should-not-leak@localhost/game",
        "postgresql+asyncpg://backup:should-not-leak@localhost/game",
        (
            "postgresql://backup:should-not-leak@localhost/game"
            "?sslmode=require"
        ),
        "not-a-database-url",
    ),
)
def test_backup_rejects_non_postgresql_or_ambiguous_urls_without_leaking(
    tmp_path: Path,
    database_url: str,
) -> None:
    result, report, work, output = _run_backup(
        tmp_path,
        database_url=database_url,
        expected_pgpass_fields=("", "", "", "", ""),
        database_secret="should-not-leak",
    )

    assert result.returncode == 2
    assert report == {}
    assert "invalid PostgreSQL database URL" in result.stderr
    assert "should-not-leak" not in result.stdout
    assert "should-not-leak" not in result.stderr
    assert not output.exists()
    assert not work.exists()


def test_backup_removes_pgpass_after_pg_dump_failure(tmp_path: Path) -> None:
    password = "failure-only-secret"
    result, report, work, output = _run_backup(
        tmp_path,
        database_url=(
            "postgresql://backup:failure-only-secret@127.0.0.1/game"
        ),
        expected_pgpass_fields=(
            "127.0.0.1",
            "5432",
            "game",
            "backup",
            password,
        ),
        database_secret=password,
        pg_dump_fails=True,
    )

    assert result.returncode == 9
    assert report["pgpass_matches"] is True
    assert report["pgpass_mode"] == 0o600
    assert report["secret_in_argv"] is False
    assert report["secret_in_environment"] is False
    assert password not in result.stdout
    assert password not in result.stderr
    assert not output.exists()
    assert not work.exists()


@pytest.mark.parametrize(
    ("failure_option", "expected_status"),
    (
        ({"gpg_encrypt_fails": True}, 11),
        ({"gpg_verify_fails": True}, 17),
        ({"tar_verify_fails": True}, 19),
    ),
)
def test_backup_never_publishes_or_leaves_partial_after_validation_failure(
    tmp_path: Path,
    failure_option: dict[str, bool],
    expected_status: int,
) -> None:
    result, _, work, output = _run_backup(
        tmp_path,
        database_url="postgresql://backup:secret@127.0.0.1/game",
        expected_pgpass_fields=(
            "127.0.0.1",
            "5432",
            "game",
            "backup",
            "secret",
        ),
        database_secret="secret",
        **failure_option,
    )

    assert result.returncode == expected_status
    assert not output.exists()
    assert not work.exists()
    assert list((tmp_path / "partial").iterdir()) == []
    assert list((tmp_path / "final").iterdir()) == []


def test_backup_keeps_existing_same_second_archive_and_uses_suffix(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    _install_fake_backup_commands(fake_bin)
    final_root = tmp_path / "final"
    final_root.mkdir()
    existing = (
        final_root
        / "trpg-master-security-test-20260729T120000Z.tar.gpg"
    )
    existing.write_bytes(b"existing-backup-must-survive")

    result, _, work, output = _run_backup(
        tmp_path,
        database_url="postgresql://backup:secret@127.0.0.1/game",
        expected_pgpass_fields=(
            "127.0.0.1",
            "5432",
            "game",
            "backup",
            "secret",
        ),
        database_secret="secret",
        prepare=False,
    )
    suffixed = (
        final_root
        / "trpg-master-security-test-20260729T120000Z-01.tar.gpg"
    )

    assert result.returncode == 0, result.stderr
    assert output == existing
    assert existing.read_bytes() == b"existing-backup-must-survive"
    assert suffixed.read_bytes() == b"fake-backup-archive"
    assert not work.exists()
    assert list((tmp_path / "partial").iterdir()) == []


def test_backup_serializes_concurrent_runs_for_the_same_root(
    tmp_path: Path,
) -> None:
    _install_fake_backup_commands(tmp_path / "bin")

    def run_one() -> subprocess.CompletedProcess[str]:
        result, _, _, _ = _run_backup(
            tmp_path,
            database_url="postgresql://backup:secret@127.0.0.1/game",
            expected_pgpass_fields=(
                "127.0.0.1",
                "5432",
                "game",
                "backup",
                "secret",
            ),
            database_secret="secret",
            pg_dump_delay=0.15,
            prepare=False,
        )
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_one(), range(2)))

    assert [result.returncode for result in results] == [0, 0]
    lock_events = (tmp_path / "flock-report.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    assert [event.split()[0] for event in lock_events] == [
        "acquire",
        "acquire",
    ]
    outputs = sorted((tmp_path / "final").glob("*.tar.gpg"))
    assert len(outputs) == 2
    assert outputs[0].read_bytes() == b"fake-backup-archive"
    assert outputs[1].read_bytes() == b"fake-backup-archive"
    assert list((tmp_path / "partial").iterdir()) == []


def test_backup_cleans_work_directory_when_signalled(tmp_path: Path) -> None:
    _install_fake_backup_commands(tmp_path / "bin")
    backup_script, backup_root_path, runtime_root = _sandboxed_backup_script(tmp_path)
    work = tmp_path / "work"
    passphrase_file = tmp_path / "backup-passphrase"
    passphrase_file.write_text("test-passphrase", encoding="utf-8")
    secret_file = tmp_path / "database-secret"
    secret_file.write_text("secret", encoding="utf-8")
    expected_pgpass = tmp_path / "expected.pgpass"
    expected_pgpass.write_text(
        "127.0.0.1:5432:game:backup:secret\n",
        encoding="utf-8",
    )
    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    final_root = tmp_path / "final"
    final_root.mkdir()
    backup_root = str(backup_root_path)
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "TRPG_BACKUP_ROOT": backup_root,
        "TRPG_BACKUP_RUNTIME_ROOT": str(runtime_root),
        "TRPG_BACKUP_PREFIX": "trpg-master-security-test",
        "TRPG_DATABASE_URL": (
            "postgresql://backup:secret@127.0.0.1/game"
        ),
        "TRPG_BACKUP_PASSPHRASE_FILE": str(passphrase_file),
        "FAKE_BACKUP_WORK": str(work),
        "FAKE_PARTIAL_ROOT": str(partial_root),
        "FAKE_FINAL_ROOT": str(final_root),
        "FAKE_EXPECTED_PGPASS": str(expected_pgpass),
        "FAKE_DATABASE_SECRET": str(secret_file),
        "FAKE_PG_DUMP_REPORT": str(tmp_path / "pg-dump-report.json"),
        "FAKE_GPG_REPORT": str(tmp_path / "gpg-report.txt"),
        "FAKE_MV_REPORT": str(tmp_path / "mv-report.txt"),
        "FAKE_FLOCK_PATH": str(tmp_path / "backup.lock"),
        "FAKE_FLOCK_REPORT": str(tmp_path / "flock-report.txt"),
        "FAKE_PG_DUMP_DELAY": "5",
    }
    process = subprocess.Popen(
        ["bash", str(backup_script)],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pg_dump_report = tmp_path / "pg-dump-report.json"
    deadline = time.monotonic() + 3
    while not pg_dump_report.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pg_dump_report.exists()
    assert work.exists()

    process.terminate()
    _, stderr = process.communicate(timeout=10)

    assert process.returncode == 143, stderr
    assert not work.exists()
    assert list(partial_root.iterdir()) == []
    assert list(final_root.iterdir()) == []
