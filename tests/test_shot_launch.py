"""tests/manual/demo/shot.py 的启动重试：Chrome 起不来与页面错误分开，失败带诊断。

不起真 Chrome：Popen 被替身接住，只验重试次数、退避、报错分类与清理。
"""

import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "manual" / "demo"))

import shot


class _FakeProc:
    def __init__(self, rc=None):
        self.rc = rc
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated += 1
        if self.rc is None:
            self.rc = -15

    def kill(self):
        self.killed += 1
        self.rc = -9

    def wait(self, timeout=None):
        return self.rc


def _profile_of(cmd):
    return next(a for a in cmd if a.startswith("--user-data-dir=")).split("=", 1)[1]


def _patch_popen(monkeypatch, factory):
    calls = []

    def fake_popen(cmd, **kwargs):
        proc = factory(len(calls))
        calls.append((cmd, proc))
        return proc

    monkeypatch.setattr(shot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(shot, "_backoff", lambda attempt: None)
    return calls


def test_launch_retries_when_chrome_exits_immediately(monkeypatch):
    # Chrome 起来就退（启动崩、非法 flag、/dev/shm 不够）：三次尝试、立刻报、不空等 30s。
    calls = _patch_popen(monkeypatch, lambda n: _FakeProc(rc=1))
    started = time.time()
    with pytest.raises(shot.ChromeLaunchError) as excinfo:
        shot.launch(800, 600, "about:blank", 0, attempts=3, timeout=3.0)
    elapsed = time.time() - started

    assert len(calls) == 3
    message = str(excinfo.value)
    assert "Chrome 没起来" in message
    assert "attempt 3/3" in message
    assert "rc=1" in message
    assert elapsed < 3.0, f"进程已退出时不该等满 timeout，实际 {elapsed:.2f}s"
    for cmd, _proc in calls:
        assert not Path(_profile_of(cmd)).exists(), "失败尝试的 profile 要清掉"


def test_launch_reports_missing_debug_port_after_retries(monkeypatch):
    # 进程活着但永远不写 DevToolsActivePort：两次尝试后报「调试口从未打开」，每个 Chrome 都收掉。
    calls = _patch_popen(monkeypatch, lambda n: _FakeProc(rc=None))
    started = time.time()
    with pytest.raises(shot.ChromeLaunchError) as excinfo:
        shot.launch(800, 600, "about:blank", 0, attempts=2, timeout=0.4)
    elapsed = time.time() - started

    assert len(calls) == 2
    assert "DevToolsActivePort" in str(excinfo.value)
    assert all(proc.terminated == 1 for _cmd, proc in calls)
    assert elapsed < 3.0, f"per-attempt 预算没生效，实际 {elapsed:.2f}s"


def test_launch_stops_retrying_once_an_attempt_succeeds(monkeypatch):
    calls = []
    proc = _FakeProc(rc=None)

    def fake_launch_once(width, height, url, port, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise shot.ChromeLaunchError("stub: 调试口没开")
        return proc, "stub-profile", "ws://127.0.0.1:1/devtools/page/stub"

    monkeypatch.setattr(shot, "_launch_once", fake_launch_once)
    monkeypatch.setattr(shot, "_backoff", lambda attempt: None)
    got_proc, got_profile, got_ws = shot.launch(800, 600, "about:blank", 0, attempts=3, timeout=3.0)

    assert (got_proc, got_profile) == (proc, "stub-profile")
    assert got_ws.startswith("ws://")
    assert len(calls) == 2
    assert calls == [1.0, 1.0], "timeout 应按尝试次数均分，健康路径不变慢"


def test_launch_does_not_swallow_page_or_assertion_errors(monkeypatch):
    # 只有 ChromeLaunchError 才重试；页面/断言类错误原样冒出，不被包装成「Chrome 没起来」。
    def boom(*_args, **_kwargs):
        raise AssertionError("页面坏了")

    monkeypatch.setattr(shot, "_launch_once", boom)
    with pytest.raises(AssertionError, match="页面坏了"):
        shot.launch(800, 600, "about:blank", 0, attempts=3, timeout=3.0)


def test_connect_cdp_retries_handshake_then_reports_launch_error(monkeypatch):
    attempts = []

    def bad_ws(_url, timeout=None):
        attempts.append(timeout)
        raise OSError("handshake failed")

    monkeypatch.setattr(shot, "WS", bad_ws)
    monkeypatch.setattr(shot, "_backoff", lambda attempt: None)
    with pytest.raises(shot.ChromeLaunchError) as excinfo:
        shot.connect_cdp("ws://127.0.0.1:1/devtools/page/stub", attempts=2, handshake_timeout=1.0)

    assert len(attempts) == 2
    assert "连不上页面 WS" in str(excinfo.value)
    assert "handshake failed" in str(excinfo.value)
