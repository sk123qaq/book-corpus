"""Phase 3 存量三批离线重洗编排器、pdf-text 源重抽、回滚守卫与 ops_status 覆盖统计单测。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from bookcorpus.md import CANONICAL_VERSION
from bookcorpus.scripts import normalize_corpus_batch as ncb
from bookcorpus.scripts import ops_status
from conftest import run_cli, write_ledger

REPO = Path(__file__).resolve().parents[1]


def _init_ws(root: Path) -> None:
    (root / "books" / "_收件箱").mkdir(parents=True, exist_ok=True)
    (root / "books" / "_冷宫").mkdir(parents=True, exist_ok=True)
    (root / "tables").mkdir(parents=True, exist_ok=True)
    (root / "build").mkdir(parents=True, exist_ok=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")


def _write_book_md(
    root: Path,
    uid: str,
    title: str,
    chapters: dict[str, str],
    *,
    manifest: dict | None = None,
    images: dict[str, bytes] | None = None,
    pdf_bytes: bytes | None = None,
) -> Path:
    bdir = root / "books" / uid
    md_dir = bdir / "md"
    md_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    if pdf_bytes is not None:
        src_dir = bdir / "source"
        src_dir.mkdir(parents=True, exist_ok=True)
        pdf_file = src_dir / f"{uid}.pdf"
        pdf_file.write_bytes(pdf_bytes)
        sources.append({"format": "pdf", "file": f"source/{uid}.pdf", "sha256": "deadbeef", "primary_src": 1})
    (bdir / "book.json").write_text(
        json.dumps(
            {
                "uid": uid,
                "title": title,
                "author": "作者甲",
                "subjects": ["人文社科", "历史"],
                "sources": sources,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for name, text in chapters.items():
        (md_dir / name).write_text(text, encoding="utf-8")
    if images:
        img_dir = md_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        for iname, blob in images.items():
            (img_dir / iname).write_bytes(blob)
    if manifest is not None:
        (md_dir / ".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return md_dir


def test_three_batch_classification_mutual_exclusion_and_idempotency(tmp_path: Path):
    ws = tmp_path / "ws"
    _init_ws(ws)

    # Batch 1a: 含 table 污染（较短）
    _write_book_md(
        ws,
        "B0001",
        "表格小书",
        {"01_第一章.md": "# 第一章\n\n<table><tr><td>甲</td><td>乙</td></tr></table>\n"},
        manifest={"uid": "B0001", "method": "paddle-ocr"},
    )
    # Batch 1b: 含大量 HTML 标签污染（降序应排在 B0001 前）
    _write_book_md(
        ws,
        "B0002",
        "HTML大书",
        {
            "01_第一章.md": "# 第一章\n\n"
            + "".join(f"<div class='calibre{i}'><span>正文句{i}</span></div>\n" for i in range(10))
        },
        manifest={"uid": "B0002", "method": "ebook-to-md"},
    )
    # Batch 2: 无 HTML、单文件、带 ##（含 pdf-text 带 ## 与 paddle-ocr）
    _write_book_md(
        ws,
        "B0003",
        "单章带二级标题",
        {
            "整本.md": "# 单章带二级标题\n\n## 第一节\n\n"
            + ("讨论制度细节。" * 100)
            + "\n\n## 第二节\n\n"
            + ("继续分析。" * 100)
        },
        manifest={"uid": "B0003", "method": "pdf-text"},
    )
    # Batch 3A: 无 ## 单章 pdf-text（即使超 24k 字也应进 3A 做源重抽，而不是进 Batch 2）
    _write_book_md(
        ws,
        "B0004",
        "无目录文本PDF",
        {"无目录文本PDF.md": "# 无目录文本PDF\n\n" + ("普通无标题段落。" * 4000)},
        manifest={"uid": "B0004", "method": "pdf-text"},
        pdf_bytes=b"%PDF-1.4 dummy",
    )
    # Batch 3B: 多章书含 [[N]]() 与 note 图标、尚未 canonical_version: 1
    _write_book_md(
        ws,
        "B0005",
        "多章脚注书",
        {
            "01_封面.md": "# 封面\n\n![](images/cover.png)\n",
            "02_正文.md": "# 正文\n\n参考这里![note](images/note_1.png)与[[2]]()。\n\n- [1] 注释一。\n- [2] 注释二。\n",
        },
        manifest={"uid": "B0005", "method": "ebook-to-md"},
        images={"cover.png": b"COV", "note_1.png": b"ICON"},
    )
    # 已规范化书（canonical_version=1）：默认跳过，--force 时重新纳管
    _write_book_md(
        ws,
        "B0006",
        "已规范化书",
        {"01_正文.md": "---\ntitle: 正文\n---\n\n# 正文\n\n已是规范化文本。\n"},
        manifest={"uid": "B0006", "method": "ebook-to-md", "canonical_version": 1},
    )
    # 代码围栏内 HTML 不应误判入 Batch 1
    _write_book_md(
        ws,
        "B0007",
        "代码围栏HTML书",
        {
            "01_第一章.md": "# 第一章\n\n```html\n<table><tr><td>代码示例</td></tr></table>\n```\n",
            "02_第二章.md": "# 第二章\n\n正常正文。\n",
        },
        manifest={"uid": "B0007", "method": "ebook-to-md"},
    )

    plan = ncb.scan_corpus(ws, force=False)
    assert plan["skipped_canonical"] == 1
    b1_uids = [x["uid"] for x in plan["batches"]["1"]]
    b2_uids = [x["uid"] for x in plan["batches"]["2"]]
    b3_items = plan["batches"]["3"]
    b3_uids = [x["uid"] for x in b3_items]

    assert b1_uids == ["B0002", "B0001"]  # HTML 字节降序
    assert b2_uids == ["B0003"]
    assert set(b3_uids) == {"B0004", "B0005", "B0007"}
    sub_by_uid = {x["uid"]: x["subroute"] for x in b3_items}
    assert sub_by_uid["B0004"] == "3A"
    assert sub_by_uid["B0005"] == "3B"
    assert sub_by_uid["B0007"] == "3B"

    # 严格互斥
    all_sets = [set(b1_uids), set(b2_uids), set(b3_uids)]
    assert len(all_sets[0] & all_sets[1]) == 0
    assert len(all_sets[0] & all_sets[2]) == 0
    assert len(all_sets[1] & all_sets[2]) == 0

    # --dry-run 不建写锁、不建批次目录
    dry = ncb.run_batch(ws, batch="all", dry_run=True)
    assert dry["dry_run"] is True
    assert dry["counts"]["total_pending"] == 6
    assert not (ws / "工作区" / "运维" / ".writer.lock").exists()
    assert not (ws / "工作区" / "批次").exists()

    # --force 包含已规范化书
    plan_force = ncb.scan_corpus(ws, force=True)
    assert plan_force["skipped_canonical"] == 0
    assert plan_force["counts"]["total_pending"] == 7


def test_backup_and_verify_failure_atomic_rollback(tmp_path: Path, monkeypatch):
    ws = tmp_path / "ws"
    _init_ws(ws)
    orig_ch = "# 第一章\n\n<table><tr><td>A</td><td>B</td></tr></table>\n"
    orig_man = {"uid": "B0001", "method": "paddle-ocr", "chapters": 1}
    md_dir = _write_book_md(
        ws,
        "B0001",
        "回滚测试",
        {"01_第一章.md": orig_ch},
        manifest=orig_man,
        images={"fig.png": b"FIGBYTES"},
    )
    # 使原 md 引用图，确保原目录合法
    orig_ch_with_img = f"{orig_ch}\n![](images/fig.png)\n"
    (md_dir / "01_第一章.md").write_text(orig_ch_with_img, encoding="utf-8")

    # 模拟 verify_output 返回非零
    monkeypatch.setattr(ncb, "_run_verify", lambda _md: (1, "simulated verify failure"))
    backup_root = tmp_path / "bk"
    summary = ncb.run_batch(ws, batch="1", backup_root=backup_root, workers=1)

    assert summary["rolled_back"] == 1
    assert summary["succeeded"] == 0
    assert summary["results"][0]["status"] == "rolled_back"
    assert summary["results"][0]["rolled_back"] is True

    # 原 md/ 逐字节恢复（含未写入 canonical_version）
    assert (md_dir / "01_第一章.md").read_text(encoding="utf-8") == orig_ch_with_img
    assert json.loads((md_dir / ".manifest.json").read_text(encoding="utf-8")) == orig_man
    assert (md_dir / "images" / "fig.png").read_bytes() == b"FIGBYTES"
    assert (backup_root / "batch1" / "B0001" / "01_第一章.md").is_file()


def test_pdf_text_3a_reextract_success_and_fallback_skip_policies(tmp_path: Path, monkeypatch):
    # 1. 成功重抽路径（模拟 pdf_text_to_md + pdf_audit_volume 在暂存目录写出带 ## 的新版 md）
    ws1 = tmp_path / "ws_ok"
    _init_ws(ws1)
    md1 = _write_book_md(
        ws1,
        "B0010",
        "文本PDF成功书",
        {"文本PDF成功书.md": "# 文本PDF成功书\n\n旧版单文件正文内容。\n"},
        manifest={"uid": "B0010", "method": "pdf-text"},
        pdf_bytes=b"%PDF-1.4 valid",
    )

    monkeypatch.setattr(ncb, "_find_pdf_python", lambda _root: sys.executable)

    def fake_subprocess_run(cmd, **_kwargs):
        exe_script = Path(cmd[1]).name
        if exe_script == "pdf_text_to_md.py":
            out_dir = Path(cmd[cmd.index("--out") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            sec1 = "## 第一节 背景\n\n" + ("新版抽取正文第一部分内容。" * 1200)
            sec2 = "## 第二节 分析\n\n" + ("新版抽取正文第二部分内容。" * 1200)
            (out_dir / "文本PDF成功书.md").write_text(
                f'---\ntitle: "文本PDF成功书"\n---\n\n# 文本PDF成功书\n\n{sec1}\n\n{sec2}\n',
                encoding="utf-8",
            )
            (out_dir / "README.md").write_text("# 文本PDF成功书\n", encoding="utf-8")
            (out_dir / ".manifest.json").write_text(
                json.dumps({"uid": "B0010", "method": "pdf-text", "source_sha256": "deadbeef"}),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        if exe_script == "pdf_audit_volume.py":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="   24,000 /    24,100  99.59%  1 md\n锚点 : 30/30 窗命中 100.00%\n",
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    orig_run = subprocess.run

    def dispatch_run(cmd, **kwargs):
        if len(cmd) >= 2 and Path(str(cmd[1])).name in ("pdf_text_to_md.py", "pdf_audit_volume.py"):
            return fake_subprocess_run(cmd, **kwargs)
        return orig_run(cmd, **kwargs)

    monkeypatch.setattr(ncb.subprocess, "run", dispatch_run)

    res_ok = ncb.run_batch(ws1, batch="3", backup_root=tmp_path / "bk1", workers=1)
    assert res_ok["succeeded"] == 1
    assert res_ok["reextracted"] == 1
    assert res_ok["results"][0]["subroute"] == "3A"
    man1 = json.loads((md1 / ".manifest.json").read_text(encoding="utf-8"))
    assert man1["canonical_version"] == CANONICAL_VERSION
    assert man1["audit_rc"] == 0
    assert man1["chapters"] >= 2
    assert (tmp_path / "bk1" / "batch3" / "B0010" / "文本PDF成功书.md").is_file()

    # 2. 源 PDF 缺失时：skip 策略不动原盘；fallback 策略安全回退为现盘 L0 规范化
    ws2 = tmp_path / "ws_fb"
    _init_ws(ws2)
    orig_body = "# 缺源PDF书\n\n— 1 —\n\n这是一段被页码打断的还没有写完的\n\n— 2 —\n\n话在这里接上。\n"
    md2 = _write_book_md(
        ws2,
        "B0011",
        "缺源PDF书",
        {"缺源PDF书.md": orig_body},
        manifest={"uid": "B0011", "method": "pdf-text"},
        pdf_bytes=None,
    )
    res_skip = ncb.run_batch(ws2, batch="3", pdf_reextract_policy="skip", backup_root=tmp_path / "bk_skip")
    assert res_skip["skipped"] == 1
    assert res_skip["results"][0]["fallback_reason"] == "source_pdf_missing"
    assert (md2 / "缺源PDF书.md").read_text(encoding="utf-8") == orig_body

    res_fb = ncb.run_batch(ws2, batch="3", pdf_reextract_policy="fallback", backup_root=tmp_path / "bk_fb")
    assert res_fb["succeeded"] == 1
    assert res_fb["results"][0]["subroute"] == "3A-fallback"
    assert res_fb["results"][0]["fallback_reason"] == "source_pdf_missing"
    cleaned_body = (md2 / "缺源PDF书.md").read_text(encoding="utf-8")
    assert "这是一段被页码打断的还没有写完的话在这里接上。" in cleaned_body
    assert json.loads((md2 / ".manifest.json").read_text(encoding="utf-8"))["canonical_version"] == CANONICAL_VERSION

    # 3. 源审计失败时安全回退且不破坏原盘
    ws3 = tmp_path / "ws_audit_fail"
    _init_ws(ws3)
    md3 = _write_book_md(
        ws3,
        "B0012",
        "审计失败PDF书",
        {"审计失败PDF书.md": "# 审计失败PDF书\n\n现盘完整正文。\n"},
        manifest={"uid": "B0012", "method": "pdf-text"},
        pdf_bytes=b"%PDF-1.4 bad-audit",
    )

    def dispatch_audit_fail(cmd, **kwargs):
        if len(cmd) >= 2 and Path(str(cmd[1])).name == "pdf_audit_volume.py":
            return subprocess.CompletedProcess(cmd, 1, stdout="500 / 10000 5.00%", stderr="")
        return dispatch_run(cmd, **kwargs)

    monkeypatch.setattr(ncb.subprocess, "run", dispatch_audit_fail)
    res_af = ncb.run_batch(ws3, batch="3", pdf_reextract_policy="fallback", backup_root=tmp_path / "bk_af")
    assert res_af["results"][0]["subroute"] == "3A-fallback"
    assert res_af["results"][0]["fallback_reason"] == "pdf_audit_failed"
    assert "现盘完整正文。" in (md3 / "审计失败PDF书.md").read_text(encoding="utf-8")


def test_resume_skips_succeeded_and_sync_degrades_gracefully(tmp_path: Path):
    ws = tmp_path / "ws"
    _init_ws(ws)
    _write_book_md(
        ws,
        "B0001",
        "甲书",
        {"01_第一章.md": "# 第一章\n\n<table><tr><td>甲</td></tr></table>\n"},
        manifest={"uid": "B0001", "method": "paddle-ocr"},
    )
    _write_book_md(
        ws,
        "B0002",
        "乙书",
        {"01_第一章.md": "# 第一章\n\n<table><tr><td>乙</td></tr></table>\n"},
        manifest={"uid": "B0002", "method": "paddle-ocr"},
    )
    write_ledger(ws, [("B0001", "人文社科", "甲书", ""), ("B0002", "人文社科", "乙书", "")])

    batch_dir = tmp_path / "norm_batch"
    # 首次只跑 1 本（--limit 1）
    r1 = ncb.run_batch(ws, batch="1", limit=1, backup_root=batch_dir, workers=1)
    assert r1["processed"] == 1
    assert (batch_dir / "plan.json").is_file()
    assert (batch_dir / "progress.jsonl").is_file()
    assert (batch_dir / "summary.json").is_file()

    # 强制 --force + --resume：应跳过已成功的 B0001，只跑 B0002，并跑 --sync（故意连不可达 embedding 端口）
    r2 = ncb.run_batch(
        ws,
        batch="1",
        force=True,
        resume=True,
        backup_root=batch_dir,
        workers=1,
        sync=True,
        embed_url="http://127.0.0.1:19999",
    )
    assert r2["resumed_skipped"] == 1
    assert r2["processed"] == 1
    assert r2["results"][0]["uid"] == "B0002"
    assert r2["sync"]["ok"] is True
    assert r2["sync"]["library"]["status"] == "succeeded"
    assert r2["sync"]["fts"]["status"] == "succeeded"
    assert r2["sync"]["embed"]["status"] == "skipped_unreachable"
    assert "book-corpus embed" in r2["sync"]["embed"]["rerun_cmd"]

    # 验证 library.db 与 index.db 已同步更新
    assert (ws / "build" / "library.db").is_file()
    con = sqlite3.connect(ws / "build" / "检索" / "index.db")
    try:
        books_indexed = {r[0] for r in con.execute("SELECT DISTINCT book FROM para").fetchall()}
        assert books_indexed == {"B0001", "B0002"}
    finally:
        con.close()


def test_sync_with_reachable_mock_embed_server(tmp_path: Path):
    ws = tmp_path / "ws"
    _init_ws(ws)
    _write_book_md(
        ws,
        "B0001",
        "向量联动书",
        {"01_第一章.md": "# 第一章\n\n<table><tr><td>指标</td></tr></table>\n"},
        manifest={"uid": "B0001", "method": "ebook-to-md"},
    )
    write_ledger(ws, [("B0001", "人文社科", "向量联动书", "")])

    class _MockEmbedHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            inputs = payload.get("input") or []
            data = [{"index": i, "embedding": [0.1] * 1024} for i in range(len(inputs))]
            body = json.dumps({"data": data, "usage": {"prompt_tokens": len(inputs) * 10}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), _MockEmbedHandler)
    port = srv.server_address[1]
    t = Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        res = ncb.run_batch(
            ws,
            batch="1",
            backup_root=tmp_path / "bk",
            workers=1,
            sync=True,
            embed_url=f"http://127.0.0.1:{port}",
        )
        assert res["succeeded"] == 1
        assert res["sync"]["ok"] is True
        assert res["sync"]["embed"]["status"] == "succeeded"
        assert (ws / "build" / "检索" / "embeddings" / "emb.db").is_file()
    finally:
        srv.shutdown()
        srv.server_close()


def test_ops_status_md_canonical_coverage_and_cli_entry(tmp_path: Path):
    ws = tmp_path / "ws"
    _init_ws(ws)
    _write_book_md(
        ws,
        "B0001",
        "已规范",
        {"01_第一章.md": "# 第一章\n\n规范正文。\n"},
        manifest={"uid": "B0001", "method": "ebook-to-md", "canonical_version": 1},
    )
    _write_book_md(
        ws,
        "B0002",
        "待洗文本PDF",
        {"待洗文本PDF.md": "# 待洗文本PDF\n\n单章正文。\n"},
        manifest={"uid": "B0002", "method": "pdf-text"},
    )
    _write_book_md(
        ws,
        "B0003",
        "待洗多章",
        {"01_第一章.md": "# 第一章\n\n旧章一。\n", "02_第二章.md": "# 第二章\n\n旧章二。\n"},
        manifest={"uid": "B0003", "method": "ebook-to-md"},
    )
    write_ledger(
        ws,
        [
            ("B0001", "人文社科", "已规范", ""),
            ("B0002", "人文社科", "待洗文本PDF", ""),
            ("B0003", "人文社科", "待洗多章", ""),
        ],
    )
    assert run_cli("--root", ws, "library").returncode == 0

    state = ops_status.collect(ops_status.Paths(str(ws)), deep=False)
    mc = state["md_canonical"]
    assert mc["ok"] is True
    assert mc["total"] == 3
    assert mc["canonical"] == 1
    assert mc["pending"] == 2
    assert mc["pdf_text_single_pending"] == 1
    assert mc["batches"]["batch3a_pdf_text"] == 1
    assert mc["batches"]["batch3b_other"] == 1
    assert state["md_done"] == 3  # 原有字段向后兼容

    # 验证 CLI 入口 book-corpus normalize-batch --dry-run --json
    cli_proc = run_cli("--root", ws, "normalize-batch", "--batch", "all", "--dry-run", "--json")
    assert cli_proc.returncode == 0, cli_proc.stderr
    cli_out = json.loads(cli_proc.stdout)
    assert cli_out["dry_run"] is True
    assert cli_out["counts"]["total_pending"] == 2


def test_normalize_batch_respects_writer_lock(tmp_path: Path):
    """持有 .writer.lock 时，normalize-batch 写操作必须阻塞至锁释放，而 --dry-run 不加锁直接完成。"""
    import fcntl
    import os
    import time

    ws = tmp_path / "ws"
    _init_ws(ws)
    _write_book_md(
        ws,
        "B0001",
        "锁测试书",
        {"01_第一章.md": "# 第一章\n\n<table><tr><td>甲</td></tr></table>\n"},
        manifest={"uid": "B0001", "method": "paddle-ocr"},
    )

    lock_path = ws / "工作区" / "运维" / ".writer.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        # --dry-run 不加锁，持锁期间也能立即退出
        dry_proc = run_cli("--root", ws, "normalize-batch", "--batch", "1", "--dry-run", "--json")
        assert dry_proc.returncode == 0

        # 非 dry-run 必须阻塞等待 .writer.lock
        env = dict(os.environ, BOOKS_ROOT=str(ws))
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bookcorpus",
                "--root",
                str(ws),
                "normalize-batch",
                "--batch",
                "1",
                "--backup-root",
                str(tmp_path / "bk"),
                "--json",
            ],
            cwd=str(REPO),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.35)
        assert proc.poll() is None, "持有 .writer.lock 时 normalize-batch 不应提前写盘"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    stdout, stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, stderr
    payload = json.loads(stdout)
    assert payload["succeeded"] == 1


def test_scan_skips_math_formulas_and_fidelity_comments(tmp_path: Path):
    """公式里的 <P(Ut+2)> / <Th) > 与保真注释不得被扫描器误判进 Batch 1。"""
    ws = tmp_path / "ws"
    _init_ws(ws)
    _write_book_md(
        ws,
        "B0001",
        "公式数学书",
        {
            "01_第一章.md": "# 第一章\n\n公式 <P(Ut+2)> 与 <Th) > 以及 $ <U(X^{b})> $。\n",
            "02_第二章.md": "# 第二章\n\n<!-- 源 EPUB 重复收录相同正文；为保真保留此副本。 -->\n",
        },
        manifest={"uid": "B0001", "method": "ebook-to-md"},
    )
    plan = ncb.scan_corpus(ws)
    assert plan["batches"]["1"] == []
    assert [x["uid"] for x in plan["batches"]["3"]] == ["B0001"]


def test_text_retention_guard_triggers_atomic_rollback_in_batch(tmp_path: Path, monkeypatch):
    """Batch 遇到 TextRetentionGuardError 时从备份原子回滚并标注 text_retention_guard。"""
    ws = tmp_path / "ws"
    _init_ws(ws)
    orig = (
        "# 第一章\n\n"
        "<div><span>前置标签一</span><span>前置标签二</span></div>\n\n" + ("不可静默丢失的学术正文段落。" * 50) + "\n"
    )
    md_dir = _write_book_md(
        ws,
        "B0001",
        "熔断回滚书",
        {"01_第一章.md": orig},
        manifest={"uid": "B0001", "method": "paddle-ocr"},
    )

    def _corrupting_normalize(_md, **_kwargs):
        raise ncb.TextRetentionGuardError("simulated loss", lost_chars=1200, drop_ratio=0.5)

    monkeypatch.setattr(ncb, "normalize_book_md", _corrupting_normalize)
    bk = tmp_path / "bk"
    summary = ncb.run_batch(ws, batch="1", backup_root=bk, workers=1)
    assert summary["rolled_back"] == 1
    rec = summary["results"][0]
    assert rec["status"] == "rolled_back"
    assert rec["rolled_back"] is True
    assert rec["guard"] == "text_retention_guard"
    assert "text_retention_guard" in rec["error"]
    assert (md_dir / "01_第一章.md").read_text(encoding="utf-8") == orig


def test_find_pdf_python_handles_broken_venv_symlink_and_injects_pythonpath(tmp_path: Path, monkeypatch):
    """断链 .venv-pdf/bin/python* 时自动探测同版本本机 python 并通过 PYTHONPATH 注入 site-packages。"""
    ws = tmp_path / "ws"
    _init_ws(ws)
    venv_dir = ws / "工作区" / ".venv-pdf"
    (venv_dir / "bin").mkdir(parents=True)
    py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    # 模拟跨机同步后失效的软链
    (venv_dir / "bin" / "python").symlink_to("/nonexistent/colaqaq/bin/python")
    (venv_dir / "bin" / py_ver).symlink_to("/nonexistent/colaqaq/bin/python")

    sp_dir = venv_dir / "lib" / py_ver / "site-packages"
    sp_dir.mkdir(parents=True)
    (sp_dir / "pymupdf.py").write_text("DUMMY = True\n", encoding="utf-8")

    monkeypatch.delenv("BOOKS_PDF_PYTHON", raising=False)
    monkeypatch.setattr(ncb, "_matching_system_pythons", lambda ver: [sys.executable] if ver == py_ver else [])
    ncb._PDF_PYTHON_CACHE.clear()

    py_exe, extra_pp = ncb._find_pdf_python_spec(ws)
    assert py_exe == sys.executable
    assert extra_pp == str(sp_dir)
    assert ncb._find_pdf_python(ws) == sys.executable

    # 验证 reextract 子进程调用继承 PYTHONPATH
    md_dir = _write_book_md(
        ws,
        "B0099",
        "断链测试书",
        {"断链测试书.md": "# 断链测试书\n\n正文。\n"},
        manifest={"uid": "B0099", "method": "pdf-text"},
        pdf_bytes=b"%PDF-1.4 dummy",
    )
    assert md_dir.is_dir()
    seen_envs: list[str] = []
    orig_run = subprocess.run

    def _spy_run(cmd, **kwargs):
        if len(cmd) >= 2 and Path(str(cmd[1])).name in ("pdf_text_to_md.py", "pdf_audit_volume.py"):
            seen_envs.append((kwargs.get("env") or {}).get("PYTHONPATH", ""))
            if Path(str(cmd[1])).name == "pdf_text_to_md.py":
                out_dir = Path(cmd[cmd.index("--out") + 1])
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "断链测试书.md").write_text("# 断链测试书\n\n重抽正文。\n", encoding="utf-8")
                (out_dir / ".manifest.json").write_text(
                    json.dumps({"uid": "B0099", "method": "pdf-text"}), encoding="utf-8"
                )
                return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
            return subprocess.CompletedProcess(
                cmd, 0, stdout="100 / 100 100.00% 1 md\n锚点 : 1/1 窗命中 100.00%\n", stderr=""
            )
        return orig_run(cmd, **kwargs)

    monkeypatch.setattr(ncb.subprocess, "run", _spy_run)
    ok, err, _ = ncb._reextract_pdf_text_to_stage(ws, "B0099", tmp_path / "stage", split_chars=24000, split_blocks=800)
    assert ok is True, err
    assert len(seen_envs) == 2
    assert all(str(sp_dir) in pp for pp in seen_envs)

    # BOOKS_PDF_PYTHON 保持最高优先级覆盖
    fake_override = tmp_path / "override_py"
    fake_override.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("BOOKS_PDF_PYTHON", str(fake_override))
    monkeypatch.setattr(ncb, "_can_import_pymupdf", lambda exe, pythonpath=None: exe == str(fake_override))
    assert ncb._find_pdf_python(ws) == str(fake_override)
