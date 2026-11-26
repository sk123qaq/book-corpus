"""Phase 2 L0 规范化器、上游转档改进、入库管线集成与伴读水章折叠全套单测。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bookcorpus.md import (
    CANONICAL_VERSION,
    normalize_book_md,
    parse_markdown,
    read_chapter_role,
)
from bookcorpus.reading.chapmap import plan_chunks
from bookcorpus.reading.library import Library
from bookcorpus.reading.preflight import evaluate
from bookcorpus.reading.service import ReadingService
from bookcorpus.scripts import build_para_index as bpi
from bookcorpus.scripts import docs_to_md, pdf_md_batch, pdf_ocr_to_md, split_md_batch

REPO = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO / ".agents" / "skills" / "ebook-to-md" / "scripts" / "verify_output.py"


def _run_verify(md_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), str(md_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_basic_book(
    root: Path, uid: str, title: str, chapters: dict[str, str], *, images: dict[str, bytes] | None = None
) -> Path:
    bdir = root / "books" / uid
    md_dir = bdir / "md"
    md_dir.mkdir(parents=True, exist_ok=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    (bdir / "book.json").write_text(
        json.dumps({"uid": uid, "title": title, "author": "测试作者", "subjects": ["社科"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    for name, content in chapters.items():
        (md_dir / name).write_text(content, encoding="utf-8")
    if images:
        img_dir = md_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        for iname, blob in images.items():
            (img_dir / iname).write_bytes(blob)
    return md_dir


def test_split_three_tiers_and_readme_rewrite(tmp_path: Path):
    # Tier 1：已有章级标题，且含超大「第1篇」二次细分
    root1 = tmp_path / "ws1"
    part1_sec = "\n\n".join(f"### 1.{i} 节标题\n\n" + ("制度分析正文句子。" * 350) for i in range(1, 10))
    single_t1 = (
        '---\ntitle: "测试大书"\nauthor: "作者甲"\nsource: "big.pdf"\n---\n\n'
        "# 测试大书\n\n"
        "## 前言\n\n" + ("前言内容介绍。" * 60) + "\n\n"
        "## 第1篇 总论\n\n" + part1_sec + "\n\n"
        "## 第2章 分论\n\n" + ("第二章正文内容。" * 120) + "\n\n"
        "![](images/chart.png)\n\n"
        "## 后记\n\n" + ("后记致谢内容。" * 40) + "\n"
    )
    md1 = _write_basic_book(root1, "B0001", "测试大书", {"测试大书.md": single_t1}, images={"chart.png": b"PNGDATA"})
    res1 = normalize_book_md(md1)
    assert res1["changed"] is True
    assert res1["did_split"] is True
    assert res1["stats"]["chapters_after"] >= 4
    assert not (md1 / "测试大书.md").exists()
    readme1 = (md1 / "README.md").read_text(encoding="utf-8")
    assert "- 作者：测试作者" in readme1 or "- 来源：`big.pdf`" in readme1
    assert "images/chart.png" in "".join(
        p.read_text(encoding="utf-8") for p in md1.glob("*.md") if p.name != "README.md"
    )
    vp1 = _run_verify(md1)
    assert vp1.returncode == 0, vp1.stdout + vp1.stderr
    for ch in md1.glob("*.md"):
        if ch.name == "README.md":
            continue
        parsed = parse_markdown(ch.read_text(encoding="utf-8"), stem=ch.stem, base_dir=md1)
        gate = evaluate(parsed["blocks"], plan_chunks(parsed["blocks"], 18000, 100))
        assert gate.ok, f"{ch.name} still triggers {gate.code}"

    # Tier 2：任意 ## 按目标 8k-18k 归并切章
    root2 = tmp_path / "ws2"
    sec_blocks = "\n\n".join(f"## Topic {i}\n\n" + (f"英文与中文段落第{i}节讨论社会现象。" * 180) for i in range(1, 15))
    md2 = _write_basic_book(root2, "B0002", "小节归并书", {"小节归并书.md": f"# 小节归并书\n\n{sec_blocks}\n"})
    res2 = normalize_book_md(md2)
    assert res2["did_split"] is True
    assert 2 <= res2["stats"]["chapters_after"] < 14
    assert _run_verify(md2).returncode == 0

    # Tier 3：无标题长书按语义空行分段切卷
    root3 = tmp_path / "ws3"
    paras = "\n\n".join(f"这是一段没有标题的长文内容第{i}段，用于测试语义空行分段切卷。" * 12 for i in range(1, 120))
    md3 = _write_basic_book(root3, "B0003", "无标题长书", {"无标题长书.md": f"# 无标题长书\n\n{paras}\n"})
    res3 = normalize_book_md(md3)
    assert res3["did_split"] is True
    assert res3["stats"]["chapters_after"] >= 2
    assert _run_verify(md3).returncode == 0


def test_html_table_entity_pandoc_and_code_fence_protection(tmp_path: Path):
    root = tmp_path / "ws"
    raw = (
        "# 第一章\n\n"
        "<div><span>正文包含&amp;实体与&ldquo;引号&rdquo;<sup>1</sup>"
        "和属性{#p1 .calibre3}与私用区\ue210。</span></div>\n\n"
        '<table border="1">'
        '<tr><th colspan="2">汇总指标</th><th>备注</th></tr>'
        '<tr><td style="color:red">2024</td><td>增长 12%</td><td><img src="images/tbl.png" alt="表图"></td></tr>'
        "</table>\n\n"
        "```html\n"
        '<div class="keep">代码围栏内的HTML不许清洗</div>\n'
        "```\n"
    )
    md_dir = _write_basic_book(root, "B0001", "清洗测试", {"01_第一章.md": raw}, images={"tbl.png": b"IMG"})
    res = normalize_book_md(md_dir)
    assert res["stats"]["html_tables_converted"] == 1
    assert res["stats"]["html_bytes_cleaned"] > 0

    out = (md_dir / "01_第一章.md").read_text(encoding="utf-8")
    assert "<table" not in out.lower()
    assert "<td" not in out.lower()
    assert "<div>" not in out
    assert ".calibre3" not in out
    assert "\ue210" not in out
    assert "正文包含&实体与“引号”1和属性与私用区。" in out
    assert "| 汇总指标 |  | 备注 |" in out
    assert "| 2024 | 增长 12% | ![表图](images/tbl.png) |" in out
    assert '<div class="keep">代码围栏内的HTML不许清洗</div>' in out
    assert _run_verify(md_dir).returncode == 0


def test_page_denoising_stitching_and_poetry_protection(tmp_path: Path):
    root = tmp_path / "ws"
    raw = (
        "# 第一章\n\n"
        "反复页眉\n\n1\n\n"
        "这是被页码打断的一句未完的\n\n"
        "反复页眉\n\n— 2 —\n\n"
        "话在这里接着写完。\n\n"
        "反复页眉\n\n第 3 页\n\n"
        "独立完整句结束。\n\n"
        "反复页眉\n\n4\n\n"
        "版权声明\n\n"
        "- 5 -\n\n"
        "下面是一首短诗不可误缝：\n\n"
        "蒲公英\n\n"
        "中县的天空\n\n"
        "飘满了蒲公英\n\n"
        "飘着的，是命运\n"
    )
    md_dir = _write_basic_book(root, "B0001", "去噪测试", {"01_第一章.md": raw})
    res = normalize_book_md(md_dir)
    assert res["stats"]["page_lines_removed"] >= 4
    assert res["stats"]["header_lines_removed"] >= 4
    assert res["stats"]["sentences_stitched"] >= 1

    out = (md_dir / "01_第一章.md").read_text(encoding="utf-8")
    assert "这是被页码打断的一句未完的话在这里接着写完。" in out
    assert "— 2 —" not in out
    assert "第 3 页" not in out
    assert "反复页眉" not in out
    # 诗歌短行和短标题未被误缝
    assert "蒲公英\n\n中县的天空\n\n飘满了蒲公英\n\n飘着的，是命运" in out
    assert "版权声明\n\n下面是一首短诗不可误缝：" in out


def test_footnotes_normalization_and_orphan_icon_cleanup(tmp_path: Path):
    root = tmp_path / "ws"
    ch1 = (
        "# 第一章\n\n"
        "第一处通用图标^![note](images/note.png)^，第二处带号图标[![](images/note_3.png)]()，"
        "第三处双括号[[2]]()，第四处跨章[[18]]()。\n\n"
        "正文插图保留：![](images/fig.png)\n\n"
        "- [1] 第一条尾注。\n"
        "- [2] 第二条尾注。\n"
        "- [3] 第三条尾注。\n\n"
        "[[19]]() 跨章孤立双括号定义正文。\n"
    )
    md_dir = _write_basic_book(
        root,
        "B0001",
        "脚注测试",
        {"01_第一章.md": ch1},
        images={"note.png": b"ICON", "note_3.png": b"ICON3", "fig.png": b"FIG"},
    )
    res = normalize_book_md(md_dir)
    assert res["stats"]["footnotes_converted"] == 3
    assert res["stats"]["orphan_icons_removed"] == 2

    out = (md_dir / "01_第一章.md").read_text(encoding="utf-8")
    assert "note.png" not in out
    assert "note_3.png" not in out
    assert "[[2]]()" not in out
    assert "[[18]]()" not in out
    assert "[[19]]()" not in out
    assert "[^1]" in out
    assert "[^3]" in out
    assert "[^2]" in out
    assert "[^18]" in out
    assert "[^1]: 第一条尾注。" in out
    assert "[^2]: 第二条尾注。" in out
    assert "[^3]: 第三条尾注。" in out
    assert "[^19]: 跨章孤立双括号定义正文。" in out
    # 图标已删除，正文插图完好保留
    assert not (md_dir / "images" / "note.png").exists()
    assert not (md_dir / "images" / "note_3.png").exists()
    assert (md_dir / "images" / "fig.png").is_file()
    assert _run_verify(md_dir).returncode == 0


def test_roles_front_matter_reading_fold_and_idempotency(tmp_path: Path):
    root = tmp_path / "ws"
    md_dir = _write_basic_book(
        root,
        "B0001",
        "水章折叠测试",
        {
            "01_封面.md": "---\ntitle: 封面\n---\n\n# 封面\n\n![](images/cover.png)\n",
            "02_版权页.md": "---\ntitle: 版权页\n---\n\n# 版权页\n\n版权所有，翻印必究。\n",
            "03_第一章 导论.md": "---\ntitle: 第一章 导论\n---\n\n# 第一章 导论\n\n正式正文段落在这里。\n",
            "04_注释.md": "---\ntitle: 注释\n---\n\n# 注释\n\n[1] 末尾注释。\n",
        },
        images={"cover.png": b"COV"},
    )
    backup_dir = tmp_path / "backup"
    res1 = normalize_book_md(md_dir, backup_dir=backup_dir)
    assert res1["changed"] is True
    assert (backup_dir / "B0001" / "01_封面.md").is_file()
    assert res1["chapter_roles"] == {
        "01_封面.md": "front_back",
        "02_版权页.md": "front_back",
        "03_第一章 导论.md": "body",
        "04_注释.md": "notes",
    }

    # 非 body 写入 YAML front matter，body 不写多余 chapter_role
    assert "chapter_role: front_back" in (md_dir / "01_封面.md").read_text(encoding="utf-8")
    assert "chapter_role: notes" in (md_dir / "04_注释.md").read_text(encoding="utf-8")
    assert "chapter_role:" not in (md_dir / "03_第一章 导论.md").read_text(encoding="utf-8")
    assert read_chapter_role(md_dir / "01_封面.md") == "front_back"
    assert read_chapter_role(md_dir / "04_注释.md") == "notes"
    assert read_chapter_role(md_dir / "03_第一章 导论.md") == "body"

    man = json.loads((md_dir / ".manifest.json").read_text(encoding="utf-8"))
    assert man["canonical_version"] == CANONICAL_VERSION
    assert "normalized_at" in man
    assert man["chapter_roles"] == res1["chapter_roles"]

    # 严格幂等：二次运行 0 变更，文件 mtime 与内容完全一致
    mtimes_before = {p.name: p.stat().st_mtime_ns for p in md_dir.iterdir() if p.is_file()}
    res2 = normalize_book_md(md_dir)
    assert res2["changed"] is False
    mtimes_after = {p.name: p.stat().st_mtime_ns for p in md_dir.iterdir() if p.is_file()}
    assert mtimes_before == mtimes_after

    # 伴读服务透出 chapter_role，检索自动过滤水章
    Library(root)
    svc = ReadingService(root, annotate=False)
    book_payload = svc.book("B0001")
    roles_out = {c["file"]: c["chapter_role"] for c in book_payload["chapters"]}
    assert roles_out["01_封面.md"] == "front_back"
    assert roles_out["03_第一章 导论.md"] == "body"
    assert roles_out["04_注释.md"] == "notes"

    db_path = root / "build" / "检索" / "index.db"
    con = bpi.build(str(db_path), root=str(root))
    try:
        indexed = {r[0] for r in con.execute("SELECT path FROM para").fetchall()}
        assert indexed == {"books/B0001/md/03_第一章 导论.md"}
    finally:
        con.close()


def test_backup_failure_aborts_without_modifying_source(tmp_path: Path):
    root = tmp_path / "ws"
    raw = "# 第一章\n\n<div>脏HTML</div>\n"
    md_dir = _write_basic_book(root, "B0001", "备份失败中止", {"01_第一章.md": raw})

    # 预先占用备份目录，触发 FileExistsError
    bad_backup = tmp_path / "bk"
    (bad_backup / "B0001").mkdir(parents=True)

    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "bookcorpus",
            "normalize",
            "--md-dir",
            str(md_dir),
            "--backup-dir",
            str(bad_backup),
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["failed"] == 1
    # 原 md/ 绝未被修改
    assert (md_dir / "01_第一章.md").read_text(encoding="utf-8") == raw
    assert not (md_dir / ".manifest.json").exists()

    # --dry-run 同样 0 修改
    r_dry = subprocess.run(
        [sys.executable, "-m", "bookcorpus", "normalize", "--md-dir", str(md_dir), "--dry-run", "--json"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert r_dry.returncode == 0
    assert json.loads(r_dry.stdout)["changed"] == 1
    assert (md_dir / "01_第一章.md").read_text(encoding="utf-8") == raw


def test_reading_frontend_contains_front_back_fold_logic():
    book_html = (REPO / "apps" / "reading" / "web" / "book.html").read_text(encoding="utf-8")
    chapter_js = (REPO / "apps" / "reading" / "web" / "assets" / "chapter.js").read_text(encoding="utf-8")
    assert "ch-front-back-fold" in book_html
    assert "chapter_role === 'front_back'" in book_html
    assert "toc-fold-group" in chapter_js
    assert "c.chapter_role === 'front_back'" in chapter_js


def test_pdf_text_to_md_removes_headers_pages_and_emits_headings(tmp_path: Path):
    import sys as _sys
    import types

    # 无论当前 venv 是否安装 pymupdf，先用轻量 FakeDoc 验证 layout/标题/页眉去噪逻辑，
    # 若 ~/书库/工作区/.venv-pdf 可用再额外用真 fitz PDF 端到端跑一遍。
    class _FakePage:
        def __init__(self, pg: int):
            self.number = pg - 1
            self.rect = types.SimpleNamespace(width=595.0, height=842.0)
            self._blocks = [
                {
                    "type": 0,
                    "bbox": (50, 30, 300, 48),
                    "lines": [{"spans": [{"text": "RUNNING HEADER TITLE", "size": 9.0}]}],
                },
                {"type": 0, "bbox": (270, 790, 320, 808), "lines": [{"spans": [{"text": f"- {pg} -", "size": 9.0}]}]},
            ]
            if pg == 1:
                self._blocks.append(
                    {
                        "type": 0,
                        "bbox": (50, 110, 300, 134),
                        "lines": [{"spans": [{"text": "Chapter 1 Introduction", "size": 18.0}]}],
                    }
                )
            elif pg == 3:
                self._blocks.append(
                    {
                        "type": 0,
                        "bbox": (50, 110, 300, 130),
                        "lines": [{"spans": [{"text": "1.1 Subtopic Overview", "size": 14.0}]}],
                    }
                )
            self._blocks.extend(
                [
                    {
                        "type": 0,
                        "bbox": (50, 155, 250, 173),
                        "lines": [{"spans": [{"text": f"图 {pg} 系统结构示意图", "size": 13.0}]}],
                    },
                    {
                        "type": 0,
                        "bbox": (50, 210, 500, 230),
                        "lines": [
                            {
                                "spans": [
                                    {
                                        "text": f"Body paragraph content on page {pg} discussing fiscal institutions.",
                                        "size": 11.0,
                                    }
                                ]
                            }
                        ],
                    },
                ]
            )

        def get_text(self, mode="text"):
            return {"blocks": self._blocks}

        def get_drawings(self):
            return []

    class _FakeDoc:
        def __init__(self):
            self._pages = [_FakePage(p) for p in range(1, 5)]
            self.page_count = len(self._pages)

        def __iter__(self):
            return iter(self._pages)

        def __getitem__(self, idx):
            return self._pages[idx]

    fake_fitz = types.ModuleType("pymupdf")
    _sys.modules.setdefault("pymupdf", fake_fitz)
    from bookcorpus.scripts import pdf_text_to_md as PT

    pages, _dropped, _used = PT.extract(_FakeDoc(), str(tmp_path / "out"))
    joined = "\n\n".join("\n\n".join(p["parts"]) for p in pages)
    assert "RUNNING HEADER TITLE" not in joined
    assert "- 1 -" not in joined
    assert "- 4 -" not in joined
    assert "## Chapter 1 Introduction" in joined
    assert "### 1.1 Subtopic Overview" in joined
    assert "## 图 1" not in joined
    assert "### 图 1" not in joined
    assert "图 1 系统结构示意图" in joined

    venv_py = Path.home() / "书库" / "工作区" / ".venv-pdf" / "bin" / "python"
    if venv_py.is_file():
        probe_code = (
            "import pymupdf, subprocess, sys; "
            f"doc = pymupdf.open(); "
            "[(p := doc.new_page(), p.insert_text((50, 40), 'HDR', fontsize=9), "
            "p.insert_text((280, 800), str(i), fontsize=9), "
            "p.insert_text((50, 120), 'Chapter 1 Intro' if i==1 else '1.1 Sub', fontsize=18 if i==1 else 14), "
            "p.insert_text((50, 170), f'图 {i} 示意', fontsize=13), "
            "p.insert_text((50, 220), f'Body text page {i}.', fontsize=11)) for i in range(1, 5)]; "
            f"doc.save({str(tmp_path / 'real.pdf')!r}); doc.close()"
        )
        subprocess.run([str(venv_py), "-c", probe_code], check=True)
        out_dir = tmp_path / "real_out"
        proc = subprocess.run(
            [
                str(venv_py),
                str(REPO / "src" / "bookcorpus" / "scripts" / "pdf_text_to_md.py"),
                "--pdf",
                str(tmp_path / "real.pdf"),
                "--out",
                str(out_dir),
                "--uid",
                "B9001",
                "--title",
                "Sample Book",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        real_md = (out_dir / "Sample Book.md").read_text(encoding="utf-8")
        assert "HDR" not in real_md
        assert "## Chapter 1 Intro" in real_md
        assert "### 1.1 Sub" in real_md
        assert "## 图 1" not in real_md


def test_pdf_ocr_to_md_defaults_to_auto_chapters_and_keeps_single_compat(tmp_path: Path):
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    for i in range(1, 5):
        head = f"## 第{i}章 测试章\n\n" if i in (1, 3) else ""
        (pages_dir / f"doc_{i:04d}.md").write_text(head + ("正文内容测试句子。" * 500) + "\n", encoding="utf-8")

    # 默认不传 --structure：走 auto 并切章
    out_auto = tmp_path / "out_auto"
    proc_auto = subprocess.run(
        [
            sys.executable,
            str(REPO / "src" / "bookcorpus" / "scripts" / "pdf_ocr_to_md.py"),
            "--pages-dir",
            str(pages_dir),
            "--out",
            str(out_auto),
            "--uid",
            "B9002",
            "--title",
            "OCR测试书",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_auto.returncode == 0, proc_auto.stdout + proc_auto.stderr
    man_auto = json.loads((out_auto / ".manifest.json").read_text(encoding="utf-8"))
    assert man_auto["structure"] == "chapters"
    assert man_auto["chapters"] == 2
    assert man_auto["single_file"] is False

    # 显式 --structure single 兼容
    out_single = tmp_path / "out_single"
    proc_single = subprocess.run(
        [
            sys.executable,
            str(REPO / "src" / "bookcorpus" / "scripts" / "pdf_ocr_to_md.py"),
            "--pages-dir",
            str(pages_dir),
            "--out",
            str(out_single),
            "--uid",
            "B9002",
            "--title",
            "OCR测试书",
            "--structure",
            "single",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_single.returncode == 0
    man_single = json.loads((out_single / ".manifest.json").read_text(encoding="utf-8"))
    assert man_single["structure"] == "single"
    assert man_single["chapters"] == 1


def test_batch_pipelines_invoke_audit_then_normalize_then_verify(tmp_path: Path, monkeypatch):
    # 验证 pdf_md_batch.run_one_text 执行顺序：assemble -> audit_pdf -> normalize_stage -> verify
    events: list[str] = []
    root = tmp_path / "ws"
    src = root / "books" / "B0001" / "source" / "a.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"%PDF-1.4")
    sha = pdf_md_batch.L.sha256_file(str(src))

    monkeypatch.setattr(pdf_md_batch.L, "ROOT", str(root))
    monkeypatch.setattr(pdf_md_batch.L, "PATHS", pdf_md_batch.L.Layout(str(root)))

    def fake_assemble(ctx, row, s, stage, log, result):
        events.append("assemble")
        Path(stage).mkdir(parents=True, exist_ok=True)
        (Path(stage) / "a.md").write_text("# 标题\n\n<div>正文</div>\n", encoding="utf-8")
        (Path(stage) / ".manifest.json").write_text("{}", encoding="utf-8")
        return 0

    def fake_audit(ctx, s, stage, log):
        events.append("audit_pdf")
        return 0, {"ratio": 0.99, "anchors": 0.98, "md_chars": 100}

    orig_norm = pdf_md_batch.normalize_stage

    def spy_norm(ctx, stage, log, result):
        events.append("normalize")
        return orig_norm(ctx, stage, log, result)

    def fake_verify(ctx, stage, log):
        events.append("verify")
        return 0, {"chapters": 1, "chars": 100, "images": 0, "problems": 0}

    monkeypatch.setattr(pdf_md_batch, "assemble_text", fake_assemble)
    monkeypatch.setattr(pdf_md_batch, "audit_pdf", fake_audit)
    monkeypatch.setattr(pdf_md_batch, "normalize_stage", spy_norm)
    monkeypatch.setattr(pdf_md_batch, "verify", fake_verify)

    batch = root / "工作区" / "批次" / "b1"
    ctx = {
        "batch": str(batch),
        "batch_name": "b1",
        "out": str(batch / "out"),
        "tmp": str(batch / "tmp"),
        "logs": str(batch / "logs"),
        "work": str(batch / "work"),
        "review": str(batch / "review"),
        "land": True,
        "force": True,
        "replace": False,
        "route": "text",
        "stamp": "t1",
        "tool_version": "tv1",
    }
    row = {
        "uid": "B0001",
        "title": "书",
        "format": "pdf",
        "pages": "1",
        "src": "books/B0001/source/a.pdf",
        "sha256": sha,
    }
    res = pdf_md_batch.run_one_text(row, ctx)
    assert res["status"] == "landed"
    assert events == ["assemble", "audit_pdf", "normalize", "verify"]
    landed_man = json.loads((root / "books" / "B0001" / "md" / ".manifest.json").read_text(encoding="utf-8"))
    assert landed_man["canonical_version"] == CANONICAL_VERSION
    assert "<div>" not in (root / "books" / "B0001" / "md" / "a.md").read_text(encoding="utf-8")

    # 确认 split_md_batch 与 docs_to_md 模块也已接入 normalize_book_md
    assert hasattr(split_md_batch, "normalize_book_md")
    assert hasattr(docs_to_md, "normalize_book_md")
    assert pdf_ocr_to_md is not None
