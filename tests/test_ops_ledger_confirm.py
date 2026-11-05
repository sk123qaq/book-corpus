"""操作台「同意」：把备注里的「待复核」换成「分类确认 日期」，其余字一个不动。

复核状态没有字段，只住在备注文本里，所以这个动作只能改那三个字：
来源日期、图册不转之类的尾巴、分类与书名都得留着，没待复核的行必须跳过。
"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "src" / "bookcorpus" / "scripts"


def load_server():
    path = REPO / "apps" / "ops" / "server.py"
    spec = importlib.util.spec_from_file_location("ops_server_confirm", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ops_server_confirm"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ops(workspace):
    server = load_server()
    env = server.Env(str(workspace))
    env.prepare()
    # 只借 App 的方法，不起 runner 线程：__init__ 会接管并调度写任务
    app = server.App.__new__(server.App)
    app.env = env
    return app


def ledger(root):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import lib_common as LC

    return {r["uid"]: r for r in LC.categories(root=str(root))}


def set_note(root, uid, note):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import lib_common as LC

    row = next(r for r in LC.categories(root=str(root)) if r["uid"] == uid)
    row["备注"] = note
    LC.category_put(row, updated_by="test", root=str(root))


def test_confirm_rewrites_only_the_marker(ops, workspace):
    app = ops
    set_note(workspace, "B0001", "ingest:2026-11-04；待复核")
    set_note(workspace, "B0002", "ingest:2026-10-26；待复核；图册不转:复核判为不转-记图册")
    set_note(workspace, "B0003", "分类确认 2026-11-01")

    body = app.ledger_rows_confirm({"uids": ["B0001", "B0002", "B0003"]})

    today = time.strftime("%Y-%m-%d")
    assert body["confirmed"] == 2
    assert body["skipped"] == ["B0003"], "已经确认过的行不该被再写一次"
    rows = ledger(workspace)
    assert rows["B0001"]["备注"] == "ingest:2026-11-04；分类确认 " + today
    assert rows["B0002"]["备注"] == ("ingest:2026-10-26；分类确认 " + today + "；图册不转:复核判为不转-记图册"), (
        "尾巴被吃了"
    )
    assert rows["B0003"]["备注"] == "分类确认 2026-11-01"
    assert rows["B0001"]["一级"] == "文学", "分类被改了"
    assert rows["B0001"]["正式书名"] == "甲书"


def test_sample_note_drops_its_own_date(ops, workspace):
    app = ops
    set_note(workspace, "B0001", "sample:待复核 2026-11-03")

    body = app.ledger_rows_confirm({"uids": ["B0001"]})

    today = time.strftime("%Y-%m-%d")
    assert body["confirmed"] == 1
    assert ledger(workspace)["B0001"]["备注"] == "sample:分类确认 " + today, "两个日期叠在一起"


def test_unknown_uid_is_skipped_not_fatal(ops, workspace):
    app = ops
    set_note(workspace, "B0001", "ingest:2026-11-04；待复核")
    body = app.ledger_rows_confirm({"uids": ["B0001", "B0999"]})
    assert body["confirmed"] == 1
    assert body["skipped"] == ["B0999"]


def test_rejects_bad_uids_and_empty(ops, workspace):
    app = ops
    with pytest.raises(ValueError):
        app.ledger_rows_confirm({"uids": []})
    with pytest.raises(ValueError):
        app.ledger_rows_confirm({"uids": ["../../etc/passwd"]})
