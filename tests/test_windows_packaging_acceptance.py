from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf8")


def test_windows_package_uses_distinct_deterministic_artifacts() -> None:
    package = json.loads(_read("frontend/package.json"))

    assert package["scripts"]["dist:win"] == (
        "npm run build && electron-builder --win nsis portable --x64 --publish never"
    )
    assert package["build"]["nsis"]["artifactName"] == (
        "trpg-game-setup-${version}-${arch}.${ext}"
    )
    assert package["build"]["portable"]["artifactName"] == (
        "trpg-game-portable-${version}-${arch}.${ext}"
    )


def _artifact_glob(template: str) -> str:
    """artifactName 模板 → 消费侧（验证脚本/workflow）应使用的 x64 exe glob。"""
    return (
        template.replace("${version}", "*")
        .replace("${arch}", "x64")
        .replace("${ext}", "exe")
    )


def test_verifier_and_workflow_match_artifact_names() -> None:
    package = json.loads(_read("frontend/package.json"))
    expected_globs = {
        _artifact_glob(package["build"][target]["artifactName"])
        for target in ("nsis", "portable")
    }

    verifier = _read("packaging/verify_windows_desktop_artifacts.ps1")
    workflow = _read(".github/workflows/windows-package.yml")
    for glob in expected_globs:
        assert glob in verifier
        assert f"frontend/release/{glob}" in workflow
    assert "trpg-master-setup" not in verifier
    assert "trpg-master-portable" not in verifier
    assert "trpg-master-setup" not in workflow
    assert "trpg-master-portable" not in workflow


def test_windows_build_script_is_locked_and_mirrors_are_opt_in() -> None:
    script = _read("packaging/build_windows.ps1")

    assert "[switch]$SkipDependencyInstall" in script
    assert "[switch]$UseChinaMirrors" in script
    assert "npm ci" in script
    assert "npm install" not in script
    assert "if ($UseChinaMirrors)" in script
    assert "npm run dist:win" in script
    assert "-r requirements-packaging.txt" in script
    assert "pyinstaller==6.21.0" in _read("requirements-packaging.txt")


def test_packaged_backend_smoke_covers_runtime_contract() -> None:
    script = _read("packaging/smoke_windows_backend.ps1")

    assert "/api/health" in script
    assert "/api/ready" in script
    assert "/api/modules" in script
    assert "/api/characters" in script
    assert "SELECT version_num FROM alembic_version" in script
    assert "ExpectedMigrationHead" in script
    assert "$Characters.groups" in script
    assert "$CharacterCount -gt 0" in script
    assert "Stop-Process" in script


def test_windows_workflow_builds_smokes_installs_and_uploads() -> None:
    workflow = _read(".github/workflows/windows-package.yml")

    assert "runs-on: windows-latest" in workflow
    assert "./packaging/build_windows.ps1 -SkipDependencyInstall" in workflow
    assert "./packaging/smoke_windows_backend.ps1" in workflow
    assert "./packaging/verify_windows_desktop_artifacts.ps1" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "continue-on-error" not in workflow

    verifier = _read("packaging/verify_windows_desktop_artifacts.ps1")
    assert '$InstallArguments = "/S /D=$InstallRoot"' in verifier
    assert "Get-FileHash $InstalledBackend" in verifier
    assert "Uninstall*.exe" in verifier
    assert "SHA256SUMS.txt" in verifier
    assert "Assert-PortableBootstrap $Portable[0].FullName" in verifier
    assert 'Assert-DesktopLaunch $UnpackedApp[0].FullName "unpacked"' in verifier
    assert "Assert-DesktopLaunch $InstalledApp[0].FullName" in verifier
    assert "/json/list" in verifier
    assert 'EnvironmentVariables.Remove("ELECTRON_RUN_AS_NODE")' in verifier
