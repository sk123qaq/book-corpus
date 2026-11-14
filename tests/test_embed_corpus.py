"""embed_corpus.py 的切块与向量库：不碰 GPU，不请求服务。"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src" / "bookcorpus" / "scripts" / "embed_corpus.py"

spec = importlib.util.spec_from_file_location("embed_corpus_under_test", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def _write_md(tmp_path, body):
    p = tmp_path / "ch.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_chapter_chunks_respects_target_and_keeps_text(tmp_path):
    long_para = "长" * 2000
    body = (
        "---\ntitle: 测试\n---\n\n"
        "第一段讲礼治秩序与法治的差别，这一段要留在块里。\n\n"
        "![图](images/1.png)\n\n" + long_para + "\n\n最后一段收尾。\n"
    )
    md = _write_md(tmp_path, body)
    chunks = mod.chapter_chunks(md, target=300, overlap=0)
    texts = [t for _, t in chunks]
    assert chunks, "没有切出块"
    assert max(len(t) for t in texts) <= int(300 * 1.15) + 2
    joined = "".join(texts)
    assert "第一段讲礼治秩序" in joined
    assert "最后一段收尾" in joined
    assert joined.count("长") == 2000
    assert "images/1.png" not in joined
    assert "title: 测试" not in joined


def test_chapter_chunks_overlap_repeats_last_piece(tmp_path):
    body = "".join(f"第{i}段内容。" for i in range(1, 40))
    md = _write_md(tmp_path, body)
    chunks = mod.chapter_chunks(md, target=60, overlap=1)
    assert len(chunks) > 2
    first_tail = chunks[0][1].split("\n")[-1]
    second_head = chunks[1][1].split("\n")[0]
    assert first_tail == second_head


def test_split_pieces_hard_splits_single_long_sentence():
    text = "一" * 1000
    pieces = mod.split_pieces(text, 100)
    assert all(len(p) <= 100 for p in pieces)
    assert "".join(pieces) == text


def test_store_roundtrip_resume_and_force(tmp_path):
    db = tmp_path / "emb.db"
    vec = tmp_path / "vectors.f16"
    s = mod.Store(str(db), str(vec), dim=4, model="t", chunk_chars=300)
    chunks = [
        {"path": "a.md", "seq": 0, "line": 1, "chars": 5, "text": "aaaaa"},
        {"path": "a.md", "seq": 1, "line": 2, "chars": 6, "text": "bbbbbb"},
    ]
    raw = mod.pack_f16([[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]], 4)
    s.append_book("B1", "书一", "sig", chunks, raw, 123)
    assert s.last_id == 2
    assert vec.stat().st_size == 2 * 4 * 2
    st = s.stats()
    assert st["chunks"] == 2
    assert st["books"]["done"] == 1

    got = mod.unpack_f16(raw[:8], 4)
    assert got == pytest.approx([0.1, 0.2, 0.3, 0.4], rel=1e-3)
    s.db.close()

    with vec.open("ab") as fh:
        fh.write(b"\x00" * 16)
    s2 = mod.Store(str(db), str(vec), dim=4, model="t", chunk_chars=300)
    assert s2.last_id == 2, "last_id 被半截向量带偏"
    assert vec.stat().st_size == 2 * 4 * 2, "半截向量没截掉"

    s2.append_book(
        "B1",
        "书一",
        "sig2",
        [{"path": "a.md", "seq": 0, "line": 1, "chars": 3, "text": "ccc"}],
        mod.pack_f16([[1.0, 0.0, 0.0, 0.0]], 4),
        9,
    )
    st = s2.stats()
    assert s2.last_id == 3
    assert st["chunks"] == 1, "旧块没失效"
    assert st["garbage"] == 2
    s2.db.close()


def test_write_lock_blocks_second_writer(tmp_path):
    db = tmp_path / "emb.db"
    vec = tmp_path / "vectors.f16"
    s1 = mod.Store(str(db), str(vec), dim=4, model="t")
    s1.acquire_write_lock()
    s2 = mod.Store(str(db), str(vec), dim=4, model="t")
    with pytest.raises(SystemExit):
        s2.acquire_write_lock()
    s1.db.close()
    s2.db.close()


def test_sig_changes_with_content(tmp_path):
    p = _write_md(tmp_path, "一段正文。")
    a = mod.sig_of([p])
    p.write_text("一段正文改过了。", encoding="utf-8")
    os.utime(p, (1, 1))
    b = mod.sig_of([p])
    assert a != b
