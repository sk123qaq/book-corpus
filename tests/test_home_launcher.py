"""首页按需代起：命令拼装、端口探测与不启动分支。"""

import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "home"))

from launcher import (
    SERVICES,
    command_for,
    ensure,
    is_local_target,
    port_open,
    same_target,
    wait_for_port,
)


def test_command_for_demo_sets_books_root(tmp_path):
    root = tmp_path / "书库"
    command, env = command_for("search", root)

    assert command[0] == sys.executable
    assert command[1].endswith("apps/demo/server.py")
    assert command[-2:] == ["--port", "8765"]
    assert env["BOOKS_ROOT"] == str(root)


def test_command_for_reading_uses_root_argument(tmp_path):
    root = tmp_path / "书库"
    command, env = command_for("reading", root)

    assert command[1].endswith("apps/reading/server.py")
    assert command[-2:] == ["--root", str(root)]
    assert "BOOKS_ROOT" not in env


def test_ensure_already_running_does_not_spawn(tmp_path):
    result = ensure("search", tmp_path, probe=lambda port: True)

    assert result["state"] == "already"
    assert result["service"] is SERVICES["search"]


def test_ensure_disabled_when_no_start(tmp_path):
    result = ensure(
        "daily",
        tmp_path,
        allow_start=False,
        probe=lambda port: False,
    )

    assert result["state"] == "disabled"
    assert result["service"] is SERVICES["daily"]


def test_ensure_unknown_key(tmp_path):
    with pytest.raises(KeyError):
        ensure("nope", tmp_path)


def test_port_open_and_wait_for_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        assert port_open(port)
        assert wait_for_port(port, 0.5)


def test_wait_for_port_times_out():
    probe_calls = []

    def probe(port):
        probe_calls.append(port)
        return False

    assert not wait_for_port(65000, 0.2, probe=probe)
    assert probe_calls


def test_local_target_only_matches_service_port():
    assert is_local_target("search", "http://127.0.0.1:8765")
    assert is_local_target("search", "http://localhost:8765/")
    assert not is_local_target("search", "http://127.0.0.1:9999")
    assert not is_local_target("search", "https://example.com:8765")
    assert not is_local_target("nope", "http://127.0.0.1:8765")


def test_same_target_ignores_trailing_slash():
    assert same_target("http://127.0.0.1:8765", "http://127.0.0.1:8765/")
    assert not same_target("http://127.0.0.1:8765", "http://127.0.0.1:9999")
