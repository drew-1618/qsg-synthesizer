"""`qsg-synthesizer` command: start the dashboard on this computer.

Installed by `uv tool install` / `pipx install` (see README). Loads a `.env`
from the current directory if there is one, starts uvicorn and opens the
dashboard in the default browser.
"""

from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from typing import Optional, Sequence

from dotenv import find_dotenv, load_dotenv

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qsg-synthesizer",
        description="Compare telecom quick start guides and datasheets.",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}"
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            f"default {DEFAULT_HOST} (this computer only). Use 0.0.0.0 to allow "
            "other machines, and set APP_USERNAME/APP_PASSWORD first."
        ),
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="don't open the dashboard"
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(argv)
    # Before importing the app: it reads HISTORY_DIR and the keys at import.
    load_dotenv(find_dotenv(usecwd=True))

    import uvicorn

    from app.services import history

    # Lets app.main skip its "auth is off" warning when only this computer
    # can connect.
    os.environ["QSG_BIND_HOST"] = args.host
    browse_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{browse_host}:{args.port}/"
    print(f"QSG Synthesizer: {url}")
    print(f"History is saved in {history.HISTORY_DIR}")
    print("Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
