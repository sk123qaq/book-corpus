"""运行记录的最小验收：一次运行从 start 到 finish 可读、可包、可回放。"""

import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "demo"))

import runlog


def test_run_lifecycle(tmp_path):
    runlog.configure(str(tmp_path))
    rec = runlog.start("turn", session="abcdefabcdef", turn=2, question="问", thinking="high")
    assert rec.enabled
    assert rec.id
    rec.event("turn.request", question="问")
    info = rec.blob("大文本" * 1000)
    assert info["path"].startswith("blobs/")
    rec.large("大文本" * 1000, "body")
    rec.set_index(cards=3)
    manifest = rec.finish("ok")
    assert manifest["status"] == "ok"
    assert manifest["cards"] == 3
    assert manifest["events"] == 3
    events = runlog.read_run(rec.id)["events"]
    assert events[-1]["type"] == "run.end"
    assert manifest["counts"]["run.end"] == 1
    assert rec.finish("ok")["status"] == "ok"

    rows = runlog.list_runs(session="abcdefabcdef")
    assert len(rows) == 1
    assert runlog.timeline(rec.id)["id"] == rec.id
    assert runlog.doctor() == 0

    out = Path(runlog.bundle("abcdefabcdef"))
    assert out.is_file()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert any(name.endswith("run.json") for name in names)
    assert any(name.endswith("run.jsonl") for name in names)


def test_disabled_recorder_writes_nothing(tmp_path):
    runlog.configure(str(tmp_path))
    rec = runlog.Recorder("", "", {}, enabled=False)
    rec.event("anything", value=1)
    assert rec.blob("x")["path"] == ""
    manifest = rec.finish("ok")
    assert manifest["id"] == ""
    assert manifest["status"] == "ok"
    assert not list(tmp_path.glob("runs/*"))


def test_service_event_and_cache_lookup(tmp_path):
    runlog.configure(str(tmp_path))
    runlog.service_event("chapter", ok=True, path="books/B1/md/a.md", line=3)
    assert (tmp_path / "service.jsonl").is_file()

    rec = runlog.start("answer", session="abcdefabcdef", turn=1)
    rec.event("cache.write", key="deadbeef", generated_ms=12.5)
    rec.finish("ok")
    found = runlog.find_by_cache_key("deadbeef")
    assert found
    assert found["run"] == rec.id


def test_replay_rebuilds_prompt(tmp_path):
    import answer

    runlog.configure(str(tmp_path))
    rec = runlog.start("answer", session="abcdefabcdef", turn=1, question="问")
    window = {
        "path": "books/B1/md/a.md",
        "book": "B1",
        "title": "章",
        "start": 1,
        "end": 2,
        "chars": 2,
        "text": "正文",
    }
    answer.build_prompt("问", "B1", [window], [], rec=rec)
    rec.finish("ok")
    assert runlog.replay(rec.id) == 0
