import csv
import importlib.util
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1]
          / "src" / "bookcorpus" / "scripts" / "check_p1.py")


def _load_check_p1():
    spec = importlib.util.spec_from_file_location("check_p1_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ws(tmp_path):
    root = tmp_path / "ws"
    (root / "books" / "B0001" / "md").mkdir(parents=True)
    (root / "books" / "B0001" / "md" / "x.md").write_text("x", encoding="utf-8")
    (root / "工作区" / "批次").mkdir(parents=True)
    return root


def test_find_gold_takes_latest_batch(tmp_path, monkeypatch):
    """默认按最近一个 qa-检索-* 批次取 gold，不再写死 2026-10-19。"""
    root = _ws(tmp_path)
    for day in ("2026-10-19", "2026-11-05"):
        batch = root / "工作区" / "批次" / ("qa-检索-" + day)
        batch.mkdir()
        (batch / "gold.tsv").write_text("qid\tpath\n", encoding="utf-8")
    mod = _load_check_p1()
    monkeypatch.setattr(mod, "BATCH_ROOT", str(root / "工作区" / "批次"))
    got, why = mod.find_gold()
    assert got.endswith("qa-检索-2026-11-05/gold.tsv")
    assert "2026-11-05" in why

    explicit = root / "其它" / "g.tsv"
    explicit.parent.mkdir()
    explicit.write_text("qid\tpath\n", encoding="utf-8")
    assert mod.find_gold(str(explicit)) == (str(explicit), "--gold")


def test_gold_rows_outside_library_fail(tmp_path, monkeypatch):
    """题集指的正文不在库里就算失败，不再静默 SKIP。"""
    root = _ws(tmp_path)
    gold = root / "gold.tsv"
    gold.write_text(
        "qid\tcategory\tpath\n"
        "q1\t原词\tbooks/B0001/md/x.md\n"
        "q2\t原词\tbooks/B9999/md/y.md\n",
        encoding="utf-8")
    mod = _load_check_p1()
    monkeypatch.setattr(mod, "ROOT", str(root))
    mod.fails.clear()
    rows, mapped = mod.load_gold(str(gold))
    assert len(rows) == 2 and len(mapped) == 1
    assert mod.fails == ["gold q2"]


def test_no_gold_exits_2(tmp_path, monkeypatch):
    """没有题集 = 没跑：退出码 2，不是 0。"""
    root = _ws(tmp_path)
    mod = _load_check_p1()
    monkeypatch.setattr(mod, "ROOT", str(root))
    monkeypatch.setattr(mod, "BATCH_ROOT", str(root / "工作区" / "批次"))
    monkeypatch.delenv("BOOKS_P1_GOLD", raising=False)
    assert mod.main([]) == 2


def test_empty_gold_exits_2(tmp_path, monkeypatch):
    """题集只有表头 = 没有可验的行：退出码 2。"""
    root = _ws(tmp_path)
    batch = root / "工作区" / "批次" / "qa-检索-2026-11-05"
    batch.mkdir()
    (batch / "gold.tsv").write_text("qid\tcategory\tpath\n", encoding="utf-8")
    mod = _load_check_p1()
    monkeypatch.setattr(mod, "ROOT", str(root))
    monkeypatch.setattr(mod, "BATCH_ROOT", str(root / "工作区" / "批次"))
    monkeypatch.delenv("BOOKS_P1_GOLD", raising=False)
    assert mod.main([]) == 2
