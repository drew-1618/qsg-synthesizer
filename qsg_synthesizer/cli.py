"""`qsg-synthesizer` command: start the dashboard on this computer.

Installed by `uv tool install` / `pipx install` (see README). Loads a `.env`
from the current directory if there is one, starts uvicorn and opens the
dashboard in the default browser.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from typing import Optional, Sequence

from dotenv import find_dotenv, load_dotenv

from qsg_synthesizer.paths import APP_NAME

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


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as sock:
        # Same as uvicorn, so a port in TIME_WAIT counts as free. (On Windows
        # SO_REUSEADDR would let two servers share a port, so skip it there.)
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _already_running(url: str) -> bool:
    """Whether this app (not some other program) is answering at `url`."""
    try:
        with urllib.request.urlopen(f"{url}healthz", timeout=2) as response:
            return json.load(response).get("app") == APP_NAME
    except (OSError, ValueError):
        return False


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(argv)
    # Before importing the app: it reads HISTORY_DIR and the keys at import.
    load_dotenv(find_dotenv(usecwd=True))

    import uvicorn

    from qsg_synthesizer.services import history

    # Lets qsg_synthesizer.main skip its "auth is off" warning when only this computer
    # can connect.
    os.environ["QSG_BIND_HOST"] = args.host
    browse_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{browse_host}:{args.port}/"
    if not _port_is_free(args.host, args.port):
        if _already_running(url):
            # e.g. started at login: just open it.
            print(f"QSG Synthesizer is already running: {url}", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return
        sys.exit(
            f"Port {args.port} is in use by another program, so QSG Synthesizer "
            f"can't start. Close that program, or run qsg-synthesizer --port "
            f"<another port>. Your saved settings and the installed app belong "
            f"to port {DEFAULT_PORT}, so they won't carry over to another port."
        )
    print(f"QSG Synthesizer: {url}")
    print(f"History is saved in {history.HISTORY_DIR}")
    print("Press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("qsg_synthesizer.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
