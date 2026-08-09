from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


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
    # PGDATABASE 只提供连接默认值,不会替代 -d/--dbname 来选择直连恢复模式
    # （本次 PostgreSQL 17 现场暴露了旧写法:must specify -d/--dbname or -f/--file）,
    # --restore 必须把目标库作为显式 --dbname 传入,不允许回归到环境变量方式。
    assert '--dbname="$target_dbname"' in script


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
    # 生产 outer bundle 是未压缩 tar(backup 端 tar --create --file - | gpg),
    # 夹具必须复刻该格式而非 gzip,否则无法覆盖真实契约。
    subprocess.run(
        ["tar", "--create", "--file", str(archive), "-C", str(bundle), "."],
        check=True,
    )

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


def test_restore_drill_restore_passes_explicit_dbname_to_pg_restore(
    tmp_path: Path,
) -> None:
    """Regression: --restore 必须把目标库作为显式 --dbname 传给 pg_restore。

    PGDATABASE 只提供连接默认值,不会替代 -d/--dbname 来选择直连恢复模式
    （真实树莓派 staging 现场,PostgreSQL 17 暴露了旧写法:
    "must specify -d/--dbname or -f/--file"）。只设 PGDATABASE 的旧实现
    会失败;修复后 pg_restore 的 argv 必须包含 --dbname=<target_dbname>。
    """
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
    archive = backup_root / "trpg-master-restore-test.tar.gpg"
    # 与生产端一致:未压缩 outer tar 直接进 gpg。
    subprocess.run(
        ["tar", "--create", "--file", str(archive), "-C", str(bundle), "."],
        check=True,
    )

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
""",
    )
    _write_stub(bin_dir, "createdb", "#!/usr/bin/env bash\nexit 0\n")
    # psql stub:库存在探测返回空(演练库不存在);pg_tables 列表返回 15 张
    # 关键表;其余(计数查询)返回三行计数。stub 不真正连接数据库。
    _write_stub(
        bin_dir,
        "psql",
        """#!/usr/bin/env bash
if [[ "$*" == *"SELECT 1 FROM pg_database"* ]]; then
    exit 0
fi
if [[ "$*" == *"pg_tables"* ]]; then
    printf 'users\\nworlds\\nworld_members\\nworld_states\\nworld_invites\\nworld_investigators\\nsessions\\nturns\\nturn_events\\nsnapshots\\nsave_slots\\nplayer_notes\\nmodel_calls\\nroom_actions\\naudit_events\\n'
    exit 0
