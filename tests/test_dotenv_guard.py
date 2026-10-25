"""The load_dotenv() guard in qsg_synthesizer/main.py keeps tests from reading
real API keys."""

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
_SCRIPT = (
    "import dotenv\n"
    "calls = []\n"
    "dotenv.load_dotenv = lambda *a, **k: calls.append(a)\n"
    "import qsg_synthesizer.main\n"
    "print(repr(calls))\n"
)


def _dotenv_calls(under_pytest: bool, launched_by_cli: bool = False) -> list:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTEST_VERSION", "QSG_ENV_LOADED")
    }
    if under_pytest:
        env["PYTEST_VERSION"] = "9.0.0"
    if launched_by_cli:
        env["QSG_ENV_LOADED"] = "1"
    out = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return ast.literal_eval(out.stdout.strip())


def test_dotenv_skipped_under_pytest():
    assert _dotenv_calls(under_pytest=True) == []


def test_dotenv_loaded_from_the_current_folder_only():
    # An explicit path: with none, python-dotenv walks up from main.py's
    # folder, which for an installed app reaches the user's home.
    assert _dotenv_calls(under_pytest=False) == [(str(ROOT / ".env"),)]


def test_dotenv_skipped_when_the_command_already_loaded_it():
    assert _dotenv_calls(under_pytest=False, launched_by_cli=True) == []
