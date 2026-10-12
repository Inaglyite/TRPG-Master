"""Optional, versioned browser-editor updates; the bundled editor is the fallback."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

EDITOR_API_VERSION = 1
REPOSITORY = "Inaglyite/TRPG_Mod_Editor"
MAX_DOWNLOAD = 40 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
VERSION_RE = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
BUNDLE_RE = re.compile(r"\d+\.\d+\.\d+-[0-9a-f]{12}\Z")


class EditorUpdateError(ValueError):
    pass


def version_key(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise EditorUpdateError("编辑器版本必须是 major.minor.patch")
    return tuple(map(int, value.split(".")))


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EditorUpdateError("更新元数据无效")
    return value


def download_bytes(url: str, limit: int, timeout: int = 8) -> bytes:
    """Only HTTPS GitHub release/API URLs and their asset redirects are allowed."""
    hosts = {"api.github.com", "github.com", "release-assets.githubusercontent.com",
             "objects.githubusercontent.com"}
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in hosts:
        raise EditorUpdateError("更新地址不受信任")
    request = Request(url, headers={"User-Agent": "TRPG-Master-Editor-Updater",
                                    "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=timeout) as response:
        final = urlsplit(response.url)
        if final.scheme != "https" or final.hostname not in hosts:
            raise EditorUpdateError("更新下载跳转不受信任")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise EditorUpdateError("更新文件超过大小限制")
    return data


class EditorUpdateStore:
    def __init__(self, bundled: Path, runtime_root: Path, *, fetch=download_bytes,
                 test_tag: str = ""):
        self.bundled = Path(bundled)
        self.root = Path(runtime_root) / "editor-updates"
        self.fetch = fetch
        if test_tag and not re.fullmatch(r"editor-web-test-[A-Za-z0-9._-]+", test_tag):
            raise EditorUpdateError("测试更新标签无效")
        self.test_tag = test_tag
        self._lock = threading.Lock()
        self._offer: dict | None = None
        self._checked_at = 0.0

    def bundled_version(self) -> str:
        try:
            version = _read_json(self.bundled / "editor-build.json")["version"]
            version_key(version)
            return version
        except (OSError, ValueError, KeyError):
            return "0.0.0"

    def bundle_directory(self, bundle_id: str) -> Path | None:
        if not BUNDLE_RE.fullmatch(bundle_id):
            return None
        folder = self.root / "versions" / bundle_id
        try:
            metadata = _read_json(folder / "installed.json")
            version_key(metadata["version"])
            if (metadata["editor_api"] != EDITOR_API_VERSION
                    or bundle_id != f'{metadata["version"]}-{metadata["sha256"][:12]}'):
                return None
            dist = folder / "dist"
            if (not (dist / "index.html").is_file()
                    or hashlib.sha256((dist / "index.html").read_bytes()).hexdigest()
                    != metadata["index_sha256"]):
                return None
            return dist
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def current(self) -> dict:
        fallback = {"version": self.bundled_version(), "source": "bundled"}
        try:
            bundle_id = _read_json(self.root / "active.json")["bundle_id"]
            dist = self.bundle_directory(bundle_id)
            if dist is not None:
                metadata = _read_json(dist.parent / "installed.json")
                if version_key(metadata["version"]) > version_key(fallback["version"]):
                    return {"version": metadata["version"], "source": "downloaded",
                            "bundle_id": bundle_id}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return fallback

    def check(self, *, force: bool = False) -> dict:
        with self._lock:
            current = self.current()
            if not force and self._checked_at and time.monotonic() - self._checked_at < 600:
                return self._check_result(current)
            api = f"https://api.github.com/repos/{REPOSITORY}/releases"
            if self.test_tag:
                releases = [json.loads(self.fetch(f"{api}/tags/{self.test_tag}", 128 * 1024))]
            else:
                releases = json.loads(self.fetch(f"{api}?per_page=30", 1024 * 1024))
            candidates = []
            for release in releases:
                if release.get("draft") or (release.get("prerelease") and not self.test_tag):
                    continue
                tag = release.get("tag_name", "")
                if self.test_tag:
                    if tag != self.test_tag:
                        continue
                elif not tag.startswith("editor-web-v") or not VERSION_RE.fullmatch(tag[12:]):
                    continue
                elif version_key(tag[12:]) <= version_key(current["version"]):
                    continue
                assets = {asset["name"]: asset for asset in release.get("assets", [])}
                if not {"editor-web-update.json", "editor-web.zip"} <= assets.keys():
                    continue
                prefix = f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
                manifest_url = assets["editor-web-update.json"].get("browser_download_url")
                bundle_url = assets["editor-web.zip"].get("browser_download_url")
                if manifest_url != prefix + "editor-web-update.json" or bundle_url != prefix + "editor-web.zip":
                    raise EditorUpdateError("更新包来源与仓库不一致")
                manifest = json.loads(self.fetch(manifest_url, 128 * 1024))
                version_key(manifest.get("version"))
                if not self.test_tag and tag != f'editor-web-v{manifest["version"]}':
                    raise EditorUpdateError("更新标签与版本不一致")
                if type(manifest.get("editor_api")) is not int or manifest["editor_api"] != EDITOR_API_VERSION:
                    continue
                if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("sha256", ""))):
                    raise EditorUpdateError("更新校验和无效")
                if type(manifest.get("size")) is not int or not 0 < manifest["size"] <= MAX_DOWNLOAD:
                    raise EditorUpdateError("更新包大小无效")
                candidates.append({**manifest, "url": bundle_url,
                                   "notes": str(manifest.get("notes", ""))[:16000]})
            self._offer = max(candidates, key=lambda item: version_key(item["version"]), default=None)
            self._checked_at = time.monotonic()
            return self._check_result(current)

    def _check_result(self, current: dict) -> dict:
        offer = self._offer
        if offer and version_key(offer["version"]) <= version_key(current["version"]):
            offer = None
        return {"ok": True, "enabled": True, "current": current,
                "update": {key: offer[key] for key in ("version", "sha256", "notes", "size")} if offer else None,
                "test_channel": bool(self.test_tag)}

    def install(self, version: str, sha256: str) -> dict:
        with self._lock:
            offer = self._offer
            if (not offer or offer["version"] != version or offer["sha256"] != sha256
                    or time.monotonic() - self._checked_at > 1800):
                raise EditorUpdateError("更新信息已变化，请重新检查更新")
            if version_key(version) <= version_key(self.current()["version"]):
                return {"ok": True, "current": self.current()}
            data = self.fetch(offer["url"], MAX_DOWNLOAD, 45)
            if len(data) != offer["size"] or hashlib.sha256(data).hexdigest() != sha256:
                raise EditorUpdateError("更新包校验失败，已保留原版本")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            versions = self.root / "versions"
            versions.mkdir(exist_ok=True)
            work = Path(tempfile.mkdtemp(prefix=".install-", dir=self.root))
            try:
                dist = work / "dist"
                dist.mkdir()
                self._extract(data, dist)
                build = _read_json(dist / "editor-build.json")
                if build.get("version") != version or build.get("editor_api") != EDITOR_API_VERSION:
                    raise EditorUpdateError("更新包版本或接口不兼容")
                metadata = {"version": version, "sha256": sha256,
                            "editor_api": EDITOR_API_VERSION,
                            "index_sha256": hashlib.sha256((dist / "index.html").read_bytes()).hexdigest()}
                (work / "installed.json").write_text(json.dumps(metadata), encoding="utf-8")
                bundle_id = f"{version}-{sha256[:12]}"
                destination = versions / bundle_id
                if not destination.exists():
                    os.replace(work, destination)
                elif self.bundle_directory(bundle_id) is None:
                    raise EditorUpdateError("已有更新目录损坏，已保留原版本")
                # Activation is the last operation; previous files remain available to old tabs.
                descriptor = self.root / "active.tmp"
                descriptor.write_text(json.dumps({"bundle_id": bundle_id}), encoding="utf-8")
                os.replace(descriptor, self.root / "active.json")
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                raise EditorUpdateError(str(exc)) from exc
            finally:
                shutil.rmtree(work, ignore_errors=True)
            return {"ok": True, "current": self.current()}

    @staticmethod
    def _extract(data: bytes, destination: Path) -> None:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 3000 or sum(info.file_size for info in entries) > MAX_EXPANDED:
                raise EditorUpdateError("更新包解压大小超过限制")
            seen = set()
            for info in entries:
                name = info.filename
                relative = PurePosixPath(name)
                mode = info.external_attr >> 16
                if (not name or "\\" in name or ":" in name or relative.is_absolute()
                        or any(part in {"..", "."} for part in name.split("/"))
                        or stat.S_ISLNK(mode) or info.flag_bits & 1
                        or name.casefold() in seen):
                    raise EditorUpdateError("更新包包含不安全路径")
                seen.add(name.casefold())
                if info.is_dir():
                    continue
                if relative.suffix.lower() not in {".html", ".js", ".css", ".json", ".png", ".jpg",
                                                    ".jpeg", ".webp", ".svg", ".ico", ".woff", ".woff2", ".txt"}:
                    raise EditorUpdateError("更新包包含非网页资源")
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        if not (destination / "index.html").is_file():
            raise EditorUpdateError("更新包缺少入口页面")
