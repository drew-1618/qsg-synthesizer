"""`qsg-synthesizer` command: start the dashboard on this computer.

Installed by `uv tool install` / `pipx install` (see README). Loads a `.env`
from the current directory if there is one (or --env-file), starts uvicorn
and opens the dashboard in the default browser. `qsg-synthesizer autostart
on|off|status` manages starting it at login (see autostart.py).
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
from pathlib import Path
from typing import Optional, Sequence

from dotenv import find_dotenv, load_dotenv

from qsg_synthesizer import autostart
from qsg_synthesizer.paths import APP_NAME, default_data_dir

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
# The address the dashboard is opened at. Browsers treat localhost and
# 127.0.0.1 as different sites, each with its own settings, saved keys and
# installed app, so this must match the README's Docker instructions.
BROWSER_HOST = "localhost"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}
# Rotated to .1 at startup once it passes this size.
_MAX_LOG_BYTES = 5 * 1024 * 1024


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
    parser.add_argument(
        "--env-file",
        type=Path,
        help="settings file to load (default: .env in the current folder)",
    )
    parser.add_argument(
        "--log-file", type=Path, help="write output here instead of the terminal"
    )
    commands = parser.add_subparsers(dest="command")
    start_at_login = commands.add_parser(
        "autostart",
        help="start QSG Synthesizer whenever you log in",
        description=(
            "Start QSG Synthesizer whenever you log in, so the installed app "
            "always finds it running. Uses the --port and .env in effect now."
        ),
    )
    start_at_login.add_argument("action", choices=("on", "off", "status"))
    return parser.parse_args(argv)


def default_log_file() -> Path:
    return default_data_dir() / f"{APP_NAME}.log"


def _redirect_output(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if log_file.exists() and log_file.stat().st_size > _MAX_LOG_BYTES:
        log_file.replace(log_file.with_name(log_file.name + ".1"))
    stream = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream


def _env_file(args: argparse.Namespace) -> Optional[Path]:
    if args.env_file is not None:
        return args.env_file.resolve()
    found = find_dotenv(usecwd=True)
    return Path(found).resolve() if found else None


def _autostart(args: argparse.Namespace) -> None:
    try:
        if args.action == "on":
            env_file = _env_file(args)
            print(autostart.enable(args.port, default_log_file(), env_file))
            if env_file:
                print(f"It reads its settings from {env_file}.")
            print(f"Output goes to {default_log_file()}.")
        elif args.action == "off":
            print(autostart.disable())
        else:
            state = "on" if autostart.is_enabled() else "off"
            probe = _probe_url(DEFAULT_HOST, args.port)
            running = "running" if _already_running(probe) else "not running"
            url = _browser_url(DEFAULT_HOST, args.port)
            print(f"Autostart is {state}; the app is {running} at {url}")
    except autostart.AutostartError as exc:
        sys.exit(str(exc))


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


def _browser_url(host: str, port: int) -> str:
    return f"http://{BROWSER_HOST if host in _LOCAL_HOSTS else host}:{port}/"


def _probe_url(host: str, port: int) -> str:
    # 127.0.0.1, not localhost: uvicorn listens on IPv4, and some systems try
    # localhost's IPv6 address first and hang on it.
    return f"http://{'127.0.0.1' if host in _LOCAL_HOSTS else host}:{port}/"


def _already_running(url: str) -> bool:
    """Whether this app (not some other program) is answering at `url`."""
    try:
        with urllib.request.urlopen(f"{url}healthz", timeout=2) as response:
            return json.load(response).get("app") == APP_NAME
    except (OSError, ValueError):
        return False


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(argv)
    if args.command == "autostart":
        _autostart(args)
        return
    if args.log_file is not None:
        _redirect_output(args.log_file)
    # Before importing the app: it reads HISTORY_DIR and the keys at import.
    env_file = _env_file(args)
    if env_file is not None:
        load_dotenv(env_file)

    import uvicorn

    from qsg_synthesizer.services import history

    # Lets qsg_synthesizer.main skip its "auth is off" warning when only this computer
    # can connect.
    os.environ["QSG_BIND_HOST"] = args.host
    url = _browser_url(args.host, args.port)
    if not _port_is_free(args.host, args.port):
        if _already_running(_probe_url(args.host, args.port)):
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
