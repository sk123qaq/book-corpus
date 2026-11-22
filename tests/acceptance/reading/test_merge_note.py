#!/usr/bin/env python3
"""8771 书页「由 N 个 md 文件合并标题页而来」标注验收：数据、API、真实 DOM 三层都断言。

  python3 tests/acceptance/reading/test_merge_note.py          # 全量（DOM 需要 headless Chrome）
  PYTHONPATH=src python3 -m pytest tests/acceptance/reading/test_merge_note.py -q

背景：统计口径改成 md 文件视角后，书页页头章数是文件数，页内章目是合并标题页后的
导航章数；两者不一致（如 01 题记 + 02 前置页正文）时页面必须就地标注，不许页头 2、
页内 1 而无任何说明。没有 Chrome 的环境（CI）跳过 DOM 层，仍完成数据与 API 验证。

判定：
- 临时根里造 B0001（01_第一章.md 题记短 + 002_前置页_正文.md 正文长 → 合并成 1 个导航章）
  与 B0002（单文件正文，不触发合并）；
- /api/shelf：章数是文件视角（B0001=2），可读 2 本；
- /api/book/B0001：book.chapters=2（文件视角）、章目 1 条、merged=True、source_files 2 个；
- /book/B0001 的真实 DOM 里出现「本页 1 章，由 2 个 md 文件合并标题页而来」，
  /book/B0002 不出现该标注；末行 ALL PASS。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

MERGE_MARK = "合并标题页而来"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get_json(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _write_book(root, uid, subject, title, files):
    """conftest.write_book 的独立版：book.json + 指定名章文件。"""
    book_dir = root / "books" / uid
    (book_dir / "md").mkdir(parents=True, exist_ok=True)
    for name, body in files:
        (book_dir / "md" / name).write_text(body, encoding="utf-8")
    payload = {
        "uid": uid,
        "title": title,
        "title_from_file": "",
        "author": "某人",
        "subjects": [subject],
        "retention": "keep",
        "sources": [
            {
                "format": "epub",
                "bytes": 100,
                "sha256": f"{int(uid[1:]):064x}",
                "primary_src": 1,
                "file": f"source/{title}.epub",
            }
        ],
        "awards": [],
        "notes": "",
    }
    (book_dir / "book.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _fixture_root(base):
    root = base / "merge-note-instance"
    root.mkdir()
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    body = "前厅的钟敲了七下，窗外的雨一直没有停，他坐回桌前把灯拨亮了一格。\n\n" * 60
    _write_book(
        root,
        "B0001",
        "文学",
        "甲书",
        [
            ("001_第一章.md", "# 第一章\n\n雪落无声。"),
            ("002_前置页_正文.md", "# 前置页 正文\n\n" + body),
        ],
    )
    _write_book(root, "B0002", "人文社科", "乙书", [("001_第一章.md", "# 第一章\n\n" + body)])
    return root


def _wait_shelf(port, timeout=20):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            return _get_json(port, "/api/shelf")
        except OSError as exc:
            last = exc
            time.sleep(0.1)
    raise AssertionError(f"8771 没起来：{last}")


def find_chrome():
    env = os.environ.get("CHROME_BIN")
    if env:
        return env
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return name
    return ""


def dom_session(port, checks, timeout=20):
    """一个 Chrome 会话依次打开 checks 里的页面并断言。

    checks 每项是 (path, ready, needle, expect)：先等 ready（页面渲染完成的标记）
    出现在正文，再看 needle 是否在；expect=True 要求出现，False 要求不出现。
    返回 [(expect, needle_in_body, body), ...]。
    """
    sys.path.insert(0, str(REPO / "tests" / "manual" / "demo"))
    import shot

    chrome, profile, ws_url = shot.launch(1200, 900, "about:blank", 0)
    cdp = shot.connect_cdp(ws_url)
    try:
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        results = []
        for path, ready, needle, expect in checks:
            cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{port}{path}"})
            deadline = time.time() + timeout
            body = ""
            while time.time() < deadline:
                body = shot.eval_js(cdp, "document.body ? document.body.innerText : ''") or ""
                if ready in body:
                    break
                time.sleep(0.1)
            results.append((expect, needle in body, body))
        return results
    finally:
        cdp.ws.close()
        chrome.kill()
        chrome.wait(timeout=10)
        subprocess.run(["rm", "-rf", profile], check=False)


def main():
    base = Path(tempfile.mkdtemp(prefix="reading-merge-note-"))
    try:
        root = _fixture_root(base)
        port = _free_port()
        env = dict(os.environ, READING_ANNOTATE="0", PYTHONDONTWRITEBYTECODE="1")
        log = (base / "server.log").open("wb")
        proc = subprocess.Popen(
            [
                sys.executable,
                str(REPO / "apps" / "reading" / "server.py"),
                "--root",
                str(root),
                "--port",
                str(port),
                "--no-annotate",
            ],
            cwd=str(REPO),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            shelf = _wait_shelf(port)
            stats = shelf["stats"]
            assert stats["readable"] == 2, stats
            assert stats["chapters"] == 3, stats  # 文件视角：B0001 两个文件 + B0002 一个
            assert stats.get("built_at"), stats
            books = {b["uid"]: b for b in shelf["books"]}
            assert books["B0001"]["chapters"] == 2, books["B0001"]
            assert books["B0002"]["chapters"] == 1, books["B0002"]
            print("PASS /api/shelf 章数为文件视角（B0001=2, B0002=1, 合计 3）")

            api = _get_json(port, "/api/book/B0001")
            assert api["book"]["chapters"] == 2, api["book"]
            assert len(api["chapters"]) == 1, len(api["chapters"])
            merged = api["chapters"][0]
            assert merged["merged"] is True, merged
            assert len(merged["source_files"]) == 2, merged
            assert merged["body_file"] == "002_前置页_正文.md", merged
            print("PASS /api/book/B0001 文件视角 2 章、导航 1 章、merged=True")

            chrome = find_chrome()
            if not chrome:
                print("DOM SKIP 没找到 Chrome（设 CHROME_BIN 或装 chromium 后重跑，才验证页面标注）")
            else:
                results = dom_session(
                    port,
                    [
                        ("/book/B0001", "甲书", "本页 1 章，由 2 个 md 文件合并标题页而来", True),
                        ("/book/B0002", "乙书", MERGE_MARK, False),
                    ],
                )
                _, hit, body = results[0]
                assert hit, f"书页没出现合并标注；正文前 400 字：{body[:400]}"
                print("PASS /book/B0001 页面出现「本页 1 章，由 2 个 md 文件合并标题页而来」")
                _, hit, body = results[1]
                assert not hit, f"B0002 不该有合并标注；正文前 400 字：{body[:400]}"
                print("PASS /book/B0002 单文件书不出现合并标注")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("ALL PASS")


if __name__ == "__main__":
    main()
