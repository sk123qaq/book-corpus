"""Phase 4 检索 A/B 评测工具链与角色过滤隔离的单元测试。"""

from __future__ import annotations

import csv
import hashlib
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from bookcorpus.scripts import build_para_index, eval_embed_ab, eval_retrieval, vec_search
from bookcorpus.scripts import embed_corpus as ec


def _make_sample_workspace(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")

    # B0001：包含水章(版权页)、带 HTML 表/属性残渣的正文章、以及纯注释章
    b1 = root / "books" / "B0001"
    md1 = b1 / "md"
    md1.mkdir(parents=True)
    (b1 / "book.json").write_text(
        json.dumps({"uid": "B0001", "title": "制度与秩序", "subjects": ["人文社科", "社会学"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (md1 / "01_版权页.md").write_text(
        "# 版权页\n\n"
        "本书由测试出版社发行，ISBN 978-7-0000-0001-0，版权所有翻印必究。\n"
        "这里故意提及科层制与礼治秩序作为水章噪声词。\n",
        encoding="utf-8",
    )
    (md1 / "02_第一章_科层制.md").write_text(
        "# 第一章 科层制\n\n"
        "## 第一节 规则化分工 {#s1 .calibre2}\n\n"
        "现代科层制依赖成文规则、层级节制与专业化分工来维持组织效率，而非个人依附。^![note](images/note.png)^\n"
        "在大型现代组织中，职务权限由明确的法理规章界定，公文档案与职责边界确保行政连续性与可预测性，这也是理性化社会秩序的重要制度基础。\n\n"
        "<table><tr><th>维度</th><th>特征</th></tr><tr><td>科层制</td><td>非人格化规则</td></tr></table>\n",
        encoding="utf-8",
    )
    (md1 / "03_注释.md").write_text(
        "# 注释\n\n[1] 参见韦伯关于科层制理想型的原始文献讨论。\n[2] 参见涂尔干关于社会分工论的论述。\n",
        encoding="utf-8",
    )

    # B0002：包含另一本正文书，确保多书检索与对比排序有区分度
    b2 = root / "books" / "B0002"
    md2 = b2 / "md"
    md2.mkdir(parents=True)
    (b2 / "book.json").write_text(
        json.dumps({"uid": "B0002", "title": "乡土规范", "subjects": ["人文社科", "人类学"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (md2 / "01_目录.md").write_text(
        "# 目录\n\n- 第一章 礼治\n- 第二章 长老统治\n",
        encoding="utf-8",
    )
    (md2 / "02_礼治秩序.md").write_text(
        "# 礼治秩序\n\n"
        "礼治社会维持秩序依靠的是传统教化与内在敬畏，而不是外在的成文法律强制力。[[1]]()\n"
        "在熟人社会中，人们通过长期共同生活形成稳定的行为预期，日常习惯与乡土规范在潜移默化中约束着每个成员的选择与责任。\n",
        encoding="utf-8",
    )


def _deterministic_vec(text: str, dim: int = 16) -> list[float]:
    """根据文本关键信号生成确定性向量：正文关键词 + 上下文前缀获得更高语义相似度。"""
    v = np.zeros(dim, dtype=np.float32)
    # 维 0：科层制主题
    if "科层制" in text or "规则化分工" in text:
        v[0] += 1.0
    # 维 1：礼治主题
    if "礼治" in text or "传统教化" in text:
        v[1] += 1.0
    # 维 2：上下文前缀信号（模拟《书名》/章名带来的语义消歧增益）
    if "《制度与秩序》" in text or "制度与秩序" in text:
        v[2] += 0.6
    if "《乡土规范》" in text or "乡土规范" in text:
        v[3] += 0.6
    # 维 4：水章惩罚（版权页噪声被降权）
    if "版权所有" in text or "ISBN" in text:
        v[4] += 1.2
    # 维 5：查询指令默认给到目标主题和上下文偏好
    if text.startswith("Instruct:"):
        if "科层" in text:
            v[0] += 1.0
            v[2] += 0.8
        if "礼治" in text or "秩序" in text:
            v[1] += 1.0
            v[3] += 0.8
    # 兜底非零分量
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    for i in range(6, dim):
        v[i] = (digest[i] / 255.0 - 0.5) * 0.05
    norm = float(np.linalg.norm(v))
    if norm <= 1e-12:
        v[0] = 1.0
        norm = 1.0
    return (v / norm).tolist()


def _start_mock_embed_server(dim: int = 16) -> tuple[http.server.HTTPServer, str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            inputs = payload.get("input") or []
            data = [{"index": i, "embedding": _deterministic_vec(t, dim=dim)} for i, t in enumerate(inputs)]
            body = json.dumps({"data": data, "usage": {"prompt_tokens": sum(len(t) for t in inputs)}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}"


def test_embed_corpus_role_flags_sig_regression_and_store_meta_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "ws"
    b1 = root / "books" / "B0001"
    md = b1 / "md"
    md.mkdir(parents=True)
    (b1 / "book.json").write_text('{"uid":"B0001","title":"样书"}', encoding="utf-8")
    (md / ".manifest.json").write_text(
        json.dumps({"chapter_roles": {"01_封面.md": "front_back", "03_注释.md": "notes"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (md / "01_封面.md").write_text("# 封面\n\n版权水页。\n", encoding="utf-8")
    (md / "02_正文.md").write_text("# 正文\n\n核心正文段落。\n", encoding="utf-8")
    (md / "03_注释.md").write_text("# 注释\n\n[1] 注释段落。\n", encoding="utf-8")

    paths = ec.chapter_paths(root, "B0001")
    # 兼容红线：默认参数下 sig_of 必须与 Phase 1 字节级一致
    expected_h = hashlib.sha256()
    for p in paths:
        st = p.stat()
        expected_h.update(f"{p.name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    assert ec.sig_of(paths) == expected_h.hexdigest()[:16]
    assert ec.sig_of(paths, context_header=False, include_front_back=False, skip_notes=False) == ec.sig_of(paths)

    # 非默认开关生成独立签名
    sig_fb = ec.sig_of(paths, include_front_back=True)
    sig_sn = ec.sig_of(paths, skip_notes=True)
    sig_both = ec.sig_of(paths, context_header=True, include_front_back=True, skip_notes=True)
    assert len({ec.sig_of(paths), sig_fb, sig_sn, sig_both}) == 4

    # 角色过滤开关：默认跳过 front_back 但保留 notes；--include-front-back 放行；--skip-notes 跳过 notes
    chunks_default = ec.book_chunks(root, "B0001", skip_roles=ec.skip_roles_for())
    assert [Path(c["path"]).name for c in chunks_default] == ["02_正文.md", "03_注释.md"]

    chunks_inc_fb = ec.book_chunks(root, "B0001", skip_roles=ec.skip_roles_for(include_front_back=True))
    assert [Path(c["path"]).name for c in chunks_inc_fb] == ["01_封面.md", "02_正文.md", "03_注释.md"]

    chunks_skip_notes = ec.book_chunks(root, "B0001", skip_roles=ec.skip_roles_for(skip_notes=True))
    assert [Path(c["path"]).name for c in chunks_skip_notes] == ["02_正文.md"]

    # Store.meta 仅在非默认时写入，混用同一 emb.db 时打印 stderr 告警
    db = tmp_path / "emb.db"
    vec = tmp_path / "vectors.f16"
    s_def = ec.Store(db, vec, dim=4, model="t")
    assert s_def.meta("include_front_back") is None
    assert s_def.meta("skip_notes") is None
    s_def.append_book(
        "B0001",
        "样书",
        ec.sig_of(paths),
        [{"path": "books/B0001/md/02_正文.md", "seq": 0, "line": 1, "chars": 6, "text": "核心正文段落"}],
        ec.pack_f16([[1.0, 0.0, 0.0, 0.0]], 4),
        6,
    )
    s_def.close()

    # 同为默认参数重开不告警
    capsys.readouterr()
    s_same = ec.Store(db, vec, dim=4, model="t")
    assert capsys.readouterr().err == ""
    s_same.close()

    # 混用 --include-front-back 与 --skip-notes 告警
    s_mix = ec.Store(db, vec, dim=4, model="t", include_front_back=True, skip_notes=True)
    err = capsys.readouterr().err
    assert "include_front_back=0" in err
    assert "skip_notes=0" in err
    s_mix.close()

    # 新库开启非默认角色参数时写进 Store.meta，后续默认参数重开同样告警
    db2 = tmp_path / "emb2.db"
    vec2 = tmp_path / "vectors2.f16"
    s_nondef = ec.Store(db2, vec2, dim=4, model="t", skip_notes=True)
    assert s_nondef.meta("skip_notes") == "1"
    s_nondef.append_book(
        "B0001",
        "样书",
        sig_sn,
        [{"path": "books/B0001/md/02_正文.md", "seq": 0, "line": 1, "chars": 6, "text": "核心正文段落"}],
        ec.pack_f16([[1.0, 0.0, 0.0, 0.0]], 4),
        6,
    )
    s_nondef.close()

    capsys.readouterr()
    s_back_def = ec.Store(db2, vec2, dim=4, model="t", skip_notes=False)
    assert "skip_notes=1" in capsys.readouterr().err
    s_back_def.close()


def test_vec_search_and_eval_retrieval_isolated_dirs(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _make_sample_workspace(ws)
    idx_db = ws / "build" / "检索" / "index.db"
    con = build_para_index.build(str(idx_db), root=str(ws))
    con.close()

    emb_dir = tmp_path / "iso_emb"
    srv, url = _start_mock_embed_server(dim=16)
    try:
        vec_search.clear_cache()
        books = ec.md_books(ws)
        store = ec.Store(emb_dir / "emb.db", emb_dir / "vectors.f16", dim=16, context_header=True)
        try:
            from types import SimpleNamespace

            rc = ec.run_embedding(
                ws,
                store,
                books,
                SimpleNamespace(
                    sync=False,
                    books=None,
                    force=True,
                    context_header=True,
                    include_front_back=False,
                    skip_notes=False,
                    chunk_chars=700,
                    overlap=0,
                    batch=16,
                    timeout=30,
                    server=url,
                    keep_server=True,
                    allow_missing_engine=False,
                ),
            )
            assert rc == 0
        finally:
            store.close()

        assert vec_search.available(emb_dir=str(emb_dir))
        info = vec_search.health_info(emb_dir=str(emb_dir), url=url)
        assert info["ready"] is True
        assert info["books"] == 2

        hits, meta = vec_search.search_text("科层制规则化分工", emb_dir=str(emb_dir), url=url)
        assert meta["used"] is True
        assert hits
        assert hits[0]["book"] == "B0001"

        gold = tmp_path / "gold.tsv"
        with gold.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["qid", "category", "question", "terms", "path"], delimiter="\t")
            w.writeheader()
            w.writerow(
                {
                    "qid": "Q1",
                    "category": "原词",
                    "question": "现代科层制依赖什么维持效率",
                    "terms": "科层制,分工",
                    "path": "books/B0001/md/02_第一章_科层制.md",
                }
            )
            w.writerow(
                {
                    "qid": "Q2",
                    "category": "原词",
                    "question": "礼治社会靠什么维持秩序",
                    "terms": "礼治,秩序",
                    "path": "books/B0002/md/02_礼治秩序.md",
                }
            )

        out = tmp_path / "eval.tsv"
        eval_retrieval.main(
            [
                "--gold",
                str(gold),
                "--system",
                "E,V,BQ,HYB",
                "--k",
                "5,10",
                "--out",
                str(out),
                "--jobs",
                "1",
                "--root",
                str(ws),
                "--index-db",
                str(idx_db),
                "--emb-dir",
                str(emb_dir),
                "--server",
                url,
            ]
        )
        summary = (tmp_path / "eval-summary.tsv").read_text(encoding="utf-8").splitlines()
        assert len(summary) == 5
        for line in summary[1:]:
            cols = line.split("\t")
            assert cols[1] == "1.000"  # recall@5 = 1.000
    finally:
        srv.shutdown()
        srv.server_close()


def test_eval_embed_ab_with_mock_server_and_unreachable_degradation(tmp_path: Path) -> None:
    src_ws = tmp_path / "source_ws"
    _make_sample_workspace(src_ws)
    snapshot_before = sorted(p.relative_to(src_ws).as_posix() for p in src_ws.rglob("*"))

    gold = tmp_path / "gold.tsv"
    with gold.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["qid", "category", "question", "terms", "uid", "path", "evidence"],
            delimiter="\t",
        )
        w.writeheader()
        w.writerow(
            {
                "qid": "Q1",
                "category": "原词",
                "question": "科层制为什么依赖规则化分工",
                "terms": "科层制,规则化分工",
                "uid": "B0001",
                "path": "books/B0001/md/02_第一章_科层制.md",
                "evidence": "现代科层制依赖成文规则",
            }
        )
        w.writerow(
            {
                "qid": "Q2",
                "category": "原词",
                "question": "礼治社会为什么依靠传统教化",
                "terms": "礼治,传统教化",
                "uid": "B0002",
                "path": "books/B0002/md/02_礼治秩序.md",
                "evidence": "礼治社会维持秩序依靠的是传统教化",
            }
        )

    # 1. 安全护栏拒绝把 work_dir 设为源工作区
    with pytest.raises(SystemExit):
        eval_embed_ab.run_ab(source_root=src_ws, work_dir=src_ws, uids=["B0001"])

    # 2. Mock Embedding Server 完整跑通 A vs B vs C 对比
    srv, url = _start_mock_embed_server(dim=16)
    work_online = tmp_path / "work_online"
    try:
        out_json = work_online / "report.json"
        out_md = work_online / "report.md"
        rc = eval_embed_ab.main(
            [
                "--root",
                str(src_ws),
                "--work-dir",
                str(work_online),
                "--uids",
                "B0001,B0002",
                "--gold",
                str(gold),
                "--dim",
                "16",
                "--server",
                url,
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        assert rc == 0
        rep = json.loads(out_json.read_text(encoding="utf-8"))
        md_txt = out_md.read_text(encoding="utf-8")

        assert rep["embed_ab"]["status"] == "completed"
        assert set(rep["embed_ab"]["arms"]) == {"A_baseline", "B_treatment", "C_skip_notes"}
        assert rep["chunk_analysis"]["A_baseline"]["front_back_chunks"] > 0
        assert rep["chunk_analysis"]["B_treatment"]["front_back_chunks"] == 0
        assert rep["chunk_analysis"]["B_treatment"]["notes_chunks"] > 0
        assert rep["chunk_analysis"]["C_skip_notes"]["notes_chunks"] == 0
        assert rep["chunk_analysis"]["B_treatment"]["residue_chunks"] == 0
        assert rep["chunk_analysis"]["B_treatment"]["header_overhead_pct"] > 0
        assert rep["bm25_ab"]["pollution"]["A_raw"]["html_residue_files"] > 0
        assert rep["bm25_ab"]["pollution"]["B_norm"]["html_residue_files"] == 0
        assert rep["bm25_ab"]["pollution"]["A_raw"]["front_back_paras"] > 0
        assert rep["bm25_ab"]["pollution"]["B_norm"]["front_back_paras"] == 0
        assert "A（基线未过滤）" in md_txt
        assert "B（前缀+滤水章）" in md_txt
        assert "C（前缀+滤水章+滤注释）" in md_txt
        assert "Hybrid RRF" in md_txt
        assert rep["decision"]["recommendation"] in ("RECOMMEND_REEMBED", "HOLD_REEMBED")
    finally:
        srv.shutdown()
        srv.server_close()

    # 3. 服务不可达时优雅降级（不崩溃、输出 skipped_unreachable + BM25 对比 + Token 增幅 + 补跑命令）
    work_offline = tmp_path / "work_offline"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bookcorpus",
            "eval-ab",
            "--root",
            str(src_ws),
            "--work-dir",
            str(work_offline),
            "--uids",
            "B0001,B0002",
            "--gold",
            str(gold),
            "--server",
            url,  # 端口已关闭
            "--corpus-tokens",
            "1000000",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    off_rep = json.loads(proc.stdout)
    assert off_rep["embed_ab"]["status"] == "skipped_unreachable"
    assert "eval-ab" in off_rep["embed_ab"]["rerun_command"]
    assert off_rep["reembed_estimate"]["header_overhead_pct"] > 0
    assert off_rep["reembed_estimate"]["corpus_reembed_tokens"] > 1_000_000
    assert off_rep["reembed_estimate"]["corpus_reembed_hms"]
    assert off_rep["decision"]["recommendation"] == "PENDING_EMBED_AB"
    assert (work_offline / "eval-ab-report.md").is_file()

    # 源工作区绝对未被写入或修改
    snapshot_after = sorted(p.relative_to(src_ws).as_posix() for p in src_ws.rglob("*"))
    assert snapshot_before == snapshot_after


def test_vec_search_cli_book_query_cli_and_recommend_reembed_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path / "ws"
    _make_sample_workspace(ws)
    idx_db = ws / "build" / "检索" / "index.db"
    con = build_para_index.build(str(idx_db), root=str(ws))
    con.close()

    emb_dir = tmp_path / "cli_emb"
    srv, url = _start_mock_embed_server(dim=16)
    try:
        vec_search.clear_cache()
        books = ec.md_books(ws)
        store = ec.Store(emb_dir / "emb.db", emb_dir / "vectors.f16", dim=16, context_header=True)
        try:
            from types import SimpleNamespace

            ec.run_embedding(
                ws,
                store,
                books,
                SimpleNamespace(
                    sync=False,
                    books=None,
                    force=True,
                    context_header=True,
                    include_front_back=False,
                    skip_notes=False,
                    chunk_chars=700,
                    overlap=0,
                    batch=16,
                    timeout=30,
                    server=url,
                    keep_server=True,
                    allow_missing_engine=False,
                ),
            )
        finally:
            store.close()

        capsys.readouterr()
        assert vec_search.main(["--emb-dir", str(emb_dir), "--server", url, "--health", "--json"]) == 0
        health_out = json.loads(capsys.readouterr().out)
        assert health_out["ready"] is True

        assert vec_search.main(["--emb-dir", str(emb_dir), "--server", url, "--json", "科层制"]) == 0
        vs_out = json.loads(capsys.readouterr().out)
        assert vs_out["meta"]["used"] is True
        assert vs_out["hits"][0]["book"] == "B0001"

        from bookcorpus.scripts import book_query

        assert (
            book_query.main(
                [
                    "--root",
                    str(ws),
                    "--index-db",
                    str(idx_db),
                    "--emb-dir",
                    str(emb_dir),
                    "--embed-url",
                    url,
                    "--no-cards",
                    "--json",
                    "科层制",
                ]
            )
            == 0
        )
        bq_out = json.loads(capsys.readouterr().out)
        assert bq_out["fusion"] == "hybrid"
        assert bq_out["vec_used"] is True
    finally:
        srv.shutdown()
        srv.server_close()

    # 验证量化门槛 RECOMMEND_REEMBED 分支
    gate = eval_embed_ab._decision_gate(
        chunk_b={"header_overhead_pct": 8.5, "front_back_chunks": 0, "residue_chunks": 0},
        embed_ab={
            "status": "completed",
            "arms": {
                "A_baseline": {
                    "vector": {"mrr": 0.50, "recall": {"10": 0.60}},
                    "hybrid": {"mrr": 0.60, "recall": {"10": 0.70}},
                },
                "B_treatment": {
                    "vector": {"mrr": 0.58, "recall": {"10": 0.68}},
                    "hybrid": {"mrr": 0.66, "recall": {"10": 0.75}},
                },
            },
        },
        max_overhead_pct=15.0,
    )
    assert gate["recommendation"] == "RECOMMEND_REEMBED"
    assert gate["checks"]["overhead_within_budget"] is True
    assert gate["checks"]["pollution_cleared"] is True
    assert gate["checks"]["vector_quality_gate"] is True
