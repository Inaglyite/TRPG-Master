"""Prime @electron/get's cache from the official GitHub release assets API.

The ordinary GitHub download route sometimes returns persistent gateway errors in
CI. The API resolves the same official release asset, and the Electron npm
package's bundled checksum remains the source of trust for the downloaded bytes.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import platform
import re
import socket
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com/repos/electron/electron/releases"
DOWNLOAD_ROOT = "https://github.com/electron/electron/releases/download"
TIMEOUT = 60
CHUNK_SIZE = 1024 * 1024


def _file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _request(url: str, accept: str) -> Request:
    return Request(url, headers={
        "Accept": accept,
        "User-Agent": "trpg-electron-runtime-cache",
        "X-GitHub-Api-Version": "2022-11-28",
    })


def cache_runtime(
    electron_dir: Path, cache_root: Path, *, platform_name: str, arch: str,
) -> Path:
    """Download and atomically publish a checksum-verified Electron runtime zip."""
    package = json.loads((electron_dir / "package.json").read_text(encoding="utf-8"))
    version = package.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version,
    ):
        raise ValueError("Electron package.json has no valid release version")
    if platform_name not in {"linux", "win32", "darwin"}:
        raise ValueError("Unsupported Electron platform")
    if arch not in {"x64", "arm64", "ia32", "armv7l"}:
        raise ValueError("Unsupported Electron architecture")

    filename = f"electron-v{version}-{platform_name}-{arch}.zip"
    checksums = json.loads((electron_dir / "checksums.json").read_text(encoding="utf-8"))
    expected = checksums.get(filename, "")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ValueError(f"No valid bundled SHA256 for {filename}")
    expected = expected.lower()

    # @electron/get hashes the download URL after replacing its path with dirname.
    download_parent = f"{DOWNLOAD_ROOT}/v{version}"
    cache_key = hashlib.sha256(download_parent.encode("utf-8")).hexdigest()
    target = cache_root / cache_key / filename
    if target.is_file():
        if _file_hash(target) == expected:
            print(f"Verified Electron cache hit: {filename}", flush=True)
            return target
        target.unlink()

    with urlopen(_request(f"{API_ROOT}/tags/v{version}", "application/vnd.github+json"),
                 timeout=TIMEOUT) as response:
        release = json.load(response)
    assets = [asset for asset in release.get("assets", []) if asset.get("name") == filename]
    if len(assets) != 1:
        raise ValueError(f"Official Electron release has no unique asset named {filename}")
    asset_id = assets[0].get("id")
    if type(asset_id) is not int or asset_id <= 0:
        raise ValueError("Official Electron asset has no valid numeric ID")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{filename}.", suffix=".tmp", dir=target.parent, delete=False,
        ) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            # Construct this URL locally; never follow metadata-provided asset URLs.
            request = _request(f"{API_ROOT}/assets/{asset_id}", "application/octet-stream")
            with urlopen(request, timeout=TIMEOUT) as response:
                while chunk := response.read(CHUNK_SIZE):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f"SHA256 mismatch for {filename}; download discarded")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        print(f"Cached verified Electron runtime: {filename}", flush=True)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _host_arch() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)


def _safe_network_error(error: BaseException) -> str:
    """Keep retryable error codes, without logging redirected URLs or credentials."""
    if isinstance(error, HTTPError):
        return f"HTTPError {error.code}"
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, TimeoutError):
        return "ETIMEDOUT"
    if isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN:
        return "EAI_AGAIN"
    return errno.errorcode.get(getattr(reason, "errno", None), type(reason).__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--electron-dir", type=Path, default=Path("node_modules/electron"))
    parser.add_argument("--cache-root", type=Path, default=os.environ.get("electron_config_cache"))
    parser.add_argument("--platform", dest="platform_name", default=sys.platform,
                        choices=("linux", "win32", "darwin"))
    parser.add_argument("--arch", default=_host_arch(), choices=("x64", "arm64", "ia32", "armv7l"))
    args = parser.parse_args(argv)
    if args.cache_root is None:
        parser.error("--cache-root or electron_config_cache is required")
    try:
        cache_runtime(args.electron_dir, args.cache_root, platform_name=args.platform_name,
                      arch=args.arch)
    except (HTTPError, URLError, OSError) as error:
        print(f"Electron cache failed: {_safe_network_error(error)}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"Electron cache failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
