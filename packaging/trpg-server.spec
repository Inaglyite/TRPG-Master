# -*- mode: python ; coding: utf-8 -*-

import os
import subprocess
from pathlib import Path

_spec_root = Path(SPECPATH).resolve()
# PyInstaller exposes SPECPATH differently across releases/platforms: it may
# be the spec directory or the project root. Resolve the repository by its
# stable server.py marker instead of assuming a fixed number of parents.
ROOT = _spec_root if (_spec_root / "server.py").is_file() else _spec_root.parent
if not (ROOT / "server.py").is_file():
    raise RuntimeError(f"Could not locate repository root from {SPECPATH!r}")


def tracked_data(*paths):
    """Package only Git-tracked release assets, never ignored runtime data."""

    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", *paths],
            check=True,
            capture_output=True,
        )
        untracked = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "-z",
                "--others",
                "--exclude-standard",
                "--",
                *paths,
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "A Git checkout is required to build a safe release asset manifest"
        ) from exc
    if untracked.stdout:
        names = ", ".join(
            os.fsdecode(raw) for raw in untracked.stdout.split(b"\0") if raw
        )
        raise RuntimeError(
            "Untracked release assets must be reviewed and committed before packaging: "
            + names
        )
    relative_files = [
        Path(os.fsdecode(raw))
        for raw in result.stdout.split(b"\0")
        if raw
    ]
    if not relative_files:
        raise RuntimeError(f"No tracked release assets found for: {', '.join(paths)}")
    packaged = []
    root = ROOT.resolve()
    for relative in relative_files:
        source = (ROOT / relative).resolve()
        if not source.is_file() or not source.is_relative_to(root):
            raise RuntimeError(f"Unsafe or missing release asset: {relative}")
        destination = relative.parent.as_posix()
        packaged.append((str(source), destination if destination != "." else "."))
    return packaged


datas = tracked_data(
    "alembic.ini",
    "characters/default",
    "migrations",
    "mod",
    "rules",
    "schemas",
    "skills",
    "tools",
)

# .env.json、数据库、世界、存档、玩家档案及自定义角色绝不从工作区打包。

block_cipher = None

a = Analysis(
    [str(ROOT / "server.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.loops.auto",
        "httptools",
        "websockets",
        "yaml",
        "alembic",
        "alembic.command",
        "alembic.config",
        "alembic.ddl.postgresql",
        "alembic.ddl.sqlite",
        "alembic.runtime.migration",
        "sqlalchemy.dialects.postgresql",
        "sqlalchemy.dialects.sqlite",
        "psycopg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "pyinstaller_runtime_hook.py")],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="trpg-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="trpg-server",
)