fi
printf 'users=1\\nworlds=1\\nturns=1\\n'
""",
    )
    passphrase = tmp_path / "passphrase"
    passphrase.write_text("secret", encoding="utf-8")
    target_dbname = "trpg_drill_20260801"

    result = subprocess.run(
        [
            "bash",
            str(script),
            "--restore",
            f"postgresql+psycopg://u:p@h:5432/{target_dbname}",
        ],
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
    assert "restore drill passed" in result.stdout
    # 修复点:目标库必须是 pg_restore argv 里的显式 --dbname 参数,
    # 而不是依赖 PGDATABASE 环境变量(它只提供连接默认值,不选择恢复模式)。
    pg_restore_call = calls.read_text(encoding="utf-8")
    assert f"--dbname={target_dbname}" in pg_restore_call
    assert "database.dump" in pg_restore_call


def test_restore_drill_latest_stable_with_many_archives_under_pipefail(
    tmp_path: Path,
) -> None:
    """Regression: `--latest` 在 `set -Eeuo pipefail` 下有多份归档必须稳定成功。

    Pi staging 现场证据:备份目录已有 4 个归档时 `--latest` 无输出退出 1,
    根因是 `find … | sort -nr | head -1` 中 `head -1` 提前关闭管道使 sort
    收到 SIGPIPE,命令替换在 pipefail 下非零并触发脚本退出。

    本测试构造 find 输出超过管道缓冲(64KiB)的归档集,让旧实现的 SIGPIPE
    确定复现(而非依赖竞态),并断言仍按最新 mtime 选中归档且 dry-run 成功。
    """
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

    # 长文件名 + 足够数量,使 find 输出总量超过 64KiB 管道缓冲:
    # sort 在 head 关闭后写满缓冲,剩余输出必然 SIGPIPE(确定性复现,非竞态)。
    old_epoch = 1_700_000_000
    for index in range(500):
        path = backup_root / f"trpg-master-{index:0150d}.tar.gpg"
        path.touch()
        os.utime(path, (old_epoch, old_epoch))
    newest_name = "trpg-master-newest.tar.gpg"
    newest_archive = backup_root / newest_name
    subprocess.run(
        ["tar", "--create", "--file", str(newest_archive), "-C", str(bundle), "."],
        check=True,
    )
    os.utime(newest_archive, (old_epoch + 10_000, old_epoch + 10_000))

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
    # 选中最新 mtime 的归档,而不是被 SIGPIPE 中断。
    assert newest_name in result.stdout
    assert "no backup found" not in result.stdout


def test_restore_drill_uses_controlled_gnupghome_and_plain_outer_tar(
    tmp_path: Path,
) -> None:
    """Regression: restore-drill 必须自带受控 GNUPGHOME 并接受生产端 outer tar。

    现场第二/三处失败:
    - trpgdeploy 系统账号 HOME=/nonexistent,restore-drill.sh 未设 GNUPGHOME,
      真实 gpg 报"无法创建目录 '/nonexistent/.gnupg'"而不可用;
    - backup-trpg-master.sh 发布的是未 gzip 的 outer tar(tar --create --file - | gpg),
      而 restore-drill.sh 用 tar --extract --gzip,真实备份报"not in gzip format"。

    本测试用真实 gpg 以与生产端逐字节相同的命令构造 fixture(未压缩 outer tar),
    在 HOME=/nonexistent 下由真实 restore-drill.sh --dry-run 解密/校验:
    旧代码两处缺陷都会使这里失败,修复后必须成功且不连接数据库。
    """
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
    passphrase = tmp_path / "passphrase"
    passphrase.write_text("secret-passphrase", encoding="utf-8")
    gnupg_home = tmp_path / "gnupg"
    gnupg_home.mkdir(mode=0o700)
    archive = backup_root / "trpg-master-gnupgtest.tar.gpg"
    # 与 backup-trpg-master.sh 发布命令逐字节一致:未压缩 outer tar 直接进 gpg。
    subprocess.run(
        [
            "bash",
            "-c",
            'tar --create --file - --directory "$1" .'
            ' | gpg --batch --pinentry-mode loopback --symmetric --cipher-algo AES256'
            ' --passphrase-file "$2" > "$3"',
            "sh",
            str(bundle),
            str(passphrase),
            str(archive),
        ],
        env={**os.environ, "GNUPGHOME": str(gnupg_home)},
        check=True,
    )

    # fixture 契约断言:outer tar 未 gzip(生产端格式),否则本测试失去意义。
    plaintext = subprocess.run(
        [
            "gpg",
            "--batch",
            "--quiet",
            "--pinentry-mode",
            "loopback",
            "--passphrase-file",
            str(passphrase),
            "--decrypt",
            str(archive),
        ],
        env={**os.environ, "GNUPGHOME": str(gnupg_home)},
        capture_output=True,
        check=True,
    ).stdout
    assert not plaintext.startswith(b"\x1f\x8b"), (
        "fixture 必须复刻生产 outer tar(未 gzip):backup-trpg-master.sh 用 "
        "tar --create --file - | gpg 发布"
    )
    # 恢复端静态契约:单次解密落盘,按 gzip magic 显式选择参数;
    # 不得再无条件要求 gzip outer bundle(旧实现的现场失败点)。
    script_text = script.read_text(encoding="utf-8")
    assert '> "$work/outer.tar"' in script_text
    assert '"1f8b"' in script_text
    assert "tar_flags=(--extract --gzip)" in script_text
    assert "tar_flags=(--extract)" in script_text
    assert "tar --extract --gzip --file -" not in script_text

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "pg_restore_calls"
    _write_stub(
        bin_dir,
        "pg_restore",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {calls}
printf 'table users\\ntable worlds\\n'
""",
    )

    result = subprocess.run(
        ["bash", str(script), "--dry-run"],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "HOME": "/nonexistent",
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
    # dry-run 不连接数据库:pg_restore 只 --list,无连接参数。
    pg_restore_call = calls.read_text(encoding="utf-8")
    assert "--list" in pg_restore_call
    assert "--dbname" not in pg_restore_call
    assert "-d" not in pg_restore_call.split()


def test_restore_drill_roundtrip_real_backup_script(tmp_path: Path) -> None:
    """真实 backup-trpg-master.sh 生成 bundle,再由真实 restore-drill.sh dry-run 校验。

    backup 脚本的受管根硬编码在 /var/backups 与 /var/lib 下,需要 root 或
    passwordless sudo;非特权本地环境跳过,CI(runner 有 sudo)真实覆盖
    backup→restore 全链路与生产 outer tar 契约。
    """
    suffix = uuid.uuid4().hex[:8]
    backup_root = f"/var/backups/trpg-master-gnupgtest-{suffix}"
    runtime_root = f"/var/lib/trpg-master-gnupgtest-{suffix}"
    sudo = [] if os.geteuid() == 0 else ["sudo", "-n"]
    probe = subprocess.run(
        sudo + ["mkdir", "-p", backup_root],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip(
            "/var/backups 不可写且无 passwordless sudo;真实 backup 全链路仅在 CI 覆盖"
        )
    try:
        subprocess.run(sudo + ["chmod", "0700", backup_root], check=True)
        subprocess.run(sudo + ["mkdir", "-p", runtime_root], check=True)
        subprocess.run(sudo + ["chmod", "0700", runtime_root], check=True)
        subprocess.run(sudo + ["chown", str(os.geteuid()), backup_root], check=True)
        subprocess.run(sudo + ["chown", str(os.geteuid()), runtime_root], check=True)
        (Path(runtime_root) / "runtime-marker").write_text(
            "runtime", encoding="utf-8"
        )

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_stub(
            bin_dir,
            "pg_dump",
            "#!/usr/bin/env bash\nprintf 'fake custom-format dump\\n'\n",
        )
        passphrase = tmp_path / "passphrase"
        passphrase.write_text("secret-passphrase", encoding="utf-8")

        backup = subprocess.run(
            ["bash", str(PROJECT_ROOT / "deploy" / "backup-trpg-master.sh")],
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "TRPG_BACKUP_ROOT": backup_root,
                "TRPG_BACKUP_RUNTIME_ROOT": runtime_root,
                "TRPG_BACKUP_PREFIX": "trpg-master",
                "TRPG_DATABASE_URL": "postgresql+psycopg://u:p@h/db",
                "TRPG_BACKUP_PASSPHRASE_FILE": str(passphrase),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert backup.returncode == 0, backup.stderr
        archives = sorted(Path(backup_root).glob("trpg-master-*.tar.gpg"))
        assert archives, "backup script did not publish an archive"

        calls = tmp_path / "pg_restore_calls"
        _write_stub(
            bin_dir,
            "pg_restore",
            f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {calls}
printf 'table users\\ntable worlds\\n'
""",
        )
        restore = subprocess.run(
            ["bash", str(PROJECT_ROOT / "deploy" / "restore-drill.sh"), "--dry-run"],
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "HOME": "/nonexistent",
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "TRPG_PG_RESTORE": str(bin_dir / "pg_restore"),
                "TRPG_BACKUP_PASSPHRASE_FILE": str(passphrase),
                "TRPG_BACKUP_ROOT": backup_root,
                "TRPG_BACKUP_PREFIX": "trpg-master",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert restore.returncode == 0, restore.stderr
        assert "dry-run passed" in restore.stdout
    finally:
        subprocess.run(
            sudo + ["rm", "-rf", backup_root, runtime_root],
            capture_output=True,
        )
