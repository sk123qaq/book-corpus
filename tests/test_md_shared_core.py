"""Phase 1 共享 L1 解析内核、章节角色契约与检索切块集成测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookcorpus.md as md_pkg
from bookcorpus.md import (
    MdBlock,
    linearize_table,
    load_manifest_chapter_roles,
    normalize_chapter_role,
    parse_blocks,
    parse_markdown,
    read_chapter_role,
    resolve_chapter_role,
)
from bookcorpus.reading import footnotes as reading_footnotes
from bookcorpus.reading import mdparse as reading_mdparse
from bookcorpus.reading.library import Library
from bookcorpus.reading.locator import resolve_open_target
from bookcorpus.scripts import build_para_index as bpi
from bookcorpus.scripts import embed_corpus as ec
from bookcorpus.scripts import lib_fts_cjk as cjk


def test_reading_reexports_and_block_version_boundary():
    assert not hasattr(md_pkg, "BLOCK_VERSION")
    assert reading_mdparse.BLOCK_VERSION == 2
    assert reading_mdparse.parse_markdown is md_pkg.parse_markdown
    assert reading_footnotes.resolve_chapter_footnotes is md_pkg.resolve_chapter_footnotes
    assert reading_footnotes.NOTE_HEADING_RE is md_pkg.NOTE_HEADING_RE


def test_parse_blocks_contract_heading_path_lines_and_cleaning(tmp_path: Path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "note.png").write_bytes(b"x")
    (images / "fig.png").write_bytes(b"x")

    raw = (
        '---\ntitle: "第一章 秩序"\n---\n\n'
        "# 第一章 秩序\n\n"
        "开篇导语^![note](images/note.png)^与属性{#p1 .calibre2}以及<div>HTML</div>。\n\n"
        "## 第一节 礼治 {#sec-1}\n\n"
        "这里有双括号脚注[[1]]()与<sup>上标</sup>。\n\n"
        "### 1.1 表格样本\n\n"
        "| 概念 | 含义 |\n"
        "| --- | --- |\n"
        "| 礼治 | 靠传统与教化 |\n\n"
        "![](images/fig.png)\n\n"
        "## 第二节 尾注\n\n"
        "- [[1]]() 列表式尾注一\n"
        "- [[2]]() 列表式尾注二\n"
        "- [[3]]() 列表式尾注三\n"
    )
    blocks = parse_blocks(raw, stem="01_秩序", base_dir=tmp_path)
    assert all(isinstance(b, MdBlock) for b in blocks)
    assert all(b.start_line >= 1 and b.end_line >= b.start_line for b in blocks)

    b_intro = blocks[0]
    assert b_intro.kind == "paragraph"
    assert b_intro.heading_path == "第一章 秩序"
    assert b_intro.start_line == 7
    assert "<div>" not in b_intro.clean_text
    assert ".calibre2" not in b_intro.clean_text
    assert "note.png" not in b_intro.clean_text
    assert "^" not in b_intro.clean_text
    assert len(b_intro.fn_refs) == 1

    b_sec1 = blocks[1]
    assert b_sec1.kind == "heading"
    assert b_sec1.heading_path == "第一章 秩序 > 第一节 礼治"
    assert "{#sec-1}" not in b_sec1.clean_text

    b_p2 = blocks[2]
    assert b_p2.heading_path == "第一章 秩序 > 第一节 礼治"
    assert "[[1]]()" not in b_p2.clean_text
    assert "<sup>" not in b_p2.clean_text
    assert b_p2.fn_refs[0]["marker"] == "1"

    b_tbl = blocks[4]
    assert b_tbl.kind == "table"
    assert b_tbl.heading_path == "第一章 秩序 > 第一节 礼治 > 1.1 表格样本"
    assert b_tbl.clean_text == "概念: 礼治; 含义: 靠传统与教化"
    assert b_tbl.text == "概念 含义 礼治 靠传统与教化"

    b_img = blocks[5]
    assert b_img.kind == "image"
    assert b_img.clean_text == ""

    # 尾注列表提升后仍带真实 1-based 行号与 heading_path
    fn_blocks = [b for b in blocks if b.kind == "footnote"]
    assert len(fn_blocks) == 3
    assert all(b.start_line >= 18 for b in fn_blocks)
    assert all(b.heading_path == "第一章 秩序 > 第二节 尾注" for b in fn_blocks)


def test_html_table_linearization_and_cell_cleaning():
    raw = (
        '<table border="1">'
        "<tr><th>年份</th><th>指标 {#c1 .calibre1}</th></tr>"
        "<tr><td>2024</td><td>增长 12% &amp; 稳健[[2]]()</td></tr>"
        "</table>\n"
    )
    parsed = parse_markdown(raw)
    tbl = parsed["blocks"][0]
    assert tbl["kind"] == "table"
    assert tbl["head"] == ["年份", "指标 {#c1 .calibre1}"]
    assert "<table" not in tbl["clean_text"]
    assert "年份: 2024" in tbl["clean_text"]
    assert "增长 12% & 稳健" in tbl["clean_text"]
    assert "[[2]]()" not in tbl["clean_text"]
    assert ".calibre1" not in tbl["clean_text"]
    assert linearize_table(["甲", "乙"], [["1", "2"]]) == "甲: 1; 乙: 2"
    assert linearize_table([], [["无表头A", "无表头B"]]) == "无表头A; 无表头B"


def test_chapter_role_resolution_priority(tmp_path: Path):
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    (md_dir / ".manifest.json").write_text(
        json.dumps(
            {
                "chapter_roles": {
                    "01_版权页.md": "front_back",
                    "02_正文": "notes",
                    "03_注释.md": {"role": "notes"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ch_fb = md_dir / "01_版权页.md"
    ch_fb.write_text("# 版权页\n\n版权所有。\n", encoding="utf-8")
    assert read_chapter_role(ch_fb) == "front_back"

    # YAML front matter 优先于 .manifest.json
    ch_override = md_dir / "02_正文.md"
    ch_override.write_text("---\nchapter_role: body\n---\n\n# 正文\n\n正文内容。\n", encoding="utf-8")
    assert read_chapter_role(ch_override) == "body"

    ch_notes = md_dir / "03_注释.md"
    ch_notes.write_text("# 注释\n\n[1] 注释内容。\n", encoding="utf-8")
    assert read_chapter_role(ch_notes) == "notes"

    # 未标注老书默认 body
    ch_default = md_dir / "04_老章.md"
    ch_default.write_text("# 老章\n\n未标注章节。\n", encoding="utf-8")
    assert read_chapter_role(ch_default) == "body"
    assert normalize_chapter_role("UNKNOWN") == "body"

    roles = load_manifest_chapter_roles(md_dir)
    assert resolve_chapter_role("03_注释.md", manifest_roles=roles) == "notes"


def _init_min_book(root: Path, uid: str = "B0001") -> Path:
    bdir = root / "books" / uid
    md_dir = bdir / "md"
    md_dir.mkdir(parents=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    (bdir / "book.json").write_text(
        json.dumps({"uid": uid, "title": "测试书", "subjects": ["社科"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return md_dir


def test_build_para_index_l1_boundaries_roles_and_locator_alignment(tmp_path: Path):
    root = tmp_path / "ws"
    md_dir = _init_min_book(root, "B0001")
    (md_dir / ".manifest.json").write_text(
        json.dumps({"chapter_roles": {"01_封面.md": "front_back", "03_注释.md": "notes"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (md_dir / "01_封面.md").write_text("# 封面\n\n版权水页关键词绝不入默认索引。\n", encoding="utf-8")
    (md_dir / "02_正文.md").write_text(
        "---\ntitle: 正文\n---\n\n"
        "# 正文\n\n"
        "前节短段一。\n\n"
        "前节短段二。\n\n"
        "## 第二节 制度分析 {#s2 .calibre1}\n\n"
        "这里讨论现代科层制的演变与约束机制^![note](images/note.png)^。\n\n"
        "<table><tr><th>维度</th><th>特征</th></tr><tr><td>科层</td><td>规则化分工</td></tr></table>\n\n"
        "![](images/empty.png)\n",
        encoding="utf-8",
    )
    (md_dir / "03_注释.md").write_text("# 注释\n\n[1] 尾注专属词条。\n", encoding="utf-8")

    db_path = root / "build" / "检索" / "index.db"
    con = bpi.build(str(db_path), root=str(root))
    try:
        rows = con.execute("SELECT path, line, text, tok FROM para ORDER BY id").fetchall()
    finally:
        con.close()

    # front_back 与 notes 默认被过滤
    paths = {r[0] for r in rows}
    assert paths == {"books/B0001/md/02_正文.md"}

    texts = [r[2] for r in rows]
    # 短段一与短段二合并，但绝不跨 heading 合并进 第二节 制度分析
    assert any("前节短段一。\n前节短段二。" in t for t in texts)
    assert all(not ("前节短段" in t and "第二节" in t) for t in texts)
    # table 线性化且无 HTML/note.png/calibre 残渣，无文字图片跳过
    joined = "\n".join(texts)
    assert "维度: 科层; 特征: 规则化分工" in joined
    assert "<table" not in joined
    assert "note.png" not in joined
    assert ".calibre1" not in joined
    assert "empty.png" not in joined

    # para.line 与 reading/locator.py /open 跳转同源一致
    lib = Library(str(root))
    target_row = next(r for r in rows if "现代科层制" in r[2])
    target = resolve_open_target(lib, uid="B0001", path=target_row[0], line=target_row[1], quote=target_row[2][:40])
    assert target.kind == "chapter"
    assert target.block_ids == ("B04",)

    tbl_row = next(r for r in rows if "维度: 科层" in r[2])
    tbl_target = resolve_open_target(lib, uid="B0001", path=tbl_row[0], line=tbl_row[1], quote="规则化分工")
    assert tbl_target.block_ids == ("B05",)

    # 显式 --include-notes / --include-front-back 可放行
    db_all = root / "build" / "检索" / "index_all.db"
    con_all = bpi.build(str(db_all), root=str(root), include_notes=True, include_front_back=True)
    try:
        tok_q = cjk.index_text("尾注专属")
        hits = con_all.execute("SELECT count(*) FROM para_fts WHERE para_fts MATCH ?", (f'"{tok_q}"',)).fetchone()[0]
        assert hits == 1
    finally:
        con_all.close()


def test_embed_corpus_heading_split_roles_context_header_and_sig(tmp_path: Path, capsys):
    root = tmp_path / "ws"
    md_dir = _init_min_book(root, "B0001")
    (md_dir / "01_前言.md").write_text("---\nchapter_role: front_back\n---\n\n# 前言\n\n水页目录。\n", encoding="utf-8")
    ch2 = md_dir / "02_正文.md"
    ch2.write_text(
        "# 正文\n\n"
        "## 第一节 概念\n\n"
        + ("甲节正文句。" * 8)
        + "\n\n## 第二节 机制\n\n"
        + ("乙节正文句。" * 8)
        + "\n\n<table><tr><th>项目</th><th>值</th></tr><tr><td>采样</td><td>纯净文本</td></tr></table>\n\n"
        "![](images/pic.png)\n",
        encoding="utf-8",
    )

    # 默认关闭 context_header，且跳过 front_back 与无文字图片，不跨 heading 硬拼
    chunks_plain = ec.book_chunks(root, "B0001", target=700, overlap=1, context_header=False, book_title="测试书")
    assert len(chunks_plain) == 2
    assert "甲节正文句" in chunks_plain[0]["text"]
    assert "乙节正文句" not in chunks_plain[0]["text"]
    assert "乙节正文句" in chunks_plain[1]["text"]
    assert "项目: 采样; 值: 纯净文本" in chunks_plain[1]["text"]
    assert "pic.png" not in chunks_plain[1]["text"]
    assert "embed_text" not in chunks_plain[0]
    assert chunks_plain[0]["line"] == 3
    assert chunks_plain[1]["line"] == 7

    lib = Library(str(root))
    open_t = resolve_open_target(
        lib,
        uid="B0001",
        path=chunks_plain[1]["path"],
        line=chunks_plain[1]["line"],
        quote=chunks_plain[1]["text"][:40],
    )
    assert open_t.kind == "chapter"
    assert open_t.block_ids

    # 开启 context_header：chunk.text 仍为纯净正文，embed_text 携带上下文头
    chunks_ctx = ec.book_chunks(root, "B0001", target=700, overlap=0, context_header=True, book_title="测试书")
    assert chunks_ctx[0]["text"] == chunks_plain[0]["text"]
    assert chunks_ctx[0]["embed_text"].startswith("《测试书》 / 正文 / 第一节 概念\n")

    # sig_of 默认与存量一致，开启 context_header 时有独立签名
    paths = ec.chapter_paths(root, "B0001")
    sig_default = ec.sig_of(paths)
    sig_ctx = ec.sig_of(paths, context_header=True)
    assert sig_default != sig_ctx

    # Store 记录 context_header 且混用时打印提示
    db = tmp_path / "emb.db"
    vec = tmp_path / "vectors.f16"
    s1 = ec.Store(db, vec, dim=4, model="t", context_header=False)
    s1.append_book(
        "B0001",
        "测试书",
        sig_default,
        [{"path": chunks_plain[0]["path"], "seq": 0, "line": 3, "chars": 10, "text": chunks_plain[0]["text"]}],
        ec.pack_f16([[1.0, 0.0, 0.0, 0.0]], 4),
        10,
    )
    assert s1.meta("context_header") == "0"
    s1.close()

    s2 = ec.Store(db, vec, dim=4, model="t", context_header=True)
    err = capsys.readouterr().err
    assert "context_header=0" in err
    s2.close()


@pytest.mark.real_corpus
def test_real_corpus_dirty_books_clean_and_line_aligned():
    root = Path.home() / "书库"
    if not (root / "books").is_dir():
        pytest.skip("需要本机 ~/书库 语料")
    for uid in ("B0537", "B1726", "B0130", "B1565"):
        md_dir = root / "books" / uid / "md"
        files = sorted(p for p in md_dir.glob("*.md") if p.name != "README.md")[:5]
        rows, _, _ = bpi._extract_book_rows(uid, [str(f) for f in files], root=str(root))
        assert rows, f"{uid} 未切出任何检索段"
        for _rel, _bk, line, _chars, _tok, txt in rows:
            assert line >= 1
            assert "<table" not in txt.lower()
            assert "note.png" not in txt.lower()
            assert "[[1]]()" not in txt
