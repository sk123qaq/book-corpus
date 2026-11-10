#!/usr/bin/env python3
"""Check the chapter-path predicates against the real reading code.

This is a generated-input test, not the TLA+ pass. It enumerates the small
on-disk state space that ChapterPath.tla models (status, html, model, ledger,
legacy, sha match) and asks the real code the same questions:

  - GET /api/chapter/B0001/01                 -> service.py:649 _chapter_path
  - GET /reader/B0001/01/depth                -> server.py:458 _serve_depth
  - depth.audit._audit_chapter(...)["path"]   -> audit.py:202

Run from the repository root:

    uv run --frozen python verification/chapter_path/check_paths.py

Exit 1 means a state that a user can reach violates P1 or P2. The TLA+ model
is the exhaustive proof over the abstract state space; this script shows that
the abstraction matches the code for every state in that space.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from bookcorpus.reading.depth.audit import _audit_chapter
from bookcorpus.reading.library import Library
from bookcorpus.reading.store import Store

REPO = Path(__file__).resolve().parents[2]
UID = "B0001"
N = 1
SOURCE = "# 第1节\n\n卷积神经网络把局部模式逐层组合成整体。\n"

# A depth chapter that is structurally valid enough for the gates to run.
DEPTH_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>第1节</title></head>
<body><main><section data-body="source"><p>卷积神经网络把局部模式逐层组合成整体。</p></section></main></body></html>
"""


def make_root(root: Path) -> None:
    book = root / "books" / UID
    (book / "md").mkdir(parents=True)
    (book / "md" / "001_第1节.md").write_text(SOURCE, encoding="utf-8")
    (book / "book.json").write_text(
        json.dumps(
            {
                "uid": UID,
                "title": "路径检查",
                "subjects": ["测试"],
                "sources": [{"format": "epub", "file": "source.epub"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")


def reset_pack(store: Store) -> Path:
    pack = Path(store.pack_dir(UID, N))
    shutil.rmtree(pack, ignore_errors=True)
    pack.mkdir(parents=True, exist_ok=True)
    return pack


def write_state(
    store: Store,
    *,
    status: str,
    html: bool,
    model: bool,
    ledger: bool,
    legacy: bool,
    sha_match: bool,
    source_sha: str,
) -> None:
    pack = reset_pack(store)
    if legacy:
        (pack / "manifest.json").write_text(json.dumps({"draft": False, "tiers": {}}), encoding="utf-8")
        (pack / "chapter.html").write_text("<!doctype html><html><body><p>旧包</p></body></html>", encoding="utf-8")
    depth = pack / "depth"
    if status != "none":
        depth.mkdir(parents=True, exist_ok=True)
        state_sha = source_sha if sha_match else "0" * 64
        (depth / "state.json").write_text(
            json.dumps({"status": status, "source_sha256": state_sha}),
            encoding="utf-8",
        )
    if model:
        depth.mkdir(parents=True, exist_ok=True)
        (depth / "chapter_model.json").write_text("{}", encoding="utf-8")
    if ledger:
        depth.mkdir(parents=True, exist_ok=True)
        (depth / "ledger.json").write_text("{}", encoding="utf-8")
    if html:
        depth.mkdir(parents=True, exist_ok=True)
        (depth / "chapter.html").write_text(DEPTH_HTML, encoding="utf-8")


def reachable(*, status: str, html: bool, model: bool, ledger: bool) -> bool:
    """Which disk states the model's actions can produce.

    save_depth writes chapter_model.json and ledger.json together; a depth job
    always writes state.json. ReadyBuild always writes chapter.html, so
    status="ready" without a page is old data, not a current action.
    """
    if status == "none":
        return not (html or model or ledger)
    if status == "ready":
        return model and ledger and html
    return model and ledger


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def start_server(root: Path) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = dict(os.environ)
    env.update({"READING_DEPTH": "1", "READING_AGENT_STANDARD": "0", "READING_ANNOTATE": "0"})
    proc = subprocess.Popen(  # noqa: S603 argv 固定为解释器+本仓脚本/常量，不含外部输入
        [
            sys.executable,
            str(REPO / "apps" / "reading" / "server.py"),
            "--root",
            str(root),
            "--port",
            str(port),
            "--quiet",
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(proc.stderr.read().decode("utf-8", "replace"))
        try:
            with urllib.request.urlopen(  # noqa: S310 base 是 127.0.0.1 常量
                base + "/api/shelf", timeout=1
            ) as response:
                if response.status == 200:
                    return proc, base
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("server did not start")


def get_json(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=5) as response:  # noqa: S310 base 是 127.0.0.1 常量
        return json.loads(response.read().decode("utf-8"))


def depth_status(base: str, path: str) -> int:
    try:
        with urllib.request.urlopen(base + path, timeout=5) as response:  # noqa: S310 base 是 127.0.0.1 常量
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="chapter-path-") as tmp:
        root = Path(tmp)
        make_root(root)
        library = Library(root)
        store = Store(library.layout)
        raw = library.read_chapter(UID, N)
        if raw is None:
            print("fixture error: read_chapter returned None", file=sys.stderr)
            return 2
        source_sha = raw["source_sha256"]

        proc, base = start_server(root)
        failures: list[tuple[str, str, str]] = []
        checked = 0
        reachable_checked = 0
        try:
            for status, html, model, ledger, legacy, sha_match in itertools.product(
                ("none", "model", "ready"),
                (False, True),
                (False, True),
                (False, True),
                (False, True),
                (False, True),
            ):
                write_state(
                    store,
                    status=status,
                    html=html,
                    model=model,
                    ledger=ledger,
                    legacy=legacy,
                    sha_match=sha_match,
                    source_sha=source_sha,
                )
                is_reachable = reachable(status=status, html=html, model=model, ledger=ledger)
                checked += 1
                if not is_reachable:
                    continue
                reachable_checked += 1

                data = get_json(base, f"/api/chapter/{UID}/{N}")
                badge = data.get("path")
                http = depth_status(base, f"/reader/{UID}/{N}/depth")
                derived = store.depth_state(UID, N, source_sha)
                audit = _audit_chapter(library, store, UID, N)
                audit_path = audit["path"] if audit else None
                normalized = "missing" if badge == "none" else badge

                flags = (
                    f"status={status} html={int(html)} model={int(model)} "
                    f"ledger={int(ledger)} legacy={int(legacy)} sha={int(sha_match)}"
                )
                if badge == "depth" and http != 200:
                    failures.append(
                        (
                            "P1",
                            flags,
                            f"badge=depth depth_status={http} state={derived.get('status')}",
                        )
                    )
                if normalized != audit_path:
                    failures.append(("P2", flags, f"badge={badge} audit={audit_path}"))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        print(f"checked {checked} disk states; {reachable_checked} reachable by the model")
        if not failures:
            print("P1 AdvertisedDepthIsReadable: no violation")
            print("P2 AuditPathMatchesService: no violation")
            return 0
        for rule, flags, detail in failures:
            print(f"FAIL {rule} {flags}: {detail}")
        p1 = sum(1 for rule, _, _ in failures if rule == "P1")
        p2 = len(failures) - p1
        print(f"{len(failures)} violation(s) in reachable states ({p1} P1, {p2} P2)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
