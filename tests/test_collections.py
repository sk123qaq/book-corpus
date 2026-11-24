import json
import sqlite3

from conftest import run_cli, write_book, write_collection, write_ledger

SHARED = "合集甲乙丙（套装三册）.epub"


def _two_books_in_one_collection(root):
    write_collection(root, "C0001", SHARED)
    rows = []
    for uid, title in (("B0101", "甲卷"), ("B0102", "乙卷")):
        write_book(root, uid, "文学", title, ["正文一段。"], collection="C0001", collection_file=SHARED, sha="a" * 64)
        rows.append((uid, "文学", title, ""))
    write_ledger(root, rows)


def test_shared_source_points_at_collection(workspace):
    _two_books_in_one_collection(workspace)
    r = run_cli("--root", workspace, "library")
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(str(workspace / "build" / "library.db"))
    rows = con.execute("select uid, file, primary_src from source where uid like 'B010%' order by uid").fetchall()
    assert rows == [
        ("B0101", "collections/C0001/source/" + SHARED, 1),
        ("B0102", "collections/C0001/source/" + SHARED, 1),
    ]
    assert con.execute("select count(*) from v_dup_sha").fetchone()[0] == 1
    con.close()


def test_book_json_keeps_collection_not_path(workspace):
    _two_books_in_one_collection(workspace)
    assert run_cli("--root", workspace, "library").returncode == 0
    r = run_cli("--root", workspace, "library", "--emit-json")
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads((workspace / "books" / "B0101" / "book.json").read_text(encoding="utf-8"))
    assert doc["sources"][0]["collection"] == "C0001"
    assert doc["sources"][0]["file"] == SHARED
    assert "path" not in doc, "v2 的 book.json 不再记 path，目录名就是 uid"


def test_install_collection_installs_split_output_and_backs_up_old_md(workspace):
    _two_books_in_one_collection(workspace)
    assert run_cli("--root", workspace, "library").returncode == 0

    batch = workspace / "工作区" / "批次" / "split-2026-11-24"
    split_out = batch / "collections" / "demo" / "out"
    for folder, body in (("vol1", "# 第一章\n\n重拆甲卷正文。\n"), ("vol2", "# 第一章\n\n重拆乙卷正文。\n")):
        vdir = split_out / folder
        vdir.mkdir(parents=True)
        (vdir / "01.md").write_text(body, encoding="utf-8")
        (vdir / "README.md").write_text("# 目录\n", encoding="utf-8")

    map_path = batch / "collections" / "demo.json"
    map_path.write_text(
        json.dumps(
            {
                "collection": "C0001",
                "books": [
                    {"uid": "B0101", "folder": "vol1"},
                    {"uid": "B0102", "folder": "vol2"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    r = run_cli(
        "--root",
        workspace,
        "run",
        "install_collection",
        "--batch",
        "split-2026-11-24",
        "--map",
        str(map_path),
        "--split-out",
        str(split_out),
    )
    assert r.returncode == 0, r.stdout + r.stderr

    for uid, snippet in (("B0101", "重拆甲卷正文"), ("B0102", "重拆乙卷正文")):
        md_dir = workspace / "books" / uid / "md"
        assert snippet in (md_dir / "01.md").read_text(encoding="utf-8")
        manifest = json.loads((md_dir / ".manifest.json").read_text(encoding="utf-8"))
        assert manifest["uid"] == uid
        assert manifest["method"] == "split_collection"
        assert manifest["source_sha256"] == "a" * 64
        assert list((batch / "out").glob(f"{uid}.prev-*"))

    for folder in ("vol1", "vol2"):
        vdir = split_out / folder
        vdir.mkdir(parents=True)
        (vdir / "01.md").write_text("# 再次跑\n", encoding="utf-8")

    again = run_cli(
        "--root",
        workspace,
        "run",
        "install_collection",
        "--batch",
        "split-2026-11-24",
        "--map",
        str(map_path),
        "--split-out",
        str(split_out),
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert "skip B0101" in again.stdout
