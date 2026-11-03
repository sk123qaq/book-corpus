"""并发刷新状态时，先发起的慢请求不能盖掉后发起的新结果。"""

import importlib.util
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "ops"))

_spec = importlib.util.spec_from_file_location(
    "ops_server_status_test", REPO / "apps" / "ops" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


def test_slow_refresh_does_not_overwrite_newer():
    app = server.App.__new__(server.App)
    app.status_cache = (0.0, None)
    app.status_lock = threading.Lock()
    app.status_gen = 0

    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def fake_load(deep=False):
        calls.append(deep)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(2), "测试自身超时"
            return {"v": 1}
        return {"v": 2}

    app._load_status = fake_load
    out = {}

    def slow_call():
        out["slow"] = app.status(refresh=True)

    t = threading.Thread(target=slow_call)
    t.start()
    assert first_started.wait(2), "慢请求没启动"

    out["fast"] = app.status(refresh=True)
    release_first.set()
    t.join(2)

    assert not t.is_alive()
    assert out["slow"] == {"v": 1}
    assert out["fast"] == {"v": 2}
    assert app.status_cache[1] == {"v": 2}, "慢请求把新缓存盖掉了"
