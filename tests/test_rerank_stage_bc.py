"""交叉编码器精排（Stage B 单书选章 + Stage C 作答选窗 + GPU 预检与源测速）单元测试。"""

import http.server
import json
import sqlite3
import sys
import threading
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "bookcorpus" / "scripts"
DEMO = Path(__file__).resolve().parents[1] / "apps" / "demo"
for _p in (str(SCRIPTS), str(DEMO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import answer
import book_query
import book_score
import qsearch
import rerank_client
import vec_search

from bookcorpus import rerankenv


def test_rerankenv_skips_when_no_gpu_or_low_vram(monkeypatch, tmp_path):
    monkeypatch.setattr(rerankenv, "_detect_nvidia_gpu", lambda *a, **kw: None)
    monkeypatch.setattr(rerankenv, "_detect_amd_gpu", lambda *a, **kw: None)
    called = []
    monkeypatch.setattr(rerankenv, "_install_packages", lambda *a, **kw: called.append("pkg") or (True, "ok"))
    monkeypatch.setattr(rerankenv, "ensure_model", lambda *a, **kw: called.append("model") or (True, "ok"))

    ok, rep = rerankenv.create(tmp_path)
    assert ok is False
    assert rep["skipped"] is True
    assert called == [], "无 GPU 时不得装包或下载模型"

    # 显存不足同样跳过
    low_gpu = rerankenv.GpuInfo(False, "nvidia", "GTX 1050", 2.0, 1.2, "cuda", "显存不足")
    monkeypatch.setattr(rerankenv, "_detect_nvidia_gpu", lambda *a, **kw: low_gpu)
    ok2, rep2 = rerankenv.create(tmp_path)
    assert ok2 is False
    assert rep2["skipped"] is True
    assert called == []


def test_rerankenv_picks_faster_pypi_and_hf_mirror(monkeypatch, tmp_path):
    def fake_probe(name, url, probe_path="/packaging/", timeout=5.0):
        if "tuna.tsinghua" in url:
            return rerankenv.MirrorProbe(name, url, True, 42.0, 1024)
        return rerankenv.MirrorProbe(name, url, True, 310.0, 1024)

    monkeypatch.setattr(rerankenv, "_probe_url", fake_probe)
    best, probes = rerankenv.pick_pypi_index()
    assert best == rerankenv.PYPI_TSINGHUA
    assert len(probes) == 2

    # 官方更快时选官方
    monkeypatch.setattr(
        rerankenv,
        "_probe_url",
        lambda name, url, probe_path="/packaging/", timeout=5.0: rerankenv.MirrorProbe(
            name, url, True, 25.0 if "pypi.org" in url else 180.0, 1024
        ),
    )
    best2, _ = rerankenv.pick_pypi_index()
    assert best2 == rerankenv.PYPI_OFFICIAL

    # 验证模型下载默认优先走 hf-mirror
    py = Path(rerankenv.python(tmp_path))
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!/bin/sh\n", encoding="utf-8")
    gpu_ok = rerankenv.GpuInfo(True, "amd", "RX 9070", 12.8, 11.0, "gfx1201", "")
    seen_endpoints = []

    def fake_run(cmd, capture_output=True, text=True, timeout=1800, env=None):
        seen_endpoints.append((env or {}).get("HF_ENDPOINT"))
        m_dir = Path(cmd[-1])
        m_dir.mkdir(parents=True, exist_ok=True)
        (m_dir / "config.json").write_text("{}", encoding="utf-8")
        (m_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
        with (m_dir / "model.safetensors").open("wb") as fh:
            fh.truncate(120 * 1024 * 1024)

        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    monkeypatch.setattr(rerankenv.subprocess, "run", fake_run)
    ok_m, msg = rerankenv.ensure_model(tmp_path, gpu=gpu_ok)
    assert ok_m is True, msg
    assert seen_endpoints == [rerankenv.HF_MIRROR]
    assert rerankenv.model_ready(tmp_path) is True


def test_rerank_client_http_and_graceful_fallback(monkeypatch):
    rerank_client.clear_cache()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            scores = [float(len(t)) for t in payload["texts"]]
            body = json.dumps({"scores": scores, "ms": 3.2}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{port}"
        assert rerank_client.server_healthy(url=url) is True
        sc = rerank_client.rerank_scores("问题", ["短", "更长一段话"], url=url)
        assert sc == [1.0, 5.0]
    finally:
        srv.shutdown()
        srv.server_close()

    # 关闭端口平滑降级为 None
    rerank_client.clear_cache()
    assert rerank_client.server_healthy(url=f"http://127.0.0.1:{port}", timeout=0.2) is False
    assert rerank_client.rerank_scores("问题", ["a"], url=f"http://127.0.0.1:{port}", timeout=0.2) is None

    # off 开关彻底禁用
    monkeypatch.setenv("BOOKS_RERANK_URL", "off")
    assert rerank_client.server_healthy() is False
    assert rerank_client.rerank_scores("问题", ["a"]) is None


def test_extract_centered_text_keeps_heading_and_hit_line():
    lines = ["# 第三章 制度之变", "前情铺垫" * 60, "背景二" * 60, "关键答案句就在这一行。", "后续推论" * 60]
    out = rerank_client.extract_centered_text(lines, 4, max_chars=260)
    assert out.startswith("# 第三章 制度之变")
    assert "关键答案句就在这一行。" in out


def test_stage_b_fuse_chapters_with_rerank_and_fallback():
    bm25_rows = [
        {"chapter_path": "books/B1/md/01.md", "chapter_title": "ch1", "line": 5, "score": 6.0, "snippet": "ch1 text"},
        {"chapter_path": "books/B1/md/02.md", "chapter_title": "ch2", "line": 8, "score": 4.0, "snippet": "ch2 text"},
    ]
    vec_hits = [
        {"path": "books/B1/md/01.md", "line": 5, "score": 0.85},
        {"path": "books/B1/md/02.md", "line": 8, "score": 0.70},
    ]
    # 精排把 ch2 抬到第 1，w_rerank=3.0 足以扭转单章名次
    chaps = vec_search.fuse_chapters(
        bm25_rows,
        vec_hits,
        max_chapters=2,
        snippet_fn=None,
        question="问第二章内容",
        w_rerank=3.0,
        chapter_text_fn=lambda cp, ln, cid, fb: "第二章关键内容" if "02.md" in cp else "无关段落",
        rerank_fn=lambda q, ts: [5.0 if "第二章" in t else -4.0 for t in ts],
    )
    assert chaps[0]["chapter_path"] == "books/B1/md/02.md"
    assert "rerank_score" in chaps[0]

    # 精排返回 None 时平滑降级回原 BM25+Vec 顺序（ch1 第 1）
    chaps_fb = vec_search.fuse_chapters(
        bm25_rows,
        vec_hits,
        max_chapters=2,
        snippet_fn=None,
        question="问第二章内容",
        chapter_text_fn=lambda cp, ln, cid, fb: fb or cp,
        rerank_fn=lambda q, ts: None,
    )
    assert chaps_fb[0]["chapter_path"] == "books/B1/md/01.md"


def test_stage_c_pick_passages_rerank_reorders_seed_and_falls_back(monkeypatch, tmp_path):
    book_dir = tmp_path / "books" / "B1" / "md"
    book_dir.mkdir(parents=True)
    (book_dir / "01.md").write_text("# 第一章\n\n这里是首章但跑题的句子。\n" * 15, encoding="utf-8")
    (book_dir / "02.md").write_text("# 第二章\n\n真正直接回答为什么的关键证据在这里。\n" * 15, encoding="utf-8")

    monkeypatch.setattr(answer, "ROOT", str(tmp_path))
    monkeypatch.setattr(answer, "_chapter_titles", lambda b: ({}, "missing"))
    monkeypatch.setattr(
        qsearch,
        "search",
        lambda *a, **kw: (
            [
                {"book": "B1", "path": "books/B1/md/01.md", "line": 3, "score": 6.0, "coverage": 2, "text": "跑题"},
                {"book": "B1", "path": "books/B1/md/02.md", "line": 3, "score": 3.0, "coverage": 1, "text": "关键证据"},
            ],
            1.0,
        ),
    )

    # 精排可用：不把跑题的 current 锁死在 picked[0]
    picked, _ = answer.pick_passages(
        None,
        "B1",
        "books/B1/md/01.md",
        3,
        ["关键", "证据"],
        [],
        question="为什么会出现关键证据",
        vec_fn=lambda b, q, k: [{"book": "B1", "path": "books/B1/md/02.md", "line": 3, "score": 0.90}],
        rerank_fn=lambda q, ts: [8.0 if "真正直接回答" in t else -5.0 for t in ts],
    )
    assert picked[0]["path"] == "books/B1/md/02.md"
    assert "rerank_score" in picked[0]

    # 精排失败返回 None：平滑退回原顺序（current 固定在 picked[0]）
    picked_fb, _ = answer.pick_passages(
        None,
        "B1",
        "books/B1/md/01.md",
        3,
        ["关键", "证据"],
        [],
        question="为什么会出现关键证据",
        vec_fn=lambda b, q, k: [{"book": "B1", "path": "books/B1/md/02.md", "line": 3, "score": 0.90}],
        rerank_fn=lambda q, ts: None,
    )
    assert picked_fb[0]["path"] == "books/B1/md/01.md"


def test_build_windows_preserves_top_rank_mid_and_order(monkeypatch, tmp_path):
    book_dir = tmp_path / "books" / "B1" / "md"
    book_dir.mkdir(parents=True)
    # 构造长章节：第 10 行是低排名噪声，第 25 行（同窗重叠）与第 120 行（不重叠）是高排名命中
    lines = ["# 长章节"] + [f"第{i}行填充内容" + ("甲乙丙丁戊己" * 30) for i in range(2, 160)]
    lines[9] = "LOW_RANK_EARLIER_NOISE_LINE_10"
    lines[24] = "TOP_RANK_OVERLAP_TARGET_LINE_25"
    lines[119] = "SECOND_RANK_NON_OVERLAP_LINE_120"
    (book_dir / "01.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(answer, "ROOT", str(tmp_path))
    monkeypatch.setattr(answer, "_lines", lambda rel: (tmp_path / rel).read_text(encoding="utf-8").splitlines())

    chosen = [
        {"book": "B1", "path": "books/B1/md/01.md", "line": 120},  # rank 0 (不重叠、行号更大)
        {"book": "B1", "path": "books/B1/md/01.md", "line": 25},  # rank 1 (与 line 10 重叠)
        {"book": "B1", "path": "books/B1/md/01.md", "line": 10},  # rank 2 (更小行号，旧代码会抢走 mid 并抢占第一窗)
    ]
    wins = answer.build_windows(chosen, {"B1": {}}, {"B1": "missing"}, window_chars=1200, max_windows=2)
    assert len(wins) == 2
    assert "SECOND_RANK_NON_OVERLAP_LINE_120" in wins[0]["text"]
    assert "TOP_RANK_OVERLAP_TARGET_LINE_25" in wins[1]["text"]


def test_planner_stage_b_rerank_sets_flag():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE expand(path TEXT, kind TEXT)")
    meta = {"B1": book_score.Meta(title="书一", subject1="人文", subject2="", chapters=10)}
    kwargs = {
        "meta": meta,
        "expand": {},
        "search_fn": lambda terms: (
            [
                {
                    "id": 1,
                    "path": "books/B1/md/01.md",
                    "book": "B1",
                    "line": 2,
                    "score": 9.0,
                    "text": "a",
                    "chars": 50,
                    "hits": 1,
                    "groups": ["x"],
                },
                {
                    "id": 2,
                    "path": "books/B1/md/02.md",
                    "book": "B1",
                    "line": 2,
                    "score": 6.0,
                    "text": "b",
                    "chars": 50,
                    "hits": 1,
                    "groups": ["x"],
                },
            ],
            1.0,
        ),
        "df_fn": lambda t: 1,
        "vec_fn": lambda q, ex: (
            [{"chunk_id": 1, "book": "B1", "path": "books/B1/md/01.md", "line": 2, "score": 0.9}],
            {"used": True, "ms": 2.0, "hits": 1, "qvec": np.ones(4, dtype=np.float32)},
        ),
        "book_vec_fn": lambda b, q, k: [
            {"chunk_id": 2, "book": "B1", "path": "books/B1/md/02.md", "line": 2, "score": 0.85},
            {"chunk_id": 1, "book": "B1", "path": "books/B1/md/01.md", "line": 2, "score": 0.80},
        ],
        "limit": 3,
    }
    # 无精排时 Vec#1 的 02.md 胜出；接入精排偏置 "a" 后翻转为 01.md
    plan_no_rr = book_query.Planner(con, **kwargs).plan(["x"], question="找第一章")
    assert plan_no_rr.rerank_used is False
    assert plan_no_rr.chapters[0]["selected"][0]["chapter_path"] == "books/B1/md/02.md"

    plan = book_query.Planner(con, rerank_fn=lambda q, ts: [4.0 if "a" in t else -2.0 for t in ts], **kwargs).plan(
        ["x"], question="找第一章"
    )
    assert plan.rerank_used is True
    assert plan.chapters[0]["selected"][0]["chapter_path"] == "books/B1/md/01.md"
