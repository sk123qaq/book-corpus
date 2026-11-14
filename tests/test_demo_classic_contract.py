"""经典页的检索请求不建会话，避免留下永远 pending 的隐藏会话。"""

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "apps" / "demo" / "web" / "app.js"


def test_classic_run_opts_out_of_session_creation():
    src = APP.read_text(encoding="utf-8")
    start = src.index("const trace = await fetchJSON('/api/run'")
    end = src.index("});", start)
    body = src[start:end]
    assert "stateless: true" in body
    assert "session_id" not in body
