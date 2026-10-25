"""The load_dotenv() guard in qsg_synthesizer/main.py keeps tests from reading
real API keys."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
_SCRIPT = (
    "import dotenv\n"
    "calls = []\n"
    "dotenv.load_dotenv = lambda *a, **k: calls.append(1)\n"
    "import qsg_synthesizer.main\n"
    "print(len(calls))\n"
)


def _loads_dotenv(under_pytest: bool) -> int:
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_VERSION"}
    if under_pytest:
        env["PYTEST_VERSION"] = "9.0.0"
    out = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip())


def test_dotenv_skipped_under_pytest():
    assert _loads_dotenv(under_pytest=True) == 0


def test_dotenv_loaded_outside_pytest():
    assert _loads_dotenv(under_pytest=False) == 1
