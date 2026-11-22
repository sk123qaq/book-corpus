"""书库检索 UI 改版与跨书相关性降序筛选测试。"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from bookcorpus.scripts.book_query import Plan
from bookcorpus.scripts.book_score import BookScore
from conftest import run_cli, write_book, write_ledger

REPO = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO / "apps" / "demo"
sys.path.insert(0, str(REPO / "tests" / "manual" / "demo"))
import shot


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_demo(root: Path):
    port = _free_port()
    env = dict(os.environ)
    env.pop("BOOKS_ROOT", None)
    env.pop("BOOKS_RUNLOG_ROOT", None)
    env["BOOKS_ROOT"] = str(root)
    env["PI_BIN"] = "/bin/false"
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(DEMO_DIR / "server.py"),
            "--root",
            str(root),
            "--port",
            str(port),
            "--reading-url",
            "http://127.0.0.1:18771",
            "--quiet",
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"demo server exited {proc.returncode}:\n{stderr}")
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return proc, base
        except Exception:
            time.sleep(0.05)
    proc.terminate()
    proc.wait(timeout=5)
    raise AssertionError("demo server did not become ready")


def _stop_proc(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def test_trace_related_sorts_by_relevance_desc_before_threshold(tmp_path: Path, monkeypatch):
    """即使 score 倒挂，related_mode 也先按 relevance 降序排列再按阈值筛选。"""
    assert run_cli("init", str(tmp_path), "--no-pdf-venv", env={"BOOKS_ROOT": str(tmp_path)}).returncode == 0
    books_meta = [("B0001", "源书"), ("B0002", "高分低相关"), ("B0003", "低分高相关"), ("B0004", "中相关")]
    for uid, title in books_meta:
        write_book(tmp_path, uid, "人文", title, [f"{title} 礼治 秩序 测试正文段落"])
    write_ledger(tmp_path, [(uid, "人文", title, "") for uid, title in books_meta])
    assert run_cli("index", "--rebuild", env={"BOOKS_ROOT": str(tmp_path)}).returncode == 0
    assert run_cli("library", env={"BOOKS_ROOT": str(tmp_path)}).returncode == 0

    if str(DEMO_DIR) not in sys.path:
        sys.path.insert(0, str(DEMO_DIR))
    import trace as tracer

    import context as demo_context

    demo_context.configure_runtime(str(tmp_path))

    # B0002 has higher raw BM25 score (24.0) than B0003 (12.0),
    # while B0003 has higher vec_best (0.72) and relevance.
    b2 = BookScore(
        book="B0002",
        title="高分低相关",
        type="人文",
        chapters=3,
        score=24.0,
        best=6.0,
        topk=6.0,
        has_prefix=False,
        has_queries=False,
        scores=[6.0],
        hits=1,
        mean=6.0,
        vec_best=0.0,
        coverage=0.2,
        groups=["g1"],
        total_groups=4,
        best_path="books/B0002/md/001_第1节.md",
        best_line=3,
        best_text="高分低相关 礼治 秩序 测试正文段落",
        support=1,
        pinned=False,
    )
    b3 = BookScore(
        book="B0003",
        title="低分高相关",
        type="人文",
        chapters=3,
        score=12.0,
        best=5.0,
        topk=5.0,
        has_prefix=False,
        has_queries=False,
        scores=[5.0],
        hits=3,
        mean=5.0,
        vec_best=0.72,
        coverage=1.0,
        groups=["g1", "g2", "g3", "g4"],
        total_groups=4,
        best_path="books/B0003/md/001_第1节.md",
        best_line=3,
        best_text="低分高相关 礼治 秩序 测试正文段落",
        support=3,
        pinned=False,
    )
    b4 = BookScore(
        book="B0004",
        title="中相关",
        type="人文",
        chapters=3,
        score=18.0,
        best=4.5,
        topk=4.5,
        has_prefix=False,
        has_queries=False,
        scores=[4.5],
        hits=2,
        mean=4.5,
        vec_best=0.45,
        coverage=0.5,
        groups=["g1", "g2"],
        total_groups=4,
        best_path="books/B0004/md/001_第1节.md",
        best_line=3,
        best_text="中相关 礼治 秩序 测试正文段落",
        support=2,
        pinned=False,
    )
    fake_plan = Plan(
        query="礼治 秩序",
        terms=["礼治", "秩序"],
        nouns=["礼治", "秩序"],
        action_dropped=[],
        skipped=[],
        stop="C",
        best_round=1,
        ms=5.0,
        fetches=1,
        model_calls=0,
        timings={},
        fusion="hybrid",
        vec_used=True,
        vec_ms=2.0,
        vec_hits=3,
        rerank_used=False,
        rerank_ms=0.0,
        quality=(),
        rounds=[],
        ranking=[b2, b4, b3],
        chapters=[
            {
                "book": "B0002",
                "title": "高分低相关",
                "book_score": 24.0,
                "selected": [
                    {
                        "chapter_path": "books/B0002/md/001_第1节.md",
                        "chapter_title": "第1节",
                        "line": 3,
                        "score": 6.0,
                        "snippet": "高分低相关",
                    }
                ],
            },
            {
                "book": "B0004",
                "title": "中相关",
                "book_score": 18.0,
                "selected": [
                    {
                        "chapter_path": "books/B0004/md/001_第1节.md",
                        "chapter_title": "第1节",
                        "line": 3,
                        "score": 4.5,
                        "snippet": "中相关",
                    }
                ],
            },
            {
                "book": "B0003",
                "title": "低分高相关",
                "book_score": 12.0,
                "selected": [
                    {
                        "chapter_path": "books/B0003/md/001_第1节.md",
                        "chapter_title": "第1节",
                        "line": 3,
                        "score": 5.0,
                        "snippet": "低分高相关",
                    }
                ],
            },
        ],
    )

    monkeypatch.setattr(
        tracer,
        "extract_terms",
        lambda *_a, **_kw: {"terms": ["礼治", "秩序"], "groups": [], "ms": 1.0, "source": "stub"},
    )
    monkeypatch.setattr(tracer.book_query, "run", lambda *_a, **_kw: fake_plan)

    res = tracer.build(
        "礼治 秩序",
        mode="related",
        exclude_books=["B0001"],
        threshold=0.50,
        source_text="不重复原段",
        fresh=True,
    )
    ranking = res["plan"]["ranking"]
    rels = [r["relevance"] for r in ranking]
    assert rels == sorted(rels, reverse=True)
    assert [r["uid"] for r in ranking] == ["B0003", "B0004", "B0002"]
    assert [c["uid"] for c in res["plan"]["chapters"]] == ["B0003", "B0004", "B0002"]
    assert res["related_search"]["selected_books"] == ["B0003", "B0004"]
    assert res["result"]["uid"] == "B0003"


def test_session_ui_redesign_e2e_chromium(tmp_path: Path):
    """无头 Chromium 端到端验证：去完整流程、相关性降序筛选、主题、Popover/Dialog、四视口零溢出。"""
    env = {"BOOKS_ROOT": str(tmp_path)}
    assert run_cli("init", str(tmp_path), "--no-pdf-venv", env=env).returncode == 0

    src_para = "乡土社会是礼治社会，依靠长老教化与传统习惯维持日常秩序，不需要外在的法律强力与铁笼约束。"
    books = [
        ("B0001", "乡土中国", src_para),
        ("B0002", "东晋门阀政治", "这本书深入剖析礼治、秩序、法律、教化与门阀共治的制度根基。" * 3),
        ("B0003", "江村经济", "礼治、秩序与法律在乡村宗族评议中如何调适。" * 2),
        ("B0004", "乡土重建", "礼治与秩序依靠乡绅缓冲并存，形成基层社会的长期稳定机制。" * 2),
        ("B0005", "万历十五年", "仅仅顺便提及礼治一词，其余主要叙述朝廷财政与漕运制度。" * 2),
    ]
    for uid, title, body in books:
        write_book(tmp_path, uid, "人文社科", title, [body])
    write_ledger(tmp_path, [(uid, "人文社科", title, "") for uid, title, _ in books])
    assert run_cli("index", "--rebuild", env=env).returncode == 0
    assert run_cli("library", env=env).returncode == 0

    # Pre-seed one session so history popover/dialog controls are testable
    sess_dir = tmp_path / "工作区" / "检索" / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "a1b2c3d4e5f6.json").write_text(
        json.dumps(
            {
                "id": "a1b2c3d4e5f6",
                "updated_at": "2026-11-22T09:12:00",
                "turns": [
                    {
                        "n": 1,
                        "question": "礼治社会靠什么维持秩序？",
                        "trace": {
                            "extract": {"terms": ["礼治社会", "秩序"], "groups": []},
                            "plan": {
                                "rounds": [{"n": 1, "kind": "full", "hits": 5}],
                                "ranking": [{"uid": "B0001", "book": "B0001", "title": "乡土中国", "score": 12.0}],
                                "chapters": [{"uid": "B0001", "book": "B0001", "title": "乡土中国", "selected": []}],
                                "stop": "C",
                                "best_round": 1,
                            },
                            "cards": {"trigger": False, "cards": []},
                            "result": {
                                "uid": "B0001",
                                "book": "B0001",
                                "book_title": "乡土中国",
                                "chapter": "第1节",
                                "path": "books/B0001/md/001_第1节.md",
                                "line": 3,
                                "text": src_para,
                            },
                            "stage_ms": {"understand": 5, "rounds": 5, "rank": 5, "cards": 1, "result": 2},
                            "total_ms": 18,
                        },
                        "answer_state": "ready",
                        "answer": {
                            "ok": True,
                            "lead": "依靠教化与传统习惯维持秩序。",
                            "sections": [{"heading": "一、礼治核心", "body": "人服礼是主动的。"}],
                            "citations": [
                                {
                                    "book": "B0001",
                                    "book_title": "乡土中国",
                                    "chapter": "第1节",
                                    "path": "books/B0001/md/001_第1节.md",
                                    "line": 3,
                                    "quote": src_para,
                                    "verified": True,
                                }
                            ],
                            "confidence": "high",
                            "ms": 1200,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    proc, base = _start_demo(tmp_path)
    chrome = None
    profile = ""
    cdp = None
    try:
        # 1. HTTP checks: "/" has no "完整流程" or "/classic" and no sample buttons; "/classic" stays 200
        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            home_html = resp.read().decode("utf-8")
        assert "完整流程" not in home_html
        assert "/classic" not in home_html
        assert 'class="sample"' not in home_html
        with urllib.request.urlopen(f"{base}/classic", timeout=5) as resp:
            assert resp.status == 200
            assert "ask-form" in resp.read().decode("utf-8")

        # port=0 让内核分配调试口，避开 _free_port() 先 bind 再 close 的抢占窗口；
        # launch 内部已带退避重试，起不来时抛 ChromeLaunchError 并附 chrome 的 stderr。
        chrome, profile, ws_url = shot.launch(1440, 900, "about:blank", 0)
        cdp = shot.connect_cdp(ws_url)
        for method in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
            cdp.call(method)
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
        )

        # 2. Load home & test dark theme toggle + popover menu + native <dialog>
        cdp.call("Page.navigate", {"url": f"{base}/"})
        deadline = time.time() + 15
        home_ready = "document.querySelectorAll('.hist-row').length >= 1 && !!document.querySelector('.last-card')"
        while time.time() < deadline:
            if shot.eval_js(cdp, home_ready):
                break
            time.sleep(0.1)

        assert not shot.eval_js(cdp, "document.body.innerText.includes('完整流程')")
        assert shot.eval_js(cdp, "document.querySelectorAll('a[href*=\"classic\"]').length") == 0

        # Theme toggle
        shot.eval_js(cdp, "document.querySelector('#theme-toggle').click(); true")
        time.sleep(0.1)
        assert shot.eval_js(cdp, "document.querySelector('meta[name=\"color-scheme\"]').content") == "dark"
        assert shot.eval_js(cdp, "getComputedStyle(document.body).backgroundColor") == "rgb(22, 23, 22)"
        shot.eval_js(cdp, "document.querySelector('#theme-toggle').click(); true")
        time.sleep(0.1)

        # Popover menu & native confirm <dialog>
        shot.eval_js(cdp, "document.querySelector('.hist-more-btn').click(); true")
        time.sleep(0.05)
        assert shot.eval_js(cdp, "document.querySelector('.menu-popover').matches(':popover-open')") is True
        shot.eval_js(cdp, "document.querySelector('.menu-popover [data-hist-act=\"delete\"]').click(); true")
        time.sleep(0.05)
        assert shot.eval_js(cdp, "document.querySelector('#confirm-dialog').open") is True
        shot.eval_js(cdp, "document.querySelector('#confirm-dlg-cancel').click(); true")

        # 3. mode=trace checks
        trace_url = f"{base}/?" + urllib.parse.urlencode({"q": "乡土社会", "books": "B0001", "mode": "trace"})
        cdp.call("Page.navigate", {"url": trace_url})
        deadline = time.time() + 15
        while time.time() < deadline:
            if shot.eval_js(cdp, "!!document.querySelector('.trace-hit-card, .answer-error')"):
                break
            time.sleep(0.1)
        body_snap = shot.eval_js(cdp, "document.body.innerText.slice(0, 600)")
        assert shot.eval_js(cdp, "document.querySelectorAll('.trace-hit-card').length") == 1, body_snap
        assert shot.eval_js(cdp, "document.querySelectorAll('.scope-pill').length") == 1
        assert shot.eval_js(cdp, "!!document.querySelector('.answer-pending-note')") is True
        shot.eval_js(cdp, "document.querySelector('.strip-bar').click(); true")
        assert shot.eval_js(cdp, "document.querySelectorAll('.stage-row').length") == 5

        # 4. mode=related checks: descending sort -> threshold filter + in-place slider focus
        rel_url = f"{base}/?" + urllib.parse.urlencode(
            {
                "q": "礼治 秩序 法律 教化",
                "mode": "related",
                "exclude": "B0001",
                "src_path": "books/B0001/md/001_第1节.md",
                "src_line": "3",
                "threshold": "0.30",
            }
        )
        cdp.call("Page.navigate", {"url": rel_url})
        deadline = time.time() + 15
        while time.time() < deadline:
            if shot.eval_js(cdp, "document.querySelectorAll('.related-panel .related-card').length >= 3"):
                break
            time.sleep(0.1)

        snap = shot.eval_js(
            cdp,
            """(() => {
              const txtScore = (e) => parseInt(e.textContent, 10);
              const qual = Array.from(
                document.querySelectorAll('[data-related-dynamic] > .related-list .related-score')
              ).map(txtScore);
              const below = Array.from(
                document.querySelectorAll('.related-below .related-score')
              ).map(txtScore);
              const ranks = Array.from(document.querySelectorAll('.rank-badge')).map(
                (e) => parseInt(e.textContent.replace('#', ''), 10)
              );
              return { qual, below, ranks };
            })()""",
        )
        assert snap["qual"] == sorted(snap["qual"], reverse=True)
        assert len(snap["qual"]) >= 2
        assert snap["below"] == sorted(snap["below"], reverse=True)
        assert snap["ranks"] == list(range(1, len(snap["ranks"]) + 1))

        # Slider keeps focus while updating in-place and raising to 95% auto-opens <details>.
        # focus+input+断言必须在同一次 Runtime.evaluate 里：分两次往返时，页面若在两者之间
        # render()（mainEl.innerHTML 整体重建），被 focus 的节点已被换掉，activeElement 落回 body。
        focus_js = """(() => {
          const el = document.querySelector('.related-thr-range');
          if (!el) return 'no range';
          el.focus();
          if (document.activeElement !== el) return 'focus refused: ' + (document.activeElement || {}).className;
          el.value = '95';
          el.dispatchEvent(new Event('input', { bubbles: true }));
          if (!el.isConnected) return 'range replaced during update';
          if (document.activeElement !== el) return 'focus lost: ' + ((document.activeElement || {}).className || '');
          return true;
        })()"""
        assert shot.eval_js(cdp, focus_js) is True
        assert shot.eval_js(cdp, "!!document.querySelector('.related-lower-btn')") is True
        assert shot.eval_js(cdp, "document.querySelector('.related-below').open") is True
        shot.eval_js(cdp, "document.querySelector('.related-lower-btn').click(); true")
        qual_cnt_js = "document.querySelectorAll('[data-related-dynamic] > .related-list .related-card').length >= 1"
        assert shot.eval_js(cdp, qual_cnt_js) is True

        # 5. Responsive zero horizontal overflow across viewports
        viewports = [(1440, 900, False), (1024, 768, False), (768, 1024, False), (390, 844, True), (375, 812, True)]
        for w, h, mobile in viewports:
            cdp.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile},
            )
            time.sleep(0.08)
            no_overflow = shot.eval_js(cdp, "document.documentElement.scrollWidth <= window.innerWidth")
            assert no_overflow is True, f"overflow at {w}x{h}"

        cdp.wait_events(0.3)
        assert cdp.errors() == []
        reqs = [
            ((ev.get("params") or {}).get("request") or {}).get("url")
            for ev in cdp.events
            if ev.get("method") == "Network.requestWillBeSent"
        ]
        outside = [u for u in reqs if u and not u.startswith(base) and u != "about:blank"]
        assert outside == [], f"unexpected external requests: {outside}"
    finally:
        if cdp is not None:
            cdp.ws.close()
        if chrome is not None:
            chrome.terminate()
            chrome.wait(timeout=5)
        if profile:
            shutil.rmtree(profile, ignore_errors=True)
        _stop_proc(proc)
