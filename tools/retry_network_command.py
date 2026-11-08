"""Retry build/download commands only for transient network failures."""
from __future__ import annotations

import re
import subprocess
import sys
import time

_TRANSIENT = re.compile(
    r"(?:response|status|http(?:error)?)\s*(?:code|error)?\s*[:=]?\s*50[234]\b"
    r"|\b(?:ECONNRESET|ETIMEDOUT|EAI_AGAIN|ECONNREFUSED)\b"
    r"|\b(?:socket hang up|connection reset|temporary failure in name resolution)\b",
    re.IGNORECASE,
)


def run(command: list[str], *, attempts: int = 3, delay: float = 5) -> int:
    """Forward output, preserve exit status, and bound retries and retained output."""
    for attempt in range(1, attempts + 1):
        tail = ""
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                tail = (tail + line)[-65536:]
            code = process.wait()
        if code == 0 or attempt == attempts or not _TRANSIENT.search(tail):
            return code
        pause = delay * attempt
        print(f"Transient network failure; retry {attempt + 1}/{attempts} in {pause:g}s.", flush=True)
        time.sleep(pause)
    raise AssertionError("attempts must be positive")


if __name__ == "__main__":
    # Build tools emit UTF-8 even when the Windows parent console uses a legacy codec.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        sys.exit("usage: retry_network_command.py COMMAND [ARG ...]")
    sys.exit(run(sys.argv[1:]))
