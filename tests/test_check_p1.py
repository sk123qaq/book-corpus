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


def test_gold_path_map_from_duizhaobiao(tmp_path):
    """合成 gold（2 条 库/ 路径）+ 对照表，锁 库/ -> books/<uid> 映射。"""
    root = tmp_path / "ws"
    (root / "tables").mkdir(parents=True)
    for uid, name in (("B0001", "甲"), ("B0002", "乙")):
        md = root / "books" / uid / "md"
        md.mkdir(parents=True)
        (md / "x.md").write_text("x", encoding="utf-8")

    rows = [
        ["序号", "阶段", "uid", "类别", "旧路径", "新路径", "备注"],
        ["1", "3", "B0001", "md", "书md/甲", "库/文学/甲/md", ""],
        ["2", "3", "B0002", "md", "书md/乙", "库/文学/乙/md", ""],
    ]
    with open(root / "tables" / "对照表.csv", "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)

    gold = root / "gold.tsv"
    gold.write_text(
        "qid\tcategory\tpath\n"
        "q1\t原词\t库/文学/甲/md/x.md\n"
        "q2\t原词\t库/文学/乙/md/x.md\n"
        "q3\t原词\t库/文学/丙/md/z.md\n",
        encoding="utf-8")

    mod = _load_check_p1()
    table = mod.read_path_map(root)
    assert table["库/文学/甲/md"] == "B0001"
    assert table["库/文学/乙/md"] == "B0002"

    with open(gold, encoding="utf-8") as fh:
        got = [(r["qid"], mod.map_gold_path(r["path"], table))
               for r in csv.DictReader(fh, delimiter="\t")]
    assert got[0][1] == ("books/B0001/md/x.md", "")
    assert got[1][1] == ("books/B0002/md/x.md", "")
    assert got[2][1][0] is None
    assert "无匹配" in got[2][1][1]
    assert mod.map_gold_path("books/B0001/md/x.md", table) == ("books/B0001/md/x.md", "")
