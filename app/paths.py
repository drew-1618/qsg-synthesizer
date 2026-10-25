"""Where the app finds its bundled files and keeps its data.

The app runs three ways: from a source checkout (`uvicorn app.main:app`), in
the Docker image (which copies the repo's `.claude/` and `sample_docs/` next
to `app/`), and as an installed package (`uv tool install` / `pipx`), where
only `app/` exists and the build copies the extractor prompt and sample PDFs
into `app/_bundled/`. Data goes to `<repo>/data` in a checkout, as it always
has, and to the platform's per-user data folder for an installed package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Optional

APP_NAME = "qsg-synthesizer"

PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIR.parent
BUNDLED_DIR = PACKAGE_DIR / "_bundled"

_SOURCE_RESOURCES = {
    "agents": SOURCE_ROOT / ".claude" / "agents",
    "sample_docs": SOURCE_ROOT / "sample_docs",
}


def resource_dir(name: str) -> Path:
    """The `agents` or `sample_docs` folder: the repo's own when it's there
    (checkout, Docker), else the copy bundled into an installed package."""
    source = _SOURCE_RESOURCES[name]
    return source if source.is_dir() else BUNDLED_DIR / name


def is_source_checkout(root: Path = SOURCE_ROOT) -> bool:
    return (root / "pyproject.toml").is_file()


def user_data_dir(
    platform: str = sys.platform,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """The per-user data folder for an installed app, following each
    platform's convention."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    if platform == "win32":
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        return Path(base) / APP_NAME if base else home / "AppData" / "Local" / APP_NAME
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / APP_NAME


def default_data_dir() -> Path:
    """`<repo>/data` in a source checkout, else the per-user data folder.
    (The Docker image sets HISTORY_DIR explicitly.)"""
    return SOURCE_ROOT / "data" if is_source_checkout() else user_data_dir()
