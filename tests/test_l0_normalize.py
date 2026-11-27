"""Phase 2 L0 规范化器、上游转档改进、入库管线集成与伴读水章折叠全套单测。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bookcorpus.md import (
    CANONICAL_VERSION,
    normalize_book_md,
    parse_markdown,
    read_chapter_role,
)
from bookcorpus.md import (
    normalize as norm_mod,
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


def test_headingless_pure_notes_chapter_no_unbound_local(tmp_path: Path):
    """无标题纯 [[N]]() 注释章在循环全跳过时不应触发 UnboundLocalError。"""
    root = tmp_path / "ws"
    md_dir = _write_basic_book(
        root,
        "B0001",
        "纯注测试",
        {
            "01_正文.md": "# 正文\n\n一段正文内容。\n",
            "02_注释.md": (
                "[[1]]() 第一条纯注解释。\n\n[[2]]() 第二条含内嵌[[4]]()引用。\n\n[[3]]() 第三条纯注说明。\n"
            ),
        },
    )
    res = normalize_book_md(md_dir)
    assert res["chapter_roles"]["02_注释.md"] == "notes"
    note_out = (md_dir / "02_注释.md").read_text(encoding="utf-8")
    assert "chapter_role: notes" in note_out
    assert "[^1]: 第一条纯注解释。" in note_out
    assert "[^2]: 第二条含内嵌[^4]引用。" in note_out
    assert "[^3]: 第三条纯注说明。" in note_out
    assert "[[1]]()" not in note_out


def test_cross_file_md_footnote_links_preserved(tmp_path: Path):
    """带 .md 的跨文件脚注链接必须原样保留，空/页内锚点才转为 [^N]。"""
    root = tmp_path / "ws"
    ch1 = (
        "# 第一章\n\n"
        "保留带锚点[[1]](09_notes.md#fn1)、保留无锚点[[2]](09_notes.md)、"
        "保留尖括号[[3]](<09_notes.md#fn3>)，仅转换空目标[[4]]()和纯锚点[[5]](#local)。\n\n"
        "[[4]]() 第四条本页注。\n"
    )
    notes_ch = "# 注释\n\n[^1]: 跨文件第一条。\n"
    md_dir = _write_basic_book(
        root,
        "B0001",
        "跨文件注",
        {"01_第一章.md": ch1, "09_notes.md": notes_ch},
    )
    normalize_book_md(md_dir)
    out = (md_dir / "01_第一章.md").read_text(encoding="utf-8")
    assert "[[1]](09_notes.md#fn1)" in out
    assert "[[2]](09_notes.md)" in out
    assert "[[3]](<09_notes.md#fn3>)" in out
    assert "[[4]]()" not in out
    assert "[[5]](#local)" not in out
    assert "[^4]" in out
    assert "[^5]" in out
    assert "[^4]: 第四条本页注。" in out


def test_split_writes_new_chapters_before_unlinking_old(tmp_path: Path, monkeypatch):
    """did_split=True 时若写新章中途失败，旧章不得被提前删除。"""
    root = tmp_path / "ws"
    ch1 = "## 第一节\n\n" + ("这是足够长的一段测试文字。" * 60 + "\n\n") * 22
    ch2 = "## 第二节\n\n" + ("这是第二节同样足够长的文字。" * 60 + "\n\n") * 22
    md_dir = _write_basic_book(
        root,
        "B0001",
        "原子拆章",
        {"原子拆章.md": f"# 原子拆章\n\n{ch1}{ch2}"},
    )
    orig_write = norm_mod._write_text_atomic
    calls: list[str] = []

    def flaky_write(dest: Path, content: str) -> None:
        calls.append(dest.name)
        if len(calls) == 2:
            raise OSError("disk full")
        orig_write(dest, content)

    monkeypatch.setattr(norm_mod, "_write_text_atomic", flaky_write)
    with pytest.raises(OSError, match="disk full"):
        norm_mod.normalize_book_md(md_dir)
    assert (md_dir / "原子拆章.md").is_file()


def test_image_refs_title_dot_slash_and_unclosed_html_table(tmp_path: Path):
    """图片引用带 ./ 与 title 不误判孤儿；未闭合残缺 table 标签不粘连单元格。"""
    refs = norm_mod._referenced_images_in_texts(
        [
            "正文插图 ![](./images/fig1.png \"图 1\") 与 链接插图 [见图](./images/fig2.png '图 2')。\n"
            '尖括号带标题 ![](<./images/fig3.jpg> "图 3")。\n'
        ]
    )
    assert refs == {"images/fig1.png", "images/fig2.png", "images/fig3.jpg"}

    root = tmp_path / "ws"
    filler = "这是一个包含插图与残缺 HTML 表格的完整正文测试章节。" * 10
    body = (
        "---\ntitle: 第一章\nchapter: '01'\n---\n\n"
        "# 第一章\n\n"
        f"{filler}\n\n"
        '正文插图 ![](./images/fig1.png "图 1") 与 常规插图 ![](images/fig0.png)。\n\n'
        "<table><tr><td>甲单元格</td><th>乙表头</th></tr><tr><td>丙数据</td><td>丁结尾\n"
    )
    md_dir = _write_basic_book(
        root,
        "B0001",
        "卫生边界",
        {"README.md": "# 卫生边界\n", "01_第一章.md": body},
        images={"fig0.png": b"IMG0", "fig1.png": b"IMG1", "note.png": b"ICON"},
    )
    res = normalize_book_md(md_dir)
    # ./images/fig1.png 带 title 被识别为正文插图（非孤儿），未引用的小图标 note.png 正常清理
    assert res["stats"]["orphan_icons_removed"] == 1
    assert (md_dir / "images" / "fig0.png").is_file()
    assert (md_dir / "images" / "fig1.png").is_file()
    assert not (md_dir / "images" / "note.png").exists()
    out = (md_dir / "01_第一章.md").read_text(encoding="utf-8")
    assert "甲单元格 乙表头" in out
    assert "丙数据 丁结尾" in out
    assert "甲单元格乙表头" not in out


def test_l0_protects_math_formulas_and_strict_html_tags(tmp_path: Path):
    """严格单行 HTML 正则与 LaTeX 数学保护：绝不把 <P(Ut+2)/<i*/<U(X^{a})>/<b $$ 跨行吞字。"""
    root = tmp_path / "ws"
    raw = (
        "# 第一章 模型推导\n\n"
        "约束条件 <P(Ut+2) 推导中出现 <i* 和 <U(X^{a}) 以及 <b $$\n\n"
        "跨行后面的正常文本不能被吞掉，包含 <b x >、</i + l) 与 <I see. I wonder.>。\n\n"
        "行内数学 $ F'(K) > 0 $ 与 $ x < y &amp; z > 0 $ 保持原样。\n\n"
        "$$ \\begin{array}{l} a < b & c > d \\end{array} $$\n\n"
        '<div style="text-align: center;">真实居中标签应剥除</div>\n\n'
        '<table width="600"border="0"><tr><td>指标</td><td>数值</td></tr></table>\n\n'
        "花费 $100 与 <b>$200</b> 的真实标签仍应剥除。\n"
    )
    md_dir = _write_basic_book(root, "B0001", "公式保护", {"01_第一章.md": raw})
    res = normalize_book_md(md_dir)
    assert res["changed"] is True
    out = (md_dir / "01_第一章.md").read_text(encoding="utf-8")
    for token in (
        "<P(Ut+2)",
        "<i*",
        "<U(X^{a})",
        "<b $$",
        "跨行后面的正常文本不能被吞掉",
        "<b x >",
        "</i + l)",
        "<I see. I wonder.>",
        "$ F'(K) > 0 $",
        "$ x < y &amp; z > 0 $",
        "$$ \\begin{array}{l} a < b & c > d \\end{array} $$",
        "真实居中标签应剥除",
        "| 指标 | 数值 |",
        "$100 与 $200",
    ):
        assert token in out
    assert "<div" not in out
    assert "<b>" not in out
    assert _run_verify(md_dir).returncode == 0


def test_repeated_headers_spare_math_connectors_years_and_refrains(tmp_path: Path):
    """收紧页眉判定：绝不误删数学引导词、诗集年份、诗行复沓与小说短对话，同时保留真实页眉/水印清洗。"""
    root = tmp_path / "ws"
    connectors = "\n\n".join(
        f"Eq.{i} holds\n\nand\n\nwhere\n\nor\n\nHence,\n\nTherefore,\n\n"
        "subject to\n\nLet\n\nwe get\n\ns.t.\n\nNo star: normal"
        for i in range(1, 8)
    )
    refrains = "\n\n".join("十八世纪某月某日" for _ in range(7))
    years_and_watermarks = "\n\n".join(
        "1983年\n\n1984年\n\n1999年5月\n\n'Yes,' said Jane.\n\n"
        "于坚集诗歌\n\nWWW.FANS.COM.CN\n\n最专业的时尚社区（普通LOGO版）"
        for _ in range(10)
    )
    raw = f"# 第一章\n\n{connectors}\n\n{refrains}\n\n{years_and_watermarks}\n"
    md_dir = _write_basic_book(root, "B0001", "页眉护栏", {"01_第一章.md": raw})
    res = normalize_book_md(md_dir)
    assert res["stats"]["header_lines_removed"] >= 20
    out = (md_dir / "01_第一章.md").read_text(encoding="utf-8")
    for keep in (
        "and",
        "where",
        "or",
        "Hence,",
        "Therefore,",
        "subject to",
        "Let",
        "we get",
        "s.t.",
        "No star: normal",
        "1983年",
        "1984年",
        "1999年5月",
        "十八世纪某月某日",
        "'Yes,' said Jane.",
    ):
        assert keep in out
    assert "于坚集诗歌" not in out
    assert "WWW.FANS.COM.CN" not in out
    assert "最专业的时尚社区（普通LOGO版）" not in out


def test_preserves_ebook_to_md_fidelity_comments_for_duplicate_chapters(tmp_path: Path):
    """重复章保真注释不得被 HTML 注释清洗或页眉去噪误删，保证 verify_output 通过。"""
    root = tmp_path / "ws"
    chapters: dict[str, str] = {}
    for pair_idx in range(1, 13):
        base_txt = f"# 第{pair_idx}节\n\n第{pair_idx}节独立正文内容，确保长度足够通过校验。" * 3 + "\n"
        chapters[f"{pair_idx:02d}_节{pair_idx}.md"] = base_txt
        chapters[f"{pair_idx + 12:02d}_节{pair_idx}.md"] = (
            base_txt + "\n<!-- 源 EPUB 重复收录相同正文；为保真保留此副本。 -->\n"
        )
    md_dir = _write_basic_book(root, "B0001", "保真注释", chapters)
    normalize_book_md(md_dir)
    for pair_idx in range(1, 13):
        txt = (md_dir / f"{pair_idx + 12:02d}_节{pair_idx}.md").read_text(encoding="utf-8")
        assert "<!-- 源 EPUB 重复收录相同正文；为保真保留此副本。 -->" in txt
    vp = _run_verify(md_dir)
    assert vp.returncode == 0, vp.stdout + vp.stderr


def test_text_retention_guard_raises_without_modifying_files(tmp_path: Path, monkeypatch):
    """正文留存率异常骤降时触发 TextRetentionGuardError，并保证原盘零修改。"""
    root = tmp_path / "ws"
    orig = "# 第一章\n\n" + ("这是完整的学术正文句子，绝不允许被清洗步骤静默吞掉。" * 40) + "\n"
    md_dir = _write_basic_book(root, "B0001", "熔断测试", {"01_第一章.md": orig})

    monkeypatch.setattr(norm_mod, "clean_html_and_noise", lambda _txt, _st: "# 第一章\n\n被吞字。\n")
    with pytest.raises(norm_mod.TextRetentionGuardError):
        norm_mod.normalize_book_md(md_dir)
    assert (md_dir / "01_第一章.md").read_text(encoding="utf-8") == orig


def test_classify_chapter_role_spares_body_in_front_back_and_toc_titles():
    """前置页有正文、封面/后折页超长、目录后黏连正文或长诗均救回为 body，真水章与真长目录保持 front_back。"""
    from bookcorpus.md import classify_chapter_role

    # B0980 / B1106 / B0750 风格：前置页带实质正文 -> body；纯图或 <50 字占位前置页 -> front_back
    assert (
        classify_chapter_role(
            "前置页 9",
            "# 前置页 9\n\n" + ("这是一段真正的小说正文叙事，包含完整对话与描写。" * 60),
            "03_前置页 9.md",
            book_title="偶像失格",
        )
        == "body"
    )
    assert classify_chapter_role("前置页 2", "# 前置页 2\n\n![](images/p02.png)\n", "02_前置页 2.md") == "front_back"
    assert classify_chapter_role("开始", "# 开始\n\n![](images/p01.png)\n", "01_开始.md") == "front_back"

    # B1093 风格：封面标题下黏着长正文 -> body；真封面/真版权页 <1200 字 -> front_back
    assert (
        classify_chapter_role(
            "封面",
            "# 封面\n\n" + ("这是黏在封面后面的长篇小说正文内容。" * 100),
            "01_封面.md",
            book_title="美味料理",
        )
        == "body"
    )
    assert (
        classify_chapter_role(
            "后折页",
            "# 后折页\n\n" + ("老秦是报业老江湖，懂得怎么给写东西的人煽风点火。" * 60),
            "48_后折页.md",
        )
        == "body"
    )
    assert (
        classify_chapter_role(
            "版权页",
            "# 版权页\n\n图书在版编目（CIP）数据\nISBN 978-7-115-42229-3\n版权所有，侵权必究。\n",
            "05_版权页.md",
        )
        == "front_back"
    )

    # B1514 / B0536 风格：>=2000 字纯目录保持 front_back
    pure_toc = "\n".join(f"第{i}章 章节标题测试 …… {i * 12}" for i in range(1, 140))
    assert classify_chapter_role("目录", f"# 目录\n\n{pure_toc}\n", "02_目录.md") == "front_back"

    # B1882 / B1515 / B1540 风格：目录后黏连全书正文或长诗（哪怕硬换行短行占多数）-> body
    glued_prose = (
        "\n".join(f"第{i}章 绪论……{i}" for i in range(1, 15))
        + "\n\n"
        + ("这是紧跟在目录后面的全书正文段落，包含完整的标点符号与连续论述。\n" * 60)
    )
    assert classify_chapter_role("目录", f"# 目录\n\n{glued_prose}\n", "07_目录.md") == "body"

    glued_poetry = (
        "\n".join(f"第{i}首 诗题 …… {i * 3}" for i in range(1, 20))
        + "\n\n"
        + ("多年前我曾亲眼见过\n至今仍让我感到震撼\n那是一大片墓碑\n在开阔的天空下闪闪发光\n" * 70)
    )
    assert classify_chapter_role("目录", f"# 目录\n\n{glued_poetry}\n", "02_目录.md") == "body"


def test_reclassify_roles_only_preserves_body_and_is_idempotent(tmp_path: Path):
    """reclassify_roles_only 仅无损重标 YAML front matter 与 .manifest.json，不改正文、不拆章、二次 0 变更。"""
    root = tmp_path / "ws"
    dirty_body = "<div>保留未清洗HTML与正文空白。</div>\n\n" + ("这是一段被误标为前置页的长正文叙事。" * 100) + "\n"
    ch1_raw = (
        "---\n"
        'title: "前置页 4"\n'
        'author: "宇佐见铃"\n'
        "chapter: 1\n"
        "chapter_role: front_back\n"
        "---\n\n"
        "# 前置页 4\n\n"
        f"{dirty_body}"
    )
    ch2_raw = '---\ntitle: "封面"\nchapter: 2\n---\n\n# 封面\n\n![](images/cover.png)\n'
    md_dir = _write_basic_book(
        root,
        "B0980",
        "偶像失格",
        {"01_前置页 4.md": ch1_raw, "02_封面.md": ch2_raw},
        images={"cover.png": b"COV"},
    )
    (md_dir / ".manifest.json").write_text(
        json.dumps(
            {
                "uid": "B0980",
                "canonical_version": 1,
                "chapter_roles": {"01_前置页 4.md": "front_back", "02_封面.md": "front_back"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    res1 = normalize_book_md(md_dir, reclassify_roles_only=True)
    assert res1["changed"] is True
    assert res1["did_split"] is False
    assert res1["reclassify_roles_only"] is True
    assert res1["chapter_roles"] == {"01_前置页 4.md": "body", "02_封面.md": "front_back"}

    ch1_after = (md_dir / "01_前置页 4.md").read_text(encoding="utf-8")
    assert "chapter_role:" not in ch1_after
    # 正文逐字节原样保留，未触发 L0 HTML 清洗
    assert dirty_body in ch1_after
    assert (md_dir / "02_封面.md").read_text(encoding="utf-8").endswith("![](images/cover.png)\n")
    assert "chapter_role: front_back" in (md_dir / "02_封面.md").read_text(encoding="utf-8")

    man = json.loads((md_dir / ".manifest.json").read_text(encoding="utf-8"))
    assert man["chapter_roles"] == {"01_前置页 4.md": "body", "02_封面.md": "front_back"}
    assert man["canonical_version"] == 1

    # CLI --reclassify-roles --books 幂等：二次运行 changed=0 且 mtime 不变
    mtimes_before = {p.name: p.stat().st_mtime_ns for p in md_dir.iterdir() if p.is_file()}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bookcorpus",
            "normalize",
            "--root",
            str(root),
            "--books",
            "B0980",
            "--reclassify-roles",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["changed"] == 0
    assert payload["reclassify_roles_only"] is True
    mtimes_after = {p.name: p.stat().st_mtime_ns for p in md_dir.iterdir() if p.is_file()}
    assert mtimes_before == mtimes_after
