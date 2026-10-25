"""`qsg-synthesizer autostart`: per-platform login entries, with the system
tools (systemctl, launchctl, the Windows registry) faked."""

import plistlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from qsg_synthesizer import autostart, cli

LOG = Path("/data/qsg-synthesizer.log")


class FakeRunner:
    def __init__(self, fail: str = ""):
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, command):
        self.calls.append(list(command))
        code = 1 if self.fail and self.fail in command else 0
        return subprocess.CompletedProcess(command, code, "", "boom" if code else "")


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values: dict[str, str] = {}

    def OpenKey(self, *args):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def DeleteValue(self, key, name):
        del self.values[name]


def test_launch_command_runs_this_python_as_a_module():
    command = autostart.launch_command(8000, LOG, Path("/home/u/.env"))
    assert command[1:4] == ["-m", "qsg_synthesizer", "--no-browser"]
    assert command[0] == sys.executable
    assert command[command.index("--port") + 1] == "8000"
    assert command[command.index("--log-file") + 1] == str(LOG)
    assert command[command.index("--env-file") + 1] == "/home/u/.env"
    assert "--env-file" not in autostart.launch_command(8000, LOG, None)


class TestLinux:
    @pytest.fixture(autouse=True)
    def config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        return tmp_path

    def test_on_writes_a_user_service_and_starts_it(self, config_home):
        run = FakeRunner()
        message = autostart.enable(8000, LOG, None, platform="linux", run=run)
        unit = (config_home / "systemd/user/qsg-synthesizer.service").read_text()
        assert "ExecStart=" in unit and "-m qsg_synthesizer --no-browser" in unit
        assert "WantedBy=default.target" in unit
        assert ["systemctl", "--user", "enable", "--now", autostart.SYSTEMD_UNIT] in (
            run.calls
        )
        assert "running now" in message
        assert autostart.is_enabled("linux")

    def test_failure_cleans_up_and_suggests_an_alternative(self, config_home):
        run = FakeRunner(fail="enable")
        with pytest.raises(autostart.AutostartError, match="startup applications"):
            autostart.enable(8000, LOG, None, platform="linux", run=run)
        assert not autostart.is_enabled("linux")

    def test_missing_systemctl_is_explained(self):
        def no_systemctl(command):
            raise FileNotFoundError("systemctl")

        with pytest.raises(autostart.AutostartError, match="systemd"):
            autostart.enable(8000, LOG, None, platform="linux", run=no_systemctl)

    def test_off_stops_and_removes_the_service(self):
        autostart.enable(8000, LOG, None, platform="linux", run=FakeRunner())
        run = FakeRunner()
        assert "Removed" in autostart.disable(platform="linux", run=run)
        assert ["systemctl", "--user", "disable", "--now", autostart.SYSTEMD_UNIT] in (
            run.calls
        )
        assert not autostart.is_enabled("linux")
        assert autostart.disable(platform="linux", run=run) == "Autostart wasn't on."

    def test_paths_with_spaces_and_percent_are_quoted(self):
        unit = autostart.systemd_unit(["/opt/my apps/python", "50%"], Path("/home/u"))
        assert 'ExecStart="/opt/my apps/python" 50%%' in unit


