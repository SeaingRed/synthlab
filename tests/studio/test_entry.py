import tomllib
from pathlib import Path

import pytest


def test_parse_args_default_data_dir_is_none():
    from synthlab.studio.server import _parse_args

    assert _parse_args([]).data_dir is None


def test_parse_args_data_dir_flag():
    from synthlab.studio.server import _parse_args

    assert _parse_args(["--data-dir", "scratch_data"]).data_dir == Path("scratch_data")


def test_script_entry_points_to_callable():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    script = data["project"]["scripts"]["synthlab-studio"]
    assert script == "synthlab.studio.server:main"
    from synthlab.studio.server import main

    assert callable(main)


def test_parse_args_host_port_share_flags():
    from synthlab.studio.server import _parse_args

    args = _parse_args(["--port", "9000", "--host", "0.0.0.0", "--share"])
    assert args.port == 9000
    assert args.host == "0.0.0.0"
    assert args.share is True


def test_parse_args_host_port_share_defaults():
    from synthlab.studio.server import _parse_args

    args = _parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8420
    assert args.share is False


def test_default_data_dir_resolved_at_create_app_not_import(tmp_path, monkeypatch):
    """DEFAULT_DATA_DIR used to be computed at import time from Path.cwd(),
    so it froze whatever directory happened to be current when the module
    was first imported — usually the test runner's, not the caller's.
    `default_data_dir()` must be called fresh inside `create_app`."""
    monkeypatch.chdir(tmp_path)
    from synthlab.studio.server import create_app

    app = create_app()
    assert app.state.data_dir == tmp_path / "studio_data"


@pytest.mark.parametrize(
    "argv",
    [
        ["--host", "0.0.0.0"],
        ["--host", "192.168.1.5"],
        ["--host", "::"],
        ["--host", "my-laptop"],
        ["--share"],
        ["--share", "--host", "127.0.0.1"],
    ],
)
def test_anything_but_a_loopback_bind_gets_a_token(argv):
    """`--host 0.0.0.0` without --share used to start with no token: the
    Host allowlist was the only gate, and the client writes that header."""
    from synthlab.studio.server import _parse_args, _share_token

    token = _share_token(_parse_args(argv))
    assert token and len(token) >= 16
    assert token != _share_token(_parse_args(argv))  # drawn per launch


@pytest.mark.parametrize(
    "argv", [[], ["--host", "127.0.0.1"], ["--host", "localhost"], ["--host", "::1"]]
)
def test_a_loopback_bind_stays_token_free(argv):
    from synthlab.studio.server import _parse_args, _share_token, _urls

    args = _parse_args([*argv, "--port", "9000"])
    assert _share_token(args) is None
    assert _urls(args, None) == ("http://127.0.0.1:9000", None)


def test_a_token_launch_prints_the_link_for_the_address_it_binds(monkeypatch):
    from synthlab.studio import server

    monkeypatch.setattr(server, "_lan_ip", lambda: "192.168.1.5")
    here, there = server._urls(server._parse_args(["--host", "0.0.0.0"]), "tok")
    assert here == "http://127.0.0.1:8420/?token=tok"
    assert there == "http://192.168.1.5:8420/?token=tok"
    # one address answers only there: this machine's browser opens it too
    args = server._parse_args(["--host", "10.0.0.7", "--port", "9000"])
    assert server._urls(args, "tok") == ("http://10.0.0.7:9000/?token=tok",) * 2


def test_main_serves_a_non_loopback_bind_behind_its_token(monkeypatch, capsys):
    """The launch itself, with uvicorn and the browser stubbed out."""
    import threading

    import uvicorn

    from synthlab.studio import server

    class NoTimer:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    seen = {}
    monkeypatch.setattr("sys.argv", ["synthlab-studio", "--host", "0.0.0.0"])
    monkeypatch.setattr(server, "_lan_ip", lambda: "192.168.1.5")
    monkeypatch.setattr(threading, "Timer", NoTimer)
    monkeypatch.setattr(
        server,
        "create_app",
        lambda data_dir, share_token=None: seen.update(token=share_token),
    )

    class NoServer:
        def run(self):
            pass

    # main() builds its server through make_server (it ends the /events
    # streams on Ctrl+C); uvicorn itself is made unusable, so a change to
    # the launch can never bind a real port from this test
    monkeypatch.setattr(
        server, "make_server", lambda app, **kw: seen.update(kw) or NoServer()
    )
    monkeypatch.setattr(uvicorn, "run", None)
    monkeypatch.setattr(uvicorn, "Server", None)
    server.main()
    assert seen["host"] == "0.0.0.0" and seen["token"]
    out = capsys.readouterr().out
    assert f"http://192.168.1.5:8420/?token={seen['token']}" in out
