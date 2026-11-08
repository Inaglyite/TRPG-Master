import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.retry_network_command import run


@pytest.mark.parametrize("error", ["HTTPError: Response code 504 (Gateway Time-out)", "ETIMEDOUT"])
def test_transient_download_failure_retries_and_then_succeeds(tmp_path, error, capsys):
    marker = tmp_path / "attempts"
    script = (
        "from pathlib import Path; import sys; "
        "p=Path(sys.argv[1]); n=int(p.read_text())+1 if p.exists() else 1; "
        "p.write_text(str(n)); print(sys.argv[2] if n == 1 else 'downloaded'); "
        "sys.exit(1 if n == 1 else 0)"
    )
    assert run([sys.executable, "-c", script, str(marker), error], delay=0) == 0
    assert marker.read_text() == "2"
    assert "retry 2/3" in capsys.readouterr().out


def test_build_errors_are_not_retried_or_masked(capsys):
    assert run([sys.executable, "-c", "print('TypeScript error TS1234'); exit(7)"], delay=0) == 7
    output = capsys.readouterr().out
    assert output.count("TypeScript error") == 1 and "retry" not in output


def test_network_failure_exhausts_bound_and_preserves_failure(capsys):
    assert run([sys.executable, "-c", "print('Response code 503'); exit(6)"], delay=0) == 6
    output = capsys.readouterr().out
    assert output.count("Response code 503") == 3
    assert "retry 4" not in output


def test_cli_forwards_utf8_output_under_ascii_parent_environment():
    script = Path(__file__).resolve().parents[1] / "tools" / "retry_network_command.py"
    result = subprocess.run(
        [sys.executable, str(script), sys.executable, "-c",
         "import sys; sys.stdout.buffer.write('中文构建输出'.encode('utf-8'))"],
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "中文构建输出"