class TestMacOS:
    @pytest.fixture(autouse=True)
    def home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(autostart.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(autostart, "_launchd_domain", lambda: "gui/501")
        return tmp_path

    def test_on_writes_a_launch_agent_and_loads_it(self, home):
        run = FakeRunner()
        autostart.enable(8000, LOG, None, platform="darwin", run=run)
        path = home / "Library/LaunchAgents" / f"{autostart.LAUNCHD_LABEL}.plist"
        agent = plistlib.loads(path.read_bytes())
        assert agent["Label"] == autostart.LAUNCHD_LABEL
        assert agent["RunAtLoad"] is True
        assert agent["ProgramArguments"][1:3] == ["-m", "qsg_synthesizer"]
        assert ["launchctl", "bootstrap", "gui/501", str(path)] in run.calls

    def test_failed_load_removes_the_agent(self):
        with pytest.raises(autostart.AutostartError):
            autostart.enable(
                8000, LOG, None, platform="darwin", run=FakeRunner(fail="bootstrap")
            )
        assert not autostart.is_enabled("darwin")

    def test_off_unloads_and_removes_it(self):
        autostart.enable(8000, LOG, None, platform="darwin", run=FakeRunner())
        run = FakeRunner()
        autostart.disable(platform="darwin", run=run)
        assert ["launchctl", "bootout", f"gui/501/{autostart.LAUNCHD_LABEL}"] in (
            run.calls
        )
        assert not autostart.is_enabled("darwin")


class TestWindows:
    def test_on_adds_a_run_entry_and_starts_it_detached(self):
        winreg = FakeWinreg()
        started = []
        command = ["C:/py/pythonw.exe", "-m", "qsg_synthesizer", "--no-browser"]
        autostart._enable_windows(
            command, winreg=winreg, popen=lambda cmd, **kw: started.append((cmd, kw))
        )
        assert winreg.values[autostart.WINDOWS_VALUE_NAME] == (
            subprocess.list2cmdline(command)
        )
        assert started[0][0] == command
        assert autostart._windows_enabled(winreg)

    def test_off_removes_the_entry(self):
        winreg = FakeWinreg()
        winreg.values[autostart.WINDOWS_VALUE_NAME] = "x"
        assert "Removed" in autostart._disable_windows(winreg)
        assert not autostart._windows_enabled(winreg)
        assert autostart._disable_windows(winreg) == "Autostart wasn't on."


def test_unsupported_platform_is_an_error():
    with pytest.raises(autostart.AutostartError, match="isn't supported"):
        autostart.enable(8000, LOG, None, platform="aix")


class TestCommand:
    @pytest.fixture(autouse=True)
    def isolate(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # no .env here
        monkeypatch.setattr(cli, "default_log_file", lambda: tmp_path / "app.log")

    def test_on_passes_port_log_and_env_file(self, tmp_path, capsys):
        (tmp_path / ".env").write_text("GEMINI_API_KEY=\n")
        with patch.object(cli.autostart, "enable", return_value="Done.") as enable:
            cli.main(["--port", "8123", "autostart", "on"])
        enable.assert_called_once_with(
            8123, tmp_path / "app.log", (tmp_path / ".env").resolve()
        )
        assert "reads its settings from" in capsys.readouterr().out

    def test_status_reports_state_and_whether_it_answers(self, capsys, monkeypatch):
        monkeypatch.setattr(cli.autostart, "is_enabled", lambda: True)
        monkeypatch.setattr(cli, "_already_running", lambda url: False)
        cli.main(["autostart", "status"])
        assert "Autostart is on; the app is not running" in capsys.readouterr().out

    def test_errors_exit_with_the_message(self):
        error = cli.autostart.AutostartError("no systemd here")
        with patch.object(cli.autostart, "disable", side_effect=error):
            with pytest.raises(SystemExit, match="no systemd here"):
                cli.main(["autostart", "off"])


class TestOptions:
    @pytest.fixture(autouse=True)
    def isolate(self, monkeypatch):
        monkeypatch.setenv("QSG_BIND_HOST", "")
        monkeypatch.setattr(cli, "_port_is_free", lambda host, port: True)

    def test_env_file_is_loaded_instead_of_searching(self, tmp_path):
        env_file = tmp_path / "team.env"
        env_file.write_text("X=1\n")
        with (
            patch.object(cli, "load_dotenv") as load,
            patch("uvicorn.run"),
            patch.object(cli.threading, "Timer"),
        ):
            cli.main(["--env-file", str(env_file), "--no-browser"])
        load.assert_called_once_with(env_file.resolve())

    def test_log_file_captures_output(self, tmp_path, monkeypatch):
        log = tmp_path / "logs" / "app.log"
        monkeypatch.setattr(sys, "stdout", sys.stdout)
        monkeypatch.setattr(sys, "stderr", sys.stderr)
        with (
            patch.object(cli, "load_dotenv"),
            patch("uvicorn.run"),
            patch.object(cli.threading, "Timer"),
        ):
            cli.main(["--log-file", str(log), "--no-browser"])
            sys.stdout.flush()
        assert "History is saved in" in log.read_text()

    def test_big_log_is_rotated(self, tmp_path, monkeypatch):
        log = tmp_path / "app.log"
        log.write_bytes(b"x" * (cli._MAX_LOG_BYTES + 1))
        monkeypatch.setattr(sys, "stdout", sys.stdout)
        monkeypatch.setattr(sys, "stderr", sys.stderr)
        cli._redirect_output(log)
        sys.stdout.close()
        assert (tmp_path / "app.log.1").stat().st_size > cli._MAX_LOG_BYTES
