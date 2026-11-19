"""阶段 1 底层增量引擎与隔离闭环回归测试。

覆盖：
- build_para_index.py: --sync（含老库无 indexed_book 无痛迁移）、--books、--delete、--dry-run、冷宫隔离
- embed_corpus.py: deactivate_books、prune_orphans、diff_embed、compact 零推理压缩、--sync --allow-missing-engine
- build_library.py: --emit-json 差量写回 book.json（未变文件不改 mtime）
- retire_book.py: 退库预览含 embed_chunks、退库同步清理 FTS5 与失活向量块
"""

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from conftest import run_cli, write_book, write_ledger

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "src" / "bookcorpus" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bpi = _load("bpi_stage1_test", "build_para_index.py")
ec = _load("ec_stage1_test", "embed_corpus.py")
cjk = _load("cjk_stage1_test", "lib_fts_cjk.py")


def _fts_books(db_path: Path, query: str) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        tok = cjk.index_text(query)
        rows = con.execute(
            "SELECT DISTINCT p.book FROM para_fts f JOIN para p ON p.id=f.rowid WHERE para_fts MATCH ?",
            (f'"{tok}"',),
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def test_para_index_sync_legacy_bootstrap_and_cold_isolation(workspace):
    # 在冷宫放一个带 md/ 的书，确认绝不会被扫进 index.db
    cold_md = workspace / "books" / "_冷宫" / "冷宫废书" / "md"
    cold_md.mkdir(parents=True, exist_ok=True)
    (cold_md / "001_冷宫章.md").write_text("# 冷宫章\n\n冷宫专属保密词汇绝不入库。\n", encoding="utf-8")

    db = workspace / "build" / "检索" / "index.db"
    con = bpi.build(str(db), root=str(workspace))
    con.close()

    assert _fts_books(db, "冷宫专属") == set()
    assert _fts_books(db, "科层制") == {"B0001"}
    assert _fts_books(db, "差序格局") == {"B0002"}

    # 模拟老库：删掉 indexed_book 表，验证无痛基线迁移 + 增量同步
    con = sqlite3.connect(str(db))
    con.execute("DROP TABLE indexed_book")
    con.commit()
    con.close()

    # 新增 B0004、修改 B0001、删除 B0002 的 md/
    write_book(workspace, "B0004", "科技", "新书四", ["量子纠缠与贝尔不等式在现代物理实验中的验证。"])
    b1_md = workspace / "books" / "B0001" / "md" / "001_第1节.md"
    b1_md.write_text("# 第1节\n\n全新的修订正文引入了边际效用递减定律。\n", encoding="utf-8")
    os.utime(b1_md, (time.time() + 10, time.time() + 10))
    shutil.rmtree(workspace / "books" / "B0002" / "md")

    # --dry-run 只读探测不写库
    delta = bpi.diff_index(str(db), root=str(workspace))
    assert delta["has_db"] is True
    assert delta["has_tracking"] is False
    assert delta["added"] == ["B0004"]
    assert delta["removed"] == ["B0002"]

    # 先跑一次 --sync 完成老库基线建表并处理增删；再修改 B0001 确认 sig 变化触发增量更新
    res = bpi.sync(str(db), root=str(workspace))
    assert res["added"] == 1
    assert res["removed"] == 1
    assert _fts_books(db, "量子纠缠") == {"B0004"}
    assert _fts_books(db, "差序格局") == set()

    # 修改 B0001（现在已有 indexed_book 追踪 sig）
    b1_md.write_text("# 第1节\n\n第二次改写后的比较优势原理。\n", encoding="utf-8")
    os.utime(b1_md, (time.time() + 20, time.time() + 20))
    res2 = bpi.sync(str(db), root=str(workspace))
    assert res2["modified"] == 1
    assert _fts_books(db, "比较优势") == {"B0001"}
    assert _fts_books(db, "科层制") == set()

    # 校验 meta 与实际 para 表一致（匹配 verify_phase 箱子 4 门禁）
    con = sqlite3.connect(str(db))
    try:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        npara = con.execute("SELECT count(*) FROM para").fetchone()[0]
        nchap = con.execute("SELECT count(DISTINCT path) FROM para").fetchone()[0]
        assert int(meta["paras"]) == npara
        assert int(meta["chapters"]) == nchap
    finally:
        con.close()

    # --delete 单书清理
    del_res = bpi.delete_books(str(db), ["B0004"], root=str(workspace))
    assert del_res["deleted_books"] == 1
    assert _fts_books(db, "量子纠缠") == set()


def test_embed_prune_diff_and_zero_inference_compact(workspace):
    r_lib = run_cli("--root", workspace, "library")
    assert r_lib.returncode == 0, r_lib.stdout + r_lib.stderr
    emb_dir = workspace / "build" / "检索" / "embeddings"
    db = emb_dir / "emb.db"
    vec = emb_dir / "vectors.f16"

    store = ec.Store(db, vec, dim=4, model="t", chunk_chars=300)
    sig1 = ec.sig_of(ec.chapter_paths(workspace, "B0001"))
    sig2 = ec.sig_of(ec.chapter_paths(workspace, "B0002"))
    store.append_book(
        "B0001",
        "甲书",
        sig1,
        [{"path": "books/B0001/md/001.md", "seq": 0, "line": 1, "chars": 10, "text": "甲第一块"}],
        ec.pack_f16([[1.0, 0.0, 0.0, 0.0]], 4),
        10,
    )
    store.append_book(
        "B0002",
        "乙书",
        sig2,
        [
            {"path": "books/B0002/md/001.md", "seq": 0, "line": 1, "chars": 10, "text": "乙第一块"},
            {"path": "books/B0002/md/002.md", "seq": 1, "line": 1, "chars": 10, "text": "乙第二块"},
        ],
        ec.pack_f16([[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]], 4),
        20,
    )
    # 写入一本已不存在的孤儿书 B9999
    store.append_book(
        "B9999",
        "孤儿书",
        "deadbeef",
        [{"path": "books/B9999/md/001.md", "seq": 0, "line": 1, "chars": 8, "text": "孤儿块"}],
        ec.pack_f16([[0.0, 0.0, 0.0, 1.0]], 4),
        8,
    )
    store.db.close()

    # diff_embed 准确报出孤儿书
    diff = ec.diff_embed(workspace, db_path=db, server_url="http://127.0.0.1:1")
    assert diff["has_db"] is True
    assert diff["orphan"] == ["B9999"]
    assert diff["unchanged"] == 2
    assert diff["garbage"] == 0

    # prune 失活孤儿，再 compact 零推理压缩回收空间
    store2 = ec.Store(db, vec, dim=4, model="t", chunk_chars=300)
    pruned = store2.prune_orphans(["B0001", "B0002"])
    assert pruned == {"books": 1, "chunks": 1}
    assert store2.stats()["garbage"] == 1

    comp = store2.compact()
    assert comp["kept_chunks"] == 3
    assert comp["removed_garbage"] == 1
    assert comp["after_bytes"] == 3 * 4 * 2
    assert ec.cmd_verify(store2) == 0

    # 验证保留的向量值与顺序完整无损
    raw = vec.read_bytes()
    assert ec.unpack_f16(raw[0:8], 4) == pytest.approx([1.0, 0.0, 0.0, 0.0], rel=1e-3)
    assert ec.unpack_f16(raw[8:16], 4) == pytest.approx([0.0, 1.0, 0.0, 0.0], rel=1e-3)
    assert ec.unpack_f16(raw[16:24], 4) == pytest.approx([0.0, 0.0, 1.0, 0.0], rel=1e-3)
    store2.close()

    # CLI --sync --allow-missing-engine 在无 llama-server 时平滑退出 0
    r = run_cli(
        "--root", workspace, "run", "embed_corpus", "--sync", "--allow-missing-engine", "--root", str(workspace)
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_build_library_emit_json_diff_only(workspace):
    # 首次构建并同步 book.json
    r1 = run_cli("--root", workspace, "library", "--emit-json")
    assert r1.returncode == 0, r1.stdout + r1.stderr

    b1_json = workspace / "books" / "B0001" / "book.json"
    b2_json = workspace / "books" / "B0002" / "book.json"
    t1 = b1_json.stat().st_mtime_ns
    t2 = b2_json.stat().st_mtime_ns
    time.sleep(0.02)

    # 第二次无变化：写盘数应为 0，mtime 不变
    r2 = run_cli("--root", workspace, "library", "--emit-json")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    st2 = json.loads(r2.stdout)
    assert st2["book_json"] == 0
    assert b1_json.stat().st_mtime_ns == t1
    assert b2_json.stat().st_mtime_ns == t2

    # 只改 B0001 人账书名：仅写回 B0001，B0002 mtime 保持不变
    write_ledger(workspace, [("B0001", "文学", "甲书修订版", "")])
    time.sleep(0.02)
    r3 = run_cli("--root", workspace, "library", "--emit-json")
    assert r3.returncode == 0, r3.stdout + r3.stderr
    st3 = json.loads(r3.stdout)
    assert st3["book_json"] == 1
    assert b1_json.stat().st_mtime_ns != t1
    assert b2_json.stat().st_mtime_ns == t2


def test_retire_cleans_fts_and_deactivates_embed(workspace):
    # 准备 index.db + emb.db
    db = workspace / "build" / "检索" / "index.db"
    con = bpi.build(str(db), root=str(workspace))
    con.close()

    emb_dir = workspace / "build" / "检索" / "embeddings"
    store = ec.Store(emb_dir / "emb.db", emb_dir / "vectors.f16", dim=4, model="t")
    store.append_book(
        "B0001",
        "甲书",
        "sig1",
        [{"path": "books/B0001/md/001.md", "seq": 0, "line": 1, "chars": 10, "text": "甲块"}],
        ec.pack_f16([[1.0, 0.0, 0.0, 0.0]], 4),
        10,
    )
    store.db.close()

    # 建假源文件（write_book 建了 source 目录，补上对应文件让 gen_book_catalog 过检）
    (workspace / "books" / "B0001" / "source" / "甲书.epub").write_bytes(b"epub-data")
    (workspace / "books" / "B0002" / "source" / "乙书.epub").write_bytes(b"epub-data-2")
    (workspace / "books" / "B0003" / "source" / "丙书.txt").write_bytes(b"txt-data-3")
    r_lib = run_cli("--root", workspace, "library", "--emit-json")
    assert r_lib.returncode == 0, r_lib.stdout + r_lib.stderr

    # 预览应包含 embed_chunks=1
    r_prev = run_cli(
        "--root",
        workspace,
        "run",
        "retire_book",
        "--uid",
        "B0001",
        "--reason",
        "测试退库",
        "--dry-run",
        "--json",
        "--no-snapshot",
    )
    assert r_prev.returncode == 0, r_prev.stdout + r_prev.stderr
    preview = json.loads(r_prev.stdout)
    assert preview["embed_chunks"] == 1
    assert preview["index_rows"] > 0

    # 执行退库
    r_app = run_cli(
        "--root",
        workspace,
        "run",
        "retire_book",
        "--uid",
        "B0001",
        "--apply",
        "--plan-hash",
        preview["plan_hash"],
        "--reason",
        "测试退库",
        "--no-snapshot",
    )
    assert r_app.returncode == 0, r_app.stdout + r_app.stderr

    # 验证：FTS5 中已彻底无 B0001，emb.db 中 B0001 块已失活，且冷宫有归档记录
    assert _fts_books(db, "科层制") == set()
    store_after = ec.Store(emb_dir / "emb.db", emb_dir / "vectors.f16", dim=4, model="t")
    assert store_after.stats()["chunks"] == 0
    assert store_after.book_row("B0001")["status"] == "removed"
    store_after.db.close()
    assert not (workspace / "books" / "B0001").exists()
    assert (workspace / "books" / "_冷宫" / "甲书" / "source" / "甲书.epub").is_file()
    assert list((workspace / "工作区" / "批次").glob("移出-*/retired.tsv"))
