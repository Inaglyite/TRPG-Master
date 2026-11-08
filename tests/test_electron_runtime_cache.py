from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from tools import cache_electron_runtime as cache

VERSION = "123.4.5-beta.1"
FILENAME = f"electron-v{VERSION}-win32-x64.zip"
PAYLOAD = b"verified official runtime zip bytes\x00\xff"


@pytest.fixture
def installation(tmp_path):
    electron_dir = tmp_path / "electron"
    electron_dir.mkdir()
    (electron_dir / "package.json").write_text(json.dumps({"version": VERSION}))
    (electron_dir / "checksums.json").write_text(json.dumps({
        FILENAME: hashlib.sha256(PAYLOAD).hexdigest(),
    }))
    return electron_dir, tmp_path / "cache"


def _download(installation):
    electron_dir, cache_root = installation
    return cache.cache_runtime(electron_dir, cache_root, platform_name="win32", arch="x64")


def _target(cache_root: Path) -> Path:
    parent_url = f"https://github.com/electron/electron/releases/download/v{VERSION}"
    return cache_root / hashlib.sha256(parent_url.encode()).hexdigest() / FILENAME


def _mock_release(monkeypatch, payload=PAYLOAD):
    calls = []

    def urlopen(request, *, timeout):
        calls.append(request)
        assert timeout == 60
        assert request.get_header("User-agent")
        if len(calls) == 1:
            assert request.full_url == (
                f"https://api.github.com/repos/electron/electron/releases/tags/v{VERSION}"
            )
            assert request.get_header("Accept") == "application/vnd.github+json"
            return io.BytesIO(json.dumps({"assets": [
                {"id": 100, "name": FILENAME.replace("x64", "arm64")},
                {"id": 345, "name": FILENAME, "url": "https://untrusted.invalid/asset"},
            ]}).encode())
        assert len(calls) == 2, "downloads must not retry internally"
        assert request.full_url == "https://api.github.com/repos/electron/electron/releases/assets/345"
        assert request.get_header("Accept") == "application/octet-stream"
        return io.BytesIO(payload)

    monkeypatch.setattr(cache, "urlopen", urlopen)
    return calls


def test_selects_exact_official_asset_and_publishes_only_verified_bytes(
    installation, monkeypatch,
):
    calls = _mock_release(monkeypatch)
    target = _target(installation[1])
    real_replace = cache.os.replace
    replaced = []

    def checked_replace(source, destination):
        source = Path(source)
        assert source.parent == target.parent
        assert source.read_bytes() == PAYLOAD
        assert destination == target
        assert not target.exists()
        replaced.append(source)
        real_replace(source, destination)

    monkeypatch.setattr(cache.os, "replace", checked_replace)
    assert _download(installation) == target
    assert target.read_bytes() == PAYLOAD
    assert len(calls) == 2 and len(replaced) == 1
    assert list(target.parent.iterdir()) == [target]


def test_verified_cache_hit_does_not_contact_network(installation, monkeypatch):
    target = _target(installation[1])
    target.parent.mkdir(parents=True)
    target.write_bytes(PAYLOAD)

    def no_network(*args, **kwargs):
        pytest.fail("checksum-verified cache hits must work offline")

    monkeypatch.setattr(cache, "urlopen", no_network)
    assert _download(installation) == target


def test_corrupt_existing_cache_is_replaced_after_verification(installation, monkeypatch):
    target = _target(installation[1])
    target.parent.mkdir(parents=True)
    target.write_bytes(b"incomplete old download")
    calls = _mock_release(monkeypatch)
    assert _download(installation).read_bytes() == PAYLOAD
    assert len(calls) == 2


def test_bad_download_hash_never_publishes_or_leaves_temporary_files(installation, monkeypatch):
    _mock_release(monkeypatch, payload=b"gateway error returned with HTTP 200")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        _download(installation)
    assert not any(path.is_file() for path in installation[1].rglob("*"))


@pytest.mark.parametrize("phase", ["metadata", "asset", "stream"])
def test_network_failure_propagates_without_publishing_partial_data(
    installation, monkeypatch, phase,
):
    error = HTTPError("https://secret-signed-url.invalid/?token=private", 504, "Gateway", {}, None)
    calls = []

    class BrokenStream(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise error
            return super().read(3)

    def urlopen(request, *, timeout):
        calls.append(request)
        if len(calls) == 1:
            if phase == "metadata":
                raise error
            return io.BytesIO(json.dumps({"assets": [{"id": 12, "name": FILENAME}]}).encode())
        if phase == "asset":
            raise error
        return BrokenStream(PAYLOAD)

    monkeypatch.setattr(cache, "urlopen", urlopen)
    with pytest.raises(HTTPError) as caught:
        _download(installation)
    assert caught.value is error
    assert len(calls) == (1 if phase == "metadata" else 2)
    assert not any(path.is_file() for path in installation[1].rglob("*"))


@pytest.mark.parametrize("assets", [
    [],
    [{"name": FILENAME, "id": 1}, {"name": FILENAME, "id": 2}],
    [{"name": FILENAME, "id": "https://untrusted.invalid"}],
    [{"name": FILENAME, "id": True}],
])
def test_missing_ambiguous_or_invalid_asset_fails_before_download(
    installation, monkeypatch, assets,
):
    calls = []

    def urlopen(request, *, timeout):
        calls.append(request)
        return io.BytesIO(json.dumps({"assets": assets}).encode())

    monkeypatch.setattr(cache, "urlopen", urlopen)
    with pytest.raises(ValueError):
        _download(installation)
    assert len(calls) == 1
    assert not installation[1].exists()


def test_missing_bundled_checksum_fails_before_network(installation, monkeypatch):
    (installation[0] / "checksums.json").write_text("{}")
    monkeypatch.setattr(cache, "urlopen", lambda *args, **kwargs: pytest.fail("network requested"))
    with pytest.raises(ValueError, match="bundled SHA256"):
        _download(installation)


def test_cli_uses_cache_environment_and_installed_package_default(monkeypatch, tmp_path):
    monkeypatch.setenv("electron_config_cache", str(tmp_path))
    observed = []
    monkeypatch.setattr(cache, "cache_runtime", lambda *args, **kwargs: observed.append((args, kwargs)))
    assert cache.main(["--platform", "linux", "--arch", "x64"]) == 0
    assert observed == [((Path("node_modules/electron"), tmp_path), {
        "platform_name": "linux", "arch": "x64",
    })]


@pytest.mark.parametrize("error, expected", [
    (HTTPError("https://private.invalid/?token=secret", 504, "private URL", {}, None), "HTTPError 504"),
    (URLError(TimeoutError("https://private.invalid/?token=secret")), "ETIMEDOUT"),
])
def test_cli_retains_retryable_error_code_without_disclosing_signed_url(
    tmp_path, monkeypatch, capsys, error, expected,
):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cache, "cache_runtime", fail)
    assert cache.main(["--cache-root", str(tmp_path), "--platform", "linux", "--arch", "x64"]) == 1
    output = capsys.readouterr().err
    assert expected in output
    assert "https:" not in output and "secret" not in output and "private" not in output
