"""Packaging for local installs: dependency pins, bundled files, data paths
and the `qsg-synthesizer` command."""

import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from qsg_synthesizer import cli, paths

ROOT = Path(__file__).parent.parent


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_package_dependencies_match_requirements_txt():
    # pip-audit and Docker read requirements.txt; installs read pyproject.toml.
    requirements = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert _pyproject()["project"]["dependencies"] == requirements


def test_runtime_files_are_bundled_into_the_wheel():
    bundled = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]
    for source, target in bundled.items():
        assert (ROOT / source).is_file(), source
        assert target.startswith("qsg_synthesizer/_bundled/")
    targets = set(bundled.values())
    assert "qsg_synthesizer/_bundled/agents/qsg_extractor.md" in targets
    assert "qsg_synthesizer/_bundled/sample_docs/device_a_qsg.pdf" in targets
    assert "qsg_synthesizer/_bundled/sample_docs/device_b_qsg.pdf" in targets


class TestResourceDir:
    def test_prefers_the_repo_copy(self):
        assert paths.resource_dir("agents") == ROOT / ".claude" / "agents"

    def test_falls_back_to_the_bundled_copy(self, tmp_path, monkeypatch):
        monkeypatch.setitem(paths._SOURCE_RESOURCES, "agents", tmp_path / "gone")
        assert paths.resource_dir("agents") == paths.BUNDLED_DIR / "agents"


class TestDataDir:
    def test_source_checkout_keeps_repo_data(self):
        assert paths.is_source_checkout()
        assert paths.default_data_dir() == ROOT / "data"

    def test_installed_package_uses_the_user_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "SOURCE_ROOT", tmp_path)
        monkeypatch.setattr(paths, "is_source_checkout", lambda: False)
        assert paths.default_data_dir() == paths.user_data_dir()

    def test_linux_follows_xdg(self):
        home = Path("/home/u")
        assert paths.user_data_dir("linux", {}, home) == Path(
            "/home/u/.local/share/qsg-synthesizer"
        )
        assert paths.user_data_dir("linux", {"XDG_DATA_HOME": "/x"}, home) == Path(
            "/x/qsg-synthesizer"
        )

    def test_macos_uses_application_support(self):
        assert paths.user_data_dir("darwin", {}, Path("/Users/u")) == Path(
            "/Users/u/Library/Application Support/qsg-synthesizer"
        )

    def test_windows_uses_local_app_data(self):
        env = {"LOCALAPPDATA": "C:/Users/u/AppData/Local"}
        assert paths.user_data_dir("win32", env, Path("C:/Users/u")) == Path(
            "C:/Users/u/AppData/Local/qsg-synthesizer"
        )


class TestCli:
    @pytest.fixture(autouse=True)
    def isolate_environment(self, monkeypatch):
        # cli.main() loads ./.env, which in a dev checkout holds real API keys;
        # the suite must never pick those up (see tests/test_dotenv_guard.py).
        # It also sets QSG_BIND_HOST; monkeypatch removes it again afterwards.
        monkeypatch.setenv("QSG_BIND_HOST", "")
        with patch.object(cli, "load_dotenv") as load:
            yield load

    def test_starts_uvicorn_on_localhost_and_opens_the_browser(self, capsys):
        with (
            patch("uvicorn.run") as run,
            patch.object(cli.threading, "Timer") as timer,
        ):
            cli.main([])
        run.assert_called_once_with(
            "qsg_synthesizer.main:app", host="127.0.0.1", port=8000
        )
        assert timer.call_args.kwargs["args"] == ("http://127.0.0.1:8000/",)
        out = capsys.readouterr().out
        assert "http://127.0.0.1:8000/" in out
        assert "History is saved in" in out

    def test_tells_the_app_which_address_it_listens_on(self):
        with patch("uvicorn.run"), patch.object(cli.threading, "Timer"):
            cli.main(["--host", "127.0.0.1"])
        assert os.environ["QSG_BIND_HOST"] == "127.0.0.1"

    def test_no_browser_and_custom_port(self):
        with (
            patch("uvicorn.run") as run,
            patch.object(cli.threading, "Timer") as timer,
        ):
            cli.main(["--port", "9001", "--no-browser"])
        assert run.call_args.kwargs["port"] == 9001
        timer.assert_not_called()

    def test_all_interfaces_still_opens_localhost(self):
        with (
            patch("uvicorn.run") as run,
            patch.object(cli.threading, "Timer") as timer,
        ):
            cli.main(["--host", "0.0.0.0"])
        assert run.call_args.kwargs["host"] == "0.0.0.0"
        assert timer.call_args.kwargs["args"] == ("http://127.0.0.1:8000/",)
