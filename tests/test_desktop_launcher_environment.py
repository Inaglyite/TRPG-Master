from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _environment(root: Path, name: str, *, valid: bool = True) -> Path:
    environment = root / name
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(environment)],
        check=True,
        capture_output=True,
        text=True,
    )
    if not valid:
        config = environment / "pyvenv.cfg"
        config.write_text(
            config.read_text(encoding="utf8").replace(
                f"version = {sys.version_info.major}.{sys.version_info.minor}",
                "version = 2.7",
            ),
            encoding="utf8",
        )
    return environment


def _run_helpers(root: Path, commands: str) -> subprocess.CompletedProcess[str]:
    launcher = (PROJECT_ROOT / "start_desktop.sh").read_text(encoding="utf8")
    helpers = launcher[
        launcher.index("backend_dependencies_available() {") : launcher.index(
            '\nif [ "$BACKEND_ONLY" = true ]; then'
        )
    ]
    harness = root / "launcher-helpers.sh"
    harness.write_text("set -u\n" + helpers + "\n" + commands, encoding="utf8")
    return subprocess.run(
        ["bash", str(harness)],
        cwd=root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_healthy_environment_without_pip_is_usable(tmp_path: Path) -> None:
    _environment(tmp_path, "venv")
    result = _run_helpers(tmp_path, "backend_virtualenv_usable venv\n")
    assert result.returncode == 0, result.stderr


def test_system_python_upgrade_is_detected_without_modifying_environment(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path, "venv", valid=False)
    config = environment / "pyvenv.cfg"
    before = config.read_bytes()
    result = _run_helpers(tmp_path, "backend_virtualenv_usable venv\n")
    assert result.returncode == 1
    assert config.read_bytes() == before


def test_invalid_primary_environment_falls_back_to_healthy_secondary(
    tmp_path: Path,
) -> None:
    _environment(tmp_path, "venv", valid=False)
    secondary = _environment(tmp_path, ".venv")
    result = _run_helpers(
        tmp_path,
        'activate_backend_environment || exit $?\nprintf "selected=%s\\n" "$VIRTUAL_ENV"\n',
    )
    assert result.returncode == 0, result.stderr
    assert "venv 虚拟环境已失效" in result.stdout
    assert f"selected={secondary}" in result.stdout


def test_two_invalid_environments_fail_before_install_or_database_work(
    tmp_path: Path,
) -> None:
    environments = [_environment(tmp_path, name, valid=False) for name in ("venv", ".venv")]
    before = [(environment / "pyvenv.cfg").read_bytes() for environment in environments]
    result = _run_helpers(
        tmp_path,
        "backend_dependencies_available() { return 1; }\nactivate_backend_environment\n",
    )
    assert result.returncode == 1
    assert "没有可用的后端虚拟环境" in result.stdout
    assert "安装/更新" not in result.stdout
    assert "数据库迁移" not in result.stdout
    assert [(environment / "pyvenv.cfg").read_bytes() for environment in environments] == before


def test_preprovisioned_interpreter_does_not_create_virtualenv(tmp_path: Path) -> None:
    result = _run_helpers(
        tmp_path,
        "backend_dependencies_available() { return 0; }\nactivate_backend_environment\n",
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "venv").exists()
    assert not (tmp_path / ".venv").exists()


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_missing_pip_is_bootstrapped_before_install(
    tmp_path: Path, repair_succeeds: bool,
) -> None:
    repair_code = 0 if repair_succeeds else 1
    result = _run_helpers(
        tmp_path,
        f"""
backend_dependencies_available() {{ return 1; }}
python3() {{
    if [ "$*" = "-m pip --version" ]; then
        return 1
    fi
    if [ "$*" = "-m ensurepip --upgrade" ]; then
        echo "repair-called"
        return {repair_code}
    fi
    if [ "$*" = "-m pip install --disable-pip-version-check -r requirements.txt" ]; then
        echo "install-called"
        return 0
    fi
    echo "unexpected-python-invocation" >&2
    return 99
}}
ensure_backend_dependencies
""",
    )
    assert "repair-called" in result.stdout
    assert "unexpected-python-invocation" not in result.stderr
    if repair_succeeds:
        assert result.returncode == 0
        assert result.stdout.index("repair-called") < result.stdout.index("install-called")
    else:
        assert result.returncode == 1
        assert "无法修复 pip" in result.stdout
        assert "install-called" not in result.stdout


def test_available_dependencies_do_not_require_pip_or_install(tmp_path: Path) -> None:
    result = _run_helpers(
        tmp_path,
        """
backend_dependencies_available() { return 0; }
python3() { echo "unexpected-install"; return 99; }
ensure_backend_dependencies
""",
    )
    assert result.returncode == 0
    assert "unexpected-install" not in result.stdout
