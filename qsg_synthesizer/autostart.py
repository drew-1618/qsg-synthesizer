"""`qsg-synthesizer autostart on|off|status`: start the app when the user logs in.

So the installed app's icon always finds the dashboard running. Each platform
uses its own per-user mechanism, needing no admin rights:

- Linux: a systemd user service (~/.config/systemd/user).
- macOS: a launchd agent (~/Library/LaunchAgents).
- Windows: the per-user "Run" registry key, launching pythonw (no console).

The login-started app runs with --no-browser and writes its output to a log
file, since there's no terminal to show it.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from qsg_synthesizer.paths import APP_NAME, SOURCE_ROOT, is_source_checkout

SYSTEMD_UNIT = f"{APP_NAME}.service"
LAUNCHD_LABEL = "io.github.drew-1618.qsg-synthesizer"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_VALUE_NAME = "QSG Synthesizer"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class AutostartError(RuntimeError):
    """Autostart couldn't be changed; the message says what to do instead."""


def _run(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


def launch_command(port: int, log_file: Path, env_file: Optional[Path]) -> list[str]:
    """How the login-started app is run: this same Python, as a module, so it
    keeps working wherever uv/pipx put the app."""
    python = Path(sys.executable)
    if sys.platform == "win32" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")  # no console window
    command = [str(python), "-m", "qsg_synthesizer", "--no-browser"]
    command += ["--port", str(port), "--log-file", str(log_file)]
    # Pinned either way: the login copy's working folder (home) may hold an
    # unrelated .env.
    if env_file is not None:
        command += ["--env-file", str(env_file)]
    else:
        command.append("--no-env-file")
    return command


def working_directory() -> Path:
    # From a source checkout `-m qsg_synthesizer` only imports from the repo.
    return SOURCE_ROOT if is_source_checkout() else Path.home()


# ---- Linux: systemd user service ----


def systemd_unit_path(env: Optional[Mapping[str, str]] = None) -> Path:
    environ = os.environ if env is None else env
    config = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config) / "systemd" / "user" / SYSTEMD_UNIT


def _systemd_quote(arg: str) -> str:
    escaped = arg.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"' if any(c in arg for c in ' \t"\\') else escaped


def systemd_unit(command: Sequence[str], workdir: Path) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=QSG Synthesizer dashboard",
            "",
            "[Service]",
            "ExecStart=" + " ".join(_systemd_quote(arg) for arg in command),
            f"WorkingDirectory={_systemd_quote(str(workdir))}",
            "Restart=on-failure",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _check(result: subprocess.CompletedProcess, what: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise AutostartError(f"{what} failed: {detail}")


def _enable_systemd(command: Sequence[str], run: Runner) -> str:
    path = systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(systemd_unit(command, working_directory()), encoding="utf-8")
    try:
        _check(run(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
        _check(
            run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT]),
            "systemctl enable",
        )
    except (AutostartError, FileNotFoundError) as exc:
        path.unlink(missing_ok=True)
        raise AutostartError(
            f"Couldn't set up the systemd user service ({exc}). If your system "
            "doesn't use systemd, add `qsg-synthesizer --no-browser` to your "
            "desktop's startup applications instead."
        ) from exc
    return f"Set up the systemd user service {path}; it's running now."


def _disable_systemd(run: Runner) -> str:
    path = systemd_unit_path()
    if not path.exists():
        return "Autostart wasn't on."
    try:
        run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT])
        path.unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"])
    except FileNotFoundError:
        path.unlink(missing_ok=True)
    return "Removed the systemd user service and stopped the app."


# ---- macOS: launchd agent ----


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def launchd_plist(command: Sequence[str], workdir: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": list(command),
            "WorkingDirectory": str(workdir),
            "RunAtLoad": True,
            # Restart it if it crashes, not after a clean exit.
            "KeepAlive": {"SuccessfulExit": False},
        }
    )


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"  # type: ignore[attr-defined,unused-ignore]


def _enable_launchd(command: Sequence[str], run: Runner) -> str:
    path = launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace an older agent (e.g. from a previous version) cleanly.
    run(["launchctl", "bootout", f"{_launchd_domain()}/{LAUNCHD_LABEL}"])
    path.write_bytes(launchd_plist(command, working_directory()))
    result = run(["launchctl", "bootstrap", _launchd_domain(), str(path)])
    if result.returncode != 0:
        path.unlink(missing_ok=True)
        _check(result, "launchctl bootstrap")
    return f"Set up the login item {path}; it's running now."


def _disable_launchd(run: Runner) -> str:
    path = launchd_plist_path()
    if not path.exists():
        return "Autostart wasn't on."
    run(["launchctl", "bootout", f"{_launchd_domain()}/{LAUNCHD_LABEL}"])
    path.unlink(missing_ok=True)
    return "Removed the login item and stopped the app."


# ---- Windows: per-user Run key ----


def _winreg():  # pragma: no cover - only importable on Windows
    import winreg

    return winreg


def _enable_windows(command: Sequence[str], winreg=None, popen=subprocess.Popen) -> str:
    winreg = winreg or _winreg()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(
            key, WINDOWS_VALUE_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command)
        )
    # Start it now too, detached and without a console window.
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    popen(list(command), creationflags=flags, close_fds=True)
    return "Added QSG Synthesizer to your startup apps; it's running now."


def _windows_enabled(winreg=None) -> bool:
    winreg = winreg or _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
            winreg.QueryValueEx(key, WINDOWS_VALUE_NAME)
        return True
    except OSError:
        return False


def _disable_windows(winreg=None) -> str:
    winreg = winreg or _winreg()
    if not _windows_enabled(winreg):
        return "Autostart wasn't on."
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.DeleteValue(key, WINDOWS_VALUE_NAME)
    return (
        "Removed QSG Synthesizer from your startup apps. The copy running now "
        "keeps going until you sign out or restart."
    )


# ---- Entry points used by cli.py ----


def enable(
    port: int,
    log_file: Path,
    env_file: Optional[Path],
    platform: str = sys.platform,
    run: Runner = _run,
) -> str:
    command = launch_command(port, log_file, env_file)
    if platform.startswith("linux"):
        return _enable_systemd(command, run)
    if platform == "darwin":
        return _enable_launchd(command, run)
    if platform == "win32":
        return _enable_windows(command)
    raise AutostartError(f"Autostart isn't supported on {platform}.")


def disable(platform: str = sys.platform, run: Runner = _run) -> str:
    if platform.startswith("linux"):
        return _disable_systemd(run)
    if platform == "darwin":
        return _disable_launchd(run)
    if platform == "win32":
        return _disable_windows()
    raise AutostartError(f"Autostart isn't supported on {platform}.")


def is_enabled(platform: str = sys.platform) -> bool:
    if platform.startswith("linux"):
        return systemd_unit_path().exists()
    if platform == "darwin":
        return launchd_plist_path().exists()
    if platform == "win32":
        return _windows_enabled()
    return False
