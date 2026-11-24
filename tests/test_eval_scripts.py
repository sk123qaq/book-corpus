"""eval_retrieval.py 与 eval_report.py 的回归测试。"""

import csv
import sys
from pathlib import Path

import pytest

from bookcorpus.scripts import eval_report, eval_retrieval


def _write_gold(path: Path, rows: list[dict[str, str]]) -> None:
    cols = ["qid", "category", "question", "terms", "path"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_eval_retrieval_custom_k_dedup_sorted_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gold = tmp_path / "gold.tsv"
    _write_gold(
        gold,
        [
            {"qid": "Q1", "category": "原词", "question": "q1", "terms": "a", "path": "books/B1/md/01.md"},
            {"qid": "Q2", "category": "无原词", "question": "q2", "terms": "b", "path": "books/B2/md/25.md"},
        ],
    )
    out = tmp_path / "eval.tsv"

    def fake_grep(terms: list[str]) -> list[str]:
        if terms == ["a"]:
            return [f"books/B1/md/{i:02d}.md" for i in range(1, 10)]  # rank 1
        return [f"books/B2/md/{i:02d}.md" for i in range(1, 30)]  # rank 25

    monkeypatch.setattr(eval_retrieval, "grep_files", fake_grep)
    eval_retrieval.main(["--gold", str(gold), "--system", "A", "--k", "50,10,5,10", "--out", str(out), "--jobs", "1"])

    detail_lines = out.read_text(encoding="utf-8").splitlines()
    assert detail_lines[0] == "qid\tsystem\trank\tpos\thit5\thit10\thit50"
    assert detail_lines[1] == "Q1\tA\t1\t1\t1\t1\t1"
    assert detail_lines[2] == "Q2\tA\t25\t25\t0\t0\t1"

    summary_path = tmp_path / "eval-summary.tsv"
    summary_lines = summary_path.read_text(encoding="utf-8").splitlines()
    assert summary_lines[0] == "system\trecall@5\trecall@10\trecall@50\tmrr\tno_term_hit50\tn\tsecs"
    fields = summary_lines[1].split("\t")
    assert fields[:6] == ["A", "0.500", "0.500", "1.000", "0.520", "1.000"]
    assert fields[6] == "2"


def test_eval_retrieval_default_k_backwards_compatible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gold = tmp_path / "gold.tsv"
    _write_gold(
        gold,
        [
            {"qid": "Q1", "category": "原词", "question": "q1", "terms": "a", "path": "books/B1/md/01.md"},
        ],
    )
    out = tmp_path / "baseline.tsv"
    monkeypatch.setattr(eval_retrieval, "grep_files", lambda _terms: ["books/B1/md/01.md"])

    eval_retrieval.main(["--gold", str(gold), "--system", "A", "--out", str(out), "--jobs", "1"])

    detail_lines = out.read_text(encoding="utf-8").splitlines()
    assert detail_lines[0] == "qid\tsystem\trank\tpos\thit10\thit20"
    summary_lines = (tmp_path / "baseline-summary.tsv").read_text(encoding="utf-8").splitlines()
    assert summary_lines[0] == "system\trecall@10\trecall@20\tmrr\tno_term_hit20\tn\tsecs"
    assert summary_lines[1].startswith("A\t1.000\t1.000\t1.000\tnan\t1\t")


@pytest.mark.parametrize("bad_k", ["", " ", "0", "-5,10", "abc", "10,,20"])
def test_eval_retrieval_invalid_k_exits(tmp_path: Path, bad_k: str) -> None:
    gold = tmp_path / "gold.tsv"
    _write_gold(gold, [])
    out = tmp_path / "eval.tsv"
    with pytest.raises(SystemExit) as exc:
        eval_retrieval.main(["--gold", str(gold), "--system", "A", "--k", bad_k, "--out", str(out), "--jobs", "1"])
    assert exc.value.code != 0


def test_eval_retrieval_empty_gold_outputs_nan(tmp_path: Path) -> None:
    gold = tmp_path / "gold.tsv"
    _write_gold(gold, [])
    out = tmp_path / "empty.tsv"

    eval_retrieval.main(["--gold", str(gold), "--system", "A", "--k", "5,15", "--out", str(out), "--jobs", "1"])

    detail_lines = out.read_text(encoding="utf-8").splitlines()
    assert detail_lines == ["qid\tsystem\trank\tpos\thit5\thit15"]
    summary_lines = (tmp_path / "empty-summary.tsv").read_text(encoding="utf-8").splitlines()
    assert summary_lines[0] == "system\trecall@5\trecall@15\tmrr\tno_term_hit15\tn\tsecs"
    fields = summary_lines[1].split("\t")
    assert fields[:6] == ["A", "nan", "nan", "nan", "nan", "0"]


def test_eval_report_supports_ijk_unknown_systems_and_no_literal_category(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gold = tmp_path / "gold.tsv"
    _write_gold(
        gold,
        [
            {"qid": "Q1", "category": "近义表达", "question": "q1", "terms": "a", "path": "books/B1/md/01.md"},
            {"qid": "Q2", "category": "无原词", "question": "q2", "terms": "b", "path": "books/B2/md/01.md"},
        ],
    )
    eval_tsv = tmp_path / "eval-all.tsv"
    rows = []
    for sysname, rank in [("H", 1), ("I", 2), ("J", 5), ("K1", 12), ("K2", 25), ("Z_CUSTOM", 3), ("A_EXTRA", 8)]:
        rows.append(f"Q1\t{sysname}\t{rank}\t{rank}\t{int(rank <= 10)}\t{int(rank <= 20)}")
        rows.append(f"Q2\t{sysname}\t99\t\t0\t0")
    eval_tsv.write_text("qid\tsystem\trank\tpos\thit10\thit20\n" + "\n".join(rows) + "\n", encoding="utf-8")

    eval_report.main(["--batch", str(tmp_path)])
    out = capsys.readouterr().out

    assert "伪查询档" not in out
    assert "H E + 启发重排（--rerank）" in out
    assert "I G + 启发重排（--expand --rerank）" in out
    assert "J E + 伪查询扩展档（queries）" in out
    assert "K1 查询侧扩展（章级 base=1）" in out
    assert "K2 查询侧扩展（章级 base=2）" in out
    assert "| A_EXTRA |" in out
    assert "| Z_CUSTOM |" in out

    # 未知系统按字母序追加在预设档位之后，且无原词题时不崩溃（原词 MRR 输出 -）
    table1_rows = [line for line in out.splitlines() if line.startswith("| ") and "MRR" not in line]
    half = len(table1_rows) // 2
    first_table = table1_rows[:half]
    second_table = table1_rows[half:]
    assert [r.split("|")[1].strip().split()[0] for r in first_table] == [
        "H",
        "I",
        "J",
        "K1",
        "K2",
        "A_EXTRA",
        "Z_CUSTOM",
    ]
    assert all(line.rstrip().endswith("| - |") for line in second_table)


def test_book_query_cli_import_does_not_circular_import() -> None:
    import subprocess

    for stmt in (
        "from bookcorpus.scripts import book_query, book_cards; assert hasattr(book_cards, 'DEFAULT_CARDS')",
        "from bookcorpus.scripts import book_cards, book_query; assert hasattr(book_query, 'main')",
        "import sys; sys.path.insert(0, 'src/bookcorpus/scripts'); import book_query, book_cards",
        "import sys; sys.path.insert(0, 'src/bookcorpus/scripts'); import book_cards, book_query",
    ):
        proc = subprocess.run([sys.executable, "-c", stmt], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr
