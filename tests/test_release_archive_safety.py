from __future__ import annotations

import gzip
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = (
    PROJECT_ROOT / "deploy" / "install-release.sh",
    PROJECT_ROOT / "deploy" / "install-staging-release.sh",
)
VALIDATOR_MARKER = "# release-archive-validator-v1"


def _validator_source(installer: Path) -> str:
    source = installer.read_text(encoding="utf-8")
    start = source.index(VALIDATOR_MARKER)
    end = source.index("\nPY\n", start)
    return source[start:end] + "\n"


def _run_validator(
    installer: Path,
    archive: Path,
    destination: Path,
    *,
    replacements: dict[str, str] | None = None,
):
    source = _validator_source(installer)
    for original, replacement in (replacements or {}).items():
        source = source.replace(original, replacement)
    return subprocess.run(
        [sys.executable, "-", str(archive), str(destination)],
        input=source,
        text=True,
        capture_output=True,
        check=False,
    )


def _regular_member(bundle: tarfile.TarFile, name: str, body: bytes = b"ok") -> None:
    member = tarfile.TarInfo(name)
    member.size = len(body)
    member.mode = 0o644
    bundle.addfile(member, io.BytesIO(body))


def test_production_and_staging_use_the_same_archive_validator() -> None:
    assert _validator_source(INSTALLERS[0]) == _validator_source(INSTALLERS[1])


@pytest.mark.parametrize("installer", INSTALLERS)
def test_release_validator_extracts_regular_files_only(
    tmp_path: Path,
    installer: Path,
) -> None:
    archive = tmp_path / "safe.tar.gz"
    destination = tmp_path / "release"
    destination.mkdir()
    with tarfile.open(archive, "w:gz") as bundle:
        directory = tarfile.TarInfo("frontend")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        bundle.addfile(directory)
        _regular_member(bundle, "server.py", b"print('safe')\n")
        _regular_member(bundle, "frontend/index.html", b"<main>safe</main>")

    result = _run_validator(installer, archive, destination)

    assert result.returncode == 0, result.stderr
    assert (destination / "server.py").read_bytes() == b"print('safe')\n"
    assert (destination / "frontend" / "index.html").read_bytes() == (
        b"<main>safe</main>"
    )


@pytest.mark.parametrize(
    ("member_name", "member_type"),
    [
        ("../../outside", tarfile.REGTYPE),
        ("/tmp/outside", tarfile.REGTYPE),
        ("src/../outside", tarfile.REGTYPE),
        ("src/link", tarfile.SYMTYPE),
        ("src/hard-link", tarfile.LNKTYPE),
        ("src/fifo", tarfile.FIFOTYPE),
        ("src/new\nline", tarfile.REGTYPE),
    ],
)
def test_release_validator_rejects_traversal_links_and_special_files(
    tmp_path: Path,
    member_name: str,
    member_type: bytes,
) -> None:
    archive = tmp_path / "hostile.tar.gz"
    destination = tmp_path / "release"
    destination.mkdir()
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo(member_name)
        member.type = member_type
        if member_type == tarfile.REGTYPE:
            member.size = 7
            bundle.addfile(member, io.BytesIO(b"hostile"))
        else:
            member.linkname = "../../outside"
            bundle.addfile(member)

    result = _run_validator(INSTALLERS[0], archive, destination)

    assert result.returncode == 2
    assert "invalid release archive" in result.stderr
    assert list(destination.iterdir()) == []
    assert not (tmp_path / "outside").exists()


def test_release_validator_rejects_duplicate_paths_before_extracting(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "duplicate.tar.gz"
    destination = tmp_path / "release"
    destination.mkdir()
    with tarfile.open(archive, "w:gz") as bundle:
        _regular_member(bundle, "server.py", b"first")
        _regular_member(bundle, "server.py", b"second")

    result = _run_validator(INSTALLERS[0], archive, destination)

    assert result.returncode == 2
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("truncate_bytes", (1, 20))
def test_release_validator_rejects_truncated_gzip_without_traceback(
    tmp_path: Path,
    truncate_bytes: int,
) -> None:
    archive = tmp_path / "complete.tar.gz"
    destination = tmp_path / "release"
    destination.mkdir()
    with tarfile.open(archive, "w:gz") as bundle:
        _regular_member(bundle, "payload.bin", bytes(range(256)) * 4096)
    body = archive.read_bytes()
    archive.write_bytes(body[:-truncate_bytes])

    result = _run_validator(INSTALLERS[0], archive, destination)

    assert result.returncode == 2
    assert "invalid release archive" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(destination.iterdir()) == []


def test_release_validator_rejects_declared_huge_member_before_body(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "huge-header.tar.gz"
    destination = tmp_path / "release"
    destination.mkdir()
    member = tarfile.TarInfo("huge.bin")
    member.size = 1024 * 1024 * 1024 + 1
    with gzip.open(archive, "wb") as compressed:
        compressed.write(member.tobuf(format=tarfile.USTAR_FORMAT))

    result = _run_validator(INSTALLERS[0], archive, destination)

    assert result.returncode == 2
    assert "archive member is too large" in result.stderr
    assert list(destination.iterdir()) == []


def test_release_validator_enforces_member_limit_before_extracting(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "too-many.tar.gz"
    destination = tmp_path / "release"
    destination.mkdir()
    with tarfile.open(archive, "w:gz") as bundle:
        for index in range(3):
            _regular_member(bundle, f"{index}.txt")

    result = _run_validator(
        INSTALLERS[0],
        archive,
        destination,
        replacements={"MAX_MEMBERS = 100_000": "MAX_MEMBERS = 2"},
    )

    assert result.returncode == 2
    assert "too many archive records" in result.stderr
    assert list(destination.iterdir()) == []


def test_release_validator_does_not_materialize_tar_member_list() -> None:
    source = _validator_source(INSTALLERS[0])

    assert ".getmembers()" not in source
    assert "preflight_physical_stream(archive_path)" in source
    assert "validate_logical_archive(archive_path)" in source
