"""分层读脚注解析、章内回跳与跨文件提示测试。"""

from __future__ import annotations

import json
from pathlib import Path

from bookcorpus.reading.library import Library
from bookcorpus.reading.mdparse import parse_markdown
from bookcorpus.reading.service import ReadingService


def _write_test_book(root: Path, uid: str, title: str, chapters: list[tuple[str, str]]) -> None:
    bdir = root / "books" / uid
    md_dir = bdir / "md"
    md_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in chapters:
        (md_dir / fname).write_text(content, encoding="utf-8")
    (bdir / "book.json").write_text(
        json.dumps(
            {
                "uid": uid,
                "title": title,
                "author": "测试作者",
                "subjects": ["历史"],
                "retention": "keep",
                "sources": [{"format": "epub", "bytes": 100, "sha256": "0" * 64, "primary_src": 1}],
                "awards": [],
                "notes": "",
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def test_parse_caret_bracket_and_dbl_bracket_defs():
    raw = (
        "# 第三章\n\n"
        "正文第一句[^[25]^]()，还有一句[^[2]^]()。\n\n"
        "## 【注释】\n\n"
        "[[2]]()关于福王庄田。\n\n"
        "多段注释续文。\n\n"
        "[[25]]()这一点似乎尚未引起历史学家的注意。\n"
    )
    parsed = parse_markdown(raw)
    blocks = parsed["blocks"]
    assert len(blocks) == 5
    p = blocks[0]
    assert p["text"] == "正文第一句，还有一句。"
    assert [r["marker"] for r in p["fn_refs"]] == ["25", "2"]
    assert p["fn_refs"][0]["pos"] == len("正文第一句")
    assert p["fn_refs"][1]["pos"] == len("正文第一句，还有一句")
    assert p["fn_refs"][0]["target_block"] == "B05"
    assert p["fn_refs"][1]["target_block"] == "B03"

    fn2 = blocks[2]
    assert fn2["kind"] == "footnote"
    assert fn2["marker"] == "2"
    assert fn2["text"] == "关于福王庄田。"
    assert fn2["ref_blocks"] == ["B01"]
    assert blocks[3]["kind"] == "paragraph"  # 续文保持原 block ID 与结构


def test_parse_other_inline_and_def_variants():
    raw = (
        "# 第一章\n\n"
        "变体一^[[14]]()^，变体二^[1]^，变体三^【2】^，变体四^①^，标准[^s1]，中段[[9]]()。\n\n"
        "## 注释\n\n"
        "[1]译注一。\n\n"
        "【2】译注二。\n\n"
        "① 圈注内容。\n\n"
        "[[14]]()双括号注。\n\n"
        "[^s1]: 标准注。\n\n"
        "[[9]]()无脱字符双括号。\n"
    )
    parsed = parse_markdown(raw)
    blocks = parsed["blocks"]
    p = blocks[0]
    refs = p["fn_refs"]
    assert [r["marker"] for r in refs] == ["14", "1", "2", "①", "s1", "9"]
    assert all(r["target_block"] for r in refs)
    defs_by_id = {b["id"]: b for b in blocks if b["kind"] == "footnote"}
    assert defs_by_id[refs[0]["target_block"]]["text"] == "双括号注。"
    assert defs_by_id[refs[1]["target_block"]]["text"] == "译注一。"
    assert defs_by_id[refs[2]["target_block"]]["text"] == "译注二。"
    assert defs_by_id[refs[3]["target_block"]]["text"] == "圈注内容。"
    assert defs_by_id[refs[4]["target_block"]]["text"] == "标准注。"
    assert defs_by_id[refs[5]["target_block"]]["text"] == "无脱字符双括号。"


def test_mid_chapter_restarted_numbering_matches_nearest_subsequent_def():
    raw = "# 章节\n\n第一节内容^[1]^。\n\n[1]第一节的注。\n\n第二节内容^[1]^。\n\n[1]第二节的注。\n"
    parsed = parse_markdown(raw)
    blocks = parsed["blocks"]
    assert blocks[0]["fn_refs"][0]["target_block"] == "B02"
    assert blocks[2]["fn_refs"][0]["target_block"] == "B04"
    assert blocks[1]["kind"] == "footnote"
    assert blocks[1]["text"] == "第一节的注。"
    assert blocks[3]["kind"] == "footnote"
    assert blocks[3]["text"] == "第二节的注。"


def test_trailing_paren_notes_and_avoid_ordinary_circled_prose():
    raw_notes = "# 醉步男\n\n被狸猫^(1)^迷住了，参见白凤^(2)^。\n\n正文收尾。\n\n(1)狸猫传说。\n\n(2)白凤简称。\n"
    parsed_notes = parse_markdown(raw_notes)
    blocks = parsed_notes["blocks"]
    refs = blocks[0]["fn_refs"]
    assert [r["marker"] for r in refs] == ["1", "2"]
    assert refs[0]["target_block"] == "B03"
    assert refs[1]["target_block"] == "B04"
    assert blocks[2]["kind"] == "footnote"
    assert blocks[3]["kind"] == "footnote"

    # 没有对应上标引用时，结尾的 ① ② 只是普通要点段落，不能误转为 footnote
    raw_plain = "# 综述\n\n政策要点如下：\n\n① 扶助性。\n\n② 覆盖面广。\n"
    parsed_plain = parse_markdown(raw_plain)
    assert [b["kind"] for b in parsed_plain["blocks"]] == ["paragraph", "paragraph", "paragraph"]


def test_negative_math_exponents_ordinals_and_external_urls():
    raw = (
        "# 科学史\n\n"
        "公式 E=MC^2^ 与 2^80^，以及 19^th^ 世纪，见[官网](https://example.com)和[2](https://example.org)。\n"
    )
    parsed = parse_markdown(raw)
    p = parsed["blocks"][0]
    assert "fn_refs" not in p
    assert "E=MC^2^" in p["text"]
    assert "官网" in p["text"]


def test_offsets_with_inline_formatting_and_line_start_edge_cases():
    raw = (
        "# 混合\n\n"
        "**迈克尔·彭博^[**[203]**](13_注释.md)^**在*此处*[^[4]^]()里躲起来啦`code`。\n\n"
        "- 列表项一[^[1]^]()与**加粗**\n\n"
        "[[1]]()列表项注释。\n\n"
        "[[4]]()方言词汇。\n"
    )
    parsed = parse_markdown(raw)
    p = parsed["blocks"][0]
    assert p["text"] == "迈克尔·彭博在此处里躲起来啦code。"
    refs = p["fn_refs"]
    assert refs[0]["marker"] == "203"
    assert refs[0]["target_file"] == "13_注释.md"
    assert refs[0]["pos"] == len("迈克尔·彭博")
    assert refs[1]["marker"] == "4"
    assert refs[1]["pos"] == len("迈克尔·彭博在此处")
    assert refs[1]["target_block"] == "B04"

    lst = parsed["blocks"][1]
    assert lst["kind"] == "list"
    assert lst["items"] == ["列表项一与加粗"]
    assert lst["item_fn_refs"][0][0]["marker"] == "1"
    assert lst["item_fn_refs"][0][0]["pos"] == len("列表项一")
    assert lst["item_fn_refs"][0][0]["target_block"] == "B03"


def test_cross_file_link_formats_and_word_cond_and_empty_dbl():
    raw = (
        "# 跨文件样本\n\n"
        "一^[[192]](13_注释.md)^，"
        "二^[2](17_Notes.md)^，"
        "三[^(11)^](60_报告.md)，"
        "四[^注33^](52_注1.md)，"
        "五[1](10_註釋.md)，"
        "六^[^2^](<18_前置页 28.md>)^，"
        "七[^[if !supportFootnotes]^[7]^^]()。\n\n"
        "[[7]]()\n"
    )
    parsed = parse_markdown(raw)
    refs = parsed["blocks"][0]["fn_refs"]
    assert [(r["marker"], r["target_file"]) for r in refs] == [
        ("192", "13_注释.md"),
        ("2", "17_Notes.md"),
        ("11", "60_报告.md"),
        ("注33", "52_注1.md"),
        ("1", "10_註釋.md"),
        ("2", "18_前置页 28.md"),
        ("7", ""),
    ]
    assert refs[-1]["target_block"] == "B02"
    assert parsed["blocks"][1]["kind"] == "footnote"
    assert parsed["blocks"][1]["marker"] == "7"


def test_service_cross_file_enrichment_and_title_page_merge(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")

    # B9901: 第一章合并了前置页，含显式外链与同书注释章候选；第二章为独立注释章
    _write_test_book(
        root,
        "B9901",
        "跨文件测试书",
        [
            ("01_第一章.md", "# 第一章\n\n题记。\n"),
            (
                "02_前置页 1.md",
                "正文一段^[[192]](03_注释.md)^，另一处[^[1]^]()，还有本地注[^[2]^]()。\n\n"
                + "补充正文。" * 140
                + "\n\n[[2]]()本章自带注文。\n",
            ),
            ("03_注释.md", "# 注释\n\n[[1]]()全书注释一。\n\n[[192]]()跨章注释。\n"),
        ],
    )
    # B9902: 无独立注释章且当前章无注文 -> unresolved
    _write_test_book(
        root,
        "B9902",
        "未收注测试书",
        [("01_正文.md", "# 正文\n\n此处有孤立角标[^[9]^]()。\n")],
    )

    svc = ReadingService(str(root), annotate=False)
    ch1 = svc.chapter("B9901", 1)
    assert ch1 is not None
    # 合并前置页后块 ID 重排（B01=题记, B02=正文一段, ...），target_block/ref_blocks 必须随新 ID 同步
    p2 = ch1["blocks"][1]
    assert p2["id"] == "B02"
    refs = p2["fn_refs"]
    assert refs[0]["status"] == "external_chapter"
    assert refs[0]["target_chapter"] == {"n": "03", "title": "注释", "file": "03_注释.md", "block_id": "B02"}
    assert refs[1]["status"] == "external_candidate"
    assert refs[1]["target_block"] == ""
    assert refs[1]["target_chapter"] == {"n": "03", "title": "注释", "file": "03_注释.md", "block_id": "B01"}
    assert refs[2]["status"] == "local"
    local_def = next(b for b in ch1["blocks"] if b["id"] == refs[2]["target_block"])
    assert local_def["ref_blocks"] == ["B02"]
    assert ch1["footnotes"]["note_chapters"] == [{"n": "03", "title": "注释", "file": "03_注释.md"}]
    assert ch1["footnotes"]["unresolved_refs"] == 2

    ch_unres = svc.chapter("B9902", 1)
    assert ch_unres is not None
    r_unres = ch_unres["blocks"][0]["fn_refs"][0]
    assert r_unres["status"] == "unresolved"
    assert ch_unres["footnotes"]["note_chapters"] == []

    # 直接核对 Library.read_chapter 幂等性
    lib = Library(str(root))
    raw_ch = lib.read_chapter("B9901", 1)
    assert raw_ch is not None
    assert raw_ch["blocks"][1]["fn_refs"][2]["target_block"] == local_def["id"]


def test_colon_marker_and_multi_chapter_backlink_disambiguation(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")

    _write_test_book(
        root,
        "B9903",
        "多章重复编号测试",
        [
            ("01_第一章.md", "# Chapter 1: First\n\n第一句^[1]^，第二句^[2]^。\n"),
            ("02_第二章.md", "# Chapter 2: Second\n\n第二章第一句^[1](03_Notes.md)^，第二句^[2](03_Notes.md)^。\n"),
            (
                "03_Notes.md",
                "# Notes\n\n"
                "### Chapter 1: First\n\n"
                "[1](01_第一章.md). 第一章注一。\n\n"
                "[2](01_第一章.md). 第一章注二。\n\n"
                "### Chapter 2: Second\n\n"
                "[1](02_第二章.md). 第二章注一。\n\n"
                "[2](02_第二章.md). 第二章注二。\n\n"
                "[注33:](02_第二章.md)冒号注测试。\n",
            ),
        ],
    )

    notes_parsed = parse_markdown((root / "books" / "B9903" / "md" / "03_Notes.md").read_text(encoding="utf-8"))
    colon_blk = next(b for b in notes_parsed["blocks"] if b.get("marker") == "注33")
    assert colon_blk["kind"] == "footnote"
    assert colon_blk["back_file"] == "02_第二章.md"
    assert colon_blk["text"] == "冒号注测试。"

    svc = ReadingService(str(root), annotate=False)
    ch2 = svc.chapter("B9903", 2)
    assert ch2 is not None
    refs = ch2["blocks"][0]["fn_refs"]
    notes_ch = svc.chapter("B9903", 3)
    assert notes_ch is not None
    by_id = {b["id"]: b for b in notes_ch["blocks"]}
    assert by_id[refs[0]["target_chapter"]["block_id"]]["text"] == "第二章注一。"
    assert by_id[refs[1]["target_chapter"]["block_id"]]["text"] == "第二章注二。"


def test_real_corpus_cross_file_target_blocks():
    root = Path("~/书库").expanduser()
    if not (root / "books.toml").exists():
        return
    svc = ReadingService(str(root), annotate=False)

    ch_1786 = svc.chapter("B1786", 12)
    assert ch_1786 is not None
    ref_192 = next(r for b in ch_1786["blocks"] for r in (b.get("fn_refs") or []) if r["marker"] == "192")
    assert ref_192["target_chapter"]["block_id"] == "B193"

    ch_1704 = svc.chapter("B1704", 6)
    assert ch_1704 is not None
    ref_2 = next(r for b in ch_1704["blocks"] for r in (b.get("fn_refs") or []) if r["marker"] == "2")
    notes_1704 = svc.chapter("B1704", 17)
    assert notes_1704 is not None
    target_blk = next(b for b in notes_1704["blocks"] if b["id"] == ref_2["target_chapter"]["block_id"])
    assert target_blk["id"] != "B05"
    assert "keynote" in target_blk["text"]

    ch_0151 = svc.chapter("B0151", 33)
    assert ch_0151 is not None
    ref_z33 = next(r for b in ch_0151["blocks"] for r in (b.get("fn_refs") or []) if r["marker"] == "注33")
    notes_0151 = svc.chapter("B0151", 52)
    assert notes_0151 is not None
    z33_blk = next(b for b in notes_0151["blocks"] if b["id"] == ref_z33["target_chapter"]["block_id"])
    assert "传奇冒险家" in z33_blk["text"]

    ch_0022 = svc.chapter("B0022", 1)
    assert ch_0022 is not None
    ref_0022 = next(r for b in ch_0022["blocks"] for r in (b.get("fn_refs") or []) if r["marker"] == "1")
    assert ref_0022["status"] == "external_candidate"
    assert ref_0022["target_chapter"] == {"n": "37", "title": "脚注", "file": "37_脚注.md", "block_id": "B01"}


def test_real_corpus_b0019_ch08_footnotes():
    path = Path("~/书库/books/B0019/md/08_第三章世间已无张居正.md").expanduser()
    if not path.exists():
        return
    parsed = parse_markdown(path.read_text(encoding="utf-8"))
    blocks = parsed["blocks"]
    assert len(blocks) == 158
    for blk in blocks:
        assert "[^[" not in blk["text"]
        assert "[[" not in blk["text"]
    refs = [ref for blk in blocks for ref in (blk.get("fn_refs") or [])]
    assert len(refs) == 54
    assert all(ref["target_block"] in {f"B{i:02d}" for i in range(101, 159)} for ref in refs)
    ref25 = next(ref for ref in refs if ref["marker"] == "25")
    assert ref25["target_block"] == "B129"
    def25 = next(blk for blk in blocks if blk["id"] == "B129")
    assert def25["kind"] == "footnote"
    assert def25["marker"] == "25"
    assert def25["text"].startswith("这一点似乎尚未引起历史学家的注意")
