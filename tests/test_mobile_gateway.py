"""Slice 0+1 端到端测试：SSOT 导出、`.bcbook` 打包、鉴权网关、断点续传、分片进度与跨书检索代理。"""

from __future__ import annotations

import hashlib
import io
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bookcorpus.mobile_gateway.bundler import build_bcbook_bundle
from bookcorpus.mobile_gateway.progress import ProgressStore
from bookcorpus.mobile_gateway.server import build_server
from bookcorpus.reading.book_types import (
    DEFAULT_READ_TYPE,
    LITERATURE_SUBKIND_HINTS,
    READ_PROFILES,
    _infer_pair,
)
from bookcorpus.reading.library import CLOTHES, Library
from bookcorpus.reading.mdparse import BLOCK_VERSION
from bookcorpus.reading.store import Store
from bookcorpus.scripts.classify_model import NO_SUB, TAXONOMY
from bookcorpus.scripts.layout import Layout

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_call(
    base: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    hdrs = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
    req = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), {k.lower(): v for k, v in exc.headers.items()}


def _build_synthetic_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "books").mkdir(parents=True)
    (root / "工作区" / "阅读").mkdir(parents=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")

    # B0001: 含合并题页 (00_扉页 <=120字 + 01_前置页 >=500字)、正文、尾注、images、.manifest.json 与非过期/过期缓存
    b1 = root / "books" / "B0001"
    b1_md = b1 / "md"
    (b1_md / "images").mkdir(parents=True)
    (b1 / "source").mkdir(parents=True)
    book_src_sha = "a" * 64
    (b1 / "book.json").write_text(
        json.dumps(
            {
                "uid": "B0001",
                "title": "史记选读",
                "author": "z-library.sk, 司马迁",
                "subjects": ["人文社科", "历史"],
                "retention": "keep",
                "sources": [{"format": "epub", "bytes": 4096, "sha256": book_src_sha, "primary_src": 1}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (b1_md / "00_扉页.md").write_text("# 第一卷 项羽本纪\n\n题记：力拔山兮气盖世。\n", encoding="utf-8")
    (b1_md / "01_前置页 卷首.md").write_text(
        "# 卷首正文\n\n" + ("秦二世元年七月，陈涉等起大泽中，天下云集响应，赢粮而景从。" * 25) + "\n",
        encoding="utf-8",
    )
    (b1_md / "02_高祖本纪.md").write_text(
        "# 高祖本纪\n\n高祖，沛丰邑中阳里人，姓刘氏，字季。![附图](images/fig1.png)\n",
        encoding="utf-8",
    )
    (b1_md / "03_注释.md").write_text(
        "# 注释\n\n[^1]: 集解：应劭曰，沛，县名也。\n",
        encoding="utf-8",
    )
    (b1_md / "images" / "fig1.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKEPNG")
    (b1_md / ".manifest.json").write_text(
        json.dumps(
            {
                "uid": "B0001",
                "method": "ebook-to-md",
                "canonical_version": 1,
                "source_sha256": book_src_sha,
                "chapter_roles": {
                    "00_扉页.md": "front_back",
                    "01_前置页 卷首.md": "body",
                    "02_高祖本纪.md": "body",
                    "03_注释.md": "notes",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # B0002: 文学小说（无 companion cache）
    b2 = root / "books" / "B0002"
    b2_md = b2 / "md"
    b2_md.mkdir(parents=True)
    (b2 / "source").mkdir(parents=True)
    (b2 / "book.json").write_text(
        json.dumps(
            {
                "uid": "B0002",
                "title": "呐喊",
                "author": "鲁迅",
                "subjects": ["文学", "严肃文学小说"],
                "retention": "keep",
                "sources": [{"format": "epub", "bytes": 2048, "sha256": "b" * 64, "primary_src": 1}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (b2_md / "01_狂人日记.md").write_text(
        "# 狂人日记\n\n" + ("今天晚上，很好的月光。我不见他，已是三十多年。" * 20) + "\n",
        encoding="utf-8",
    )

    # 为 B0001 第 2 逻辑章 (02_高祖本纪.md, n=2) 写入有效缓存和过期缓存
    lib = Library(str(root), overlay_root="")
    ch2 = lib.read_chapter("B0001", 2)
    assert ch2 is not None
    ch2_sha = ch2["source_sha256"]
    store = Store(Layout(str(root)))
    store.save_chapter_map(
        "B0001",
        2,
        {
            "schema": 1,
            "uid": "B0001",
            "n": "02",
            "source_sha256": ch2_sha,
            "supported": True,
            "sections": [{"id": "S1", "title": "起兵"}],
            "terms": [],
        },
    )
    store.save_chapter_tool(
        "B0001",
        2,
        "history_diagram_parallel_timeline",
        {
            "uid": "B0001",
            "n": "02",
            "source_sha256": ch2_sha,
            "action": "diagram",
            "blueprint": "parallel_timeline",
        },
    )
    store.pin_assist_note(
        "B0001",
        2,
        {"id": "note-1", "block_ids": ["B01"], "content": "刘邦起事背景"},
        source_sha=ch2_sha,
    )
    # 故意写一条 source_sha256 过期与一条 block_version 过期的工具缓存，打包器必须剔除
    stale_dir = Path(store.pack_dir("B0001", 2)) / "chapter_tools"
    (stale_dir / "stale_sha_tool.json").write_text(
        json.dumps({"uid": "B0001", "n": "02", "source_sha256": "0" * 64, "block_version": BLOCK_VERSION}),
        encoding="utf-8",
    )
    (stale_dir / "stale_ver_tool.json").write_text(
        json.dumps({"uid": "B0001", "n": "02", "source_sha256": ch2_sha, "block_version": 1}),
        encoding="utf-8",
    )
    return root


# ---- 1. SSOT Profile Exporter & Drift Check ---------------------------
def test_ssot_profile_exporter_and_drift_check(tmp_path):
    out_file = tmp_path / "profiles.generated.json"
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "export_mobile_profiles.py"), "--out", str(out_file)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out_file.read_text(encoding="utf-8"))

    # CLI stdout 输出与 --out 文件字节一致
    stdout_proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "export_mobile_profiles.py")],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert stdout_proc.returncode == 0
    assert stdout_proc.stdout == out_file.read_text(encoding="utf-8")

    assert payload["schema_version"] == 1
    assert payload["default_read_type"] == DEFAULT_READ_TYPE == "social"
    assert len(payload["taxonomy"]) == 11
    assert payload["taxonomy"] == {k: list(v) for k, v in TAXONOMY.items()}
    assert payload["literature_subkind_hints"] == LITERATURE_SUBKIND_HINTS

    profiles = payload["profiles"]
    assert len(profiles) == len(READ_PROFILES) == 7
    assert [p["read_type"] for p in profiles] == list(READ_PROFILES.keys())

    blueprint_slots = [bp["blueprint"] for p in profiles for bp in p["blueprints"]]
    assert len(blueprint_slots) == 20
    assert len(set(blueprint_slots)) == 19

    extensions = [(p["read_type"], ext) for p in profiles for ext in p["extensions"]]
    assert len(extensions) == 13
    ext_keys = {(rt, ext["action"]) for rt, ext in extensions}
    assert ("social", "actor_incentives") in ext_keys
    cross_true = {(rt, ext["action"]) for rt, ext in extensions if ext["cross_search"] is True}
    assert cross_true == {("history", "compare_context"), ("social", "method_audit")}
    assert all(isinstance(ext["cross_search"], bool) for _, ext in extensions)

    # 校验每一类/二级对与 book_types._infer_pair 完全对齐
    resolved = payload["routing_rules"]["resolved_pairs"]
    assert len(resolved) >= len(TAXONOMY)
    for row in resolved:
        sub1 = row["subject1"]
        sub2 = row["subject2"]
        expected_rt, expected_sub, expected_hint = _infer_pair([sub1, sub2] if sub2 else ([sub1] if sub1 else []))
        assert (row["read_type"], row["sub_kind"], row["boundary_hint"]) == (expected_rt, expected_sub, expected_hint)

    # 确认每一个 TAXONOMY 里的 (一级, 二级) 都被覆盖
    pair_set = {(r["subject1"], r["subject2"]) for r in resolved}
    for sub1, sub2_tuple in TAXONOMY.items():
        assert (sub1, "") in pair_set
        assert (sub1, NO_SUB) in pair_set
        for sub2 in sub2_tuple:
            assert (sub1, sub2) in pair_set


# ---- 2. .bcbook Bundler -----------------------------------------------
def test_bcbook_bundler_merged_chapters_hashes_and_cache(tmp_path):
    root = _build_synthetic_workspace(tmp_path)
    lib = Library(str(root), overlay_root="")
    out_zip = tmp_path / "B0001.bcbook"
    artifact = build_bcbook_bundle(lib, "B0001", out_zip, include_cache=True)

    # 确定性复现：第二次打包产物字节与 SHA-256 完全一致
    out_zip2 = tmp_path / "B0001_copy.bcbook"
    artifact2 = build_bcbook_bundle(lib, "B0001", out_zip2, include_cache=True)
    assert artifact.sha256 == artifact2.sha256
    assert out_zip.read_bytes() == out_zip2.read_bytes()

    with zipfile.ZipFile(out_zip) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "md/.manifest.json" in names
        assert "md/00_扉页.md" in names
        assert "md/01_前置页 卷首.md" in names
        assert "md/02_高祖本纪.md" in names
        assert "md/03_注释.md" in names
        assert "images/fig1.png" in names
        assert "companion_cache/ch_02/chapter_map.json" in names
        assert "companion_cache/ch_02/assist_notes.json" in names
        assert "companion_cache/ch_02/chapter_tools/history_diagram_parallel_timeline.json" in names
        assert "companion_cache/ch_02/chapter_tools/stale_sha_tool.json" not in names
        assert "companion_cache/ch_02/chapter_tools/stale_ver_tool.json" not in names

        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["uid"] == "B0001"
    assert manifest["author"] == "司马迁"
    assert manifest["read_type"] == "history"
    assert manifest["book_source_sha256"] == "a" * 64
    assert manifest["cloth_color"] == CLOTHES[sum(ord(c) for c in "B0001") % 8]
    assert manifest["cover_rel"] is None
    assert manifest["block_version"] == BLOCK_VERSION == 2
    assert manifest["canonical_version"] == 1

    # 原始 4 个 md 文件合并为 3 个逻辑章节（绝不等于 raw .md 数 4）
    chapters = manifest["logical_chapters"]
    assert len(chapters) == 3
    assert [ch["ordinal"] for ch in chapters] == [1, 2, 3]
    assert [ch["n"] for ch in chapters] == ["01", "02", "03"]

    assert chapters[0]["merged"] is True
    assert chapters[0]["source_files"] == ["00_扉页.md", "01_前置页 卷首.md"]
    assert chapters[0]["chapter_role"] == "body"
    # chapter_content_sha256 必须与 Library.read_chapter() 一致且严格区别于 book_source_sha256
    ch1_read = lib.read_chapter("B0001", 0)
    assert ch1_read is not None
    assert chapters[0]["chapter_content_sha256"] == ch1_read["source_sha256"]
    assert chapters[0]["chapter_content_sha256"] != manifest["book_source_sha256"]

    assert chapters[1]["merged"] is False
    assert chapters[1]["chapter_role"] == "body"
    ch2_read = lib.read_chapter("B0001", 2)
    assert ch2_read is not None
    assert chapters[1]["chapter_content_sha256"] == ch2_read["source_sha256"]

    assert chapters[2]["chapter_role"] == "notes"

    # include_cache=False 不包含 companion_cache/
    no_cache_zip = tmp_path / "B0001_nocache.bcbook"
    build_bcbook_bundle(lib, "B0001", no_cache_zip, include_cache=False)
    with zipfile.ZipFile(no_cache_zip) as zf:
        assert not any(name.startswith("companion_cache/") for name in zf.namelist())


# ---- 3. Progress Shard Store ------------------------------------------
def test_progress_store_multi_device_merge_never_overwrites(tmp_path):
    root = _build_synthetic_workspace(tmp_path)
    store = ProgressStore(root)

    store.put_shard(
        {
            "device_id": "phone-pixel",
            "ordinal": 12,
            "block_id": "B09",
            "char_offset": 120,
            "high_water_mark_ordinal": 12,
            "updated_at_ms": 1700000000000,
        },
        uid="B0001",
    )
    store.put_shard(
        {
            "device_id": "desktop-linux",
            "ordinal": 2,
            "block_id": "B03",
            "char_offset": 15,
            "high_water_mark_ordinal": 2,
            "updated_at_ms": 1700000005000,
        },
        uid="B0001",
    )

    assert (root / "工作区" / "阅读" / "B0001" / "progress" / "phone-pixel.json").is_file()
    assert (root / "工作区" / "阅读" / "B0001" / "progress" / "desktop-linux.json").is_file()

    merged = store.get_merged("B0001")
    assert merged["uid"] == "B0001"
    assert merged["merged_high_water_mark_ordinal"] == 12
    assert len(merged["shards"]) == 2
    by_dev = {s["device_id"]: s for s in merged["shards"]}
    assert by_dev["phone-pixel"]["ordinal"] == 12
    assert by_dev["desktop-linux"]["ordinal"] == 2
    assert by_dev["desktop-linux"]["block_id"] == "B03"


# ---- 4. HTTP Gateway Contract + Range + Outbox + Cross Search ---------
@pytest.fixture
def mock_search_server():
    captured_requests: list[dict] = []
    fake_hits = [
        {
            "uid": f"B{9000 + i}",
            "book": f"B{9000 + i}",
            "title": f"关联参考书 {i}",
            "type": "历史" if i % 2 == 0 else "社会与经济",
            "relevance": round(0.95 - i * 0.02, 4),
            "best_path": f"books/B{9000 + i}/md/01_正文.md",
            "best_line": 10 + i,
            "best_text": f"这是第 {i} 条跨书对照命中摘录。",
        }
        for i in range(18)
    ]

    class _MockSearchHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:
            if self.path != "/api/run":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            captured_requests.append(payload)
            resp = json.dumps({"ok": True, "plan": {"ranking": fake_hits}}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockSearchHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", captured_requests
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def gateway_fixture(tmp_path, mock_search_server):
    root = _build_synthetic_workspace(tmp_path)
    search_url, captured = mock_search_server
    token = "deadbeef" * 8
    server, service = build_server(
        root,
        bind="127.0.0.1",
        port=0,
        token=token,
        search_url=search_url,
        quiet=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, token, root, captured, service
    finally:
        server.shutdown()
        server.server_close()


def test_gateway_auth_catalog_profiles_and_range_reassembly(gateway_fixture):
    base, token, _root, _captured, _service = gateway_fixture

    # 未携带/错误 Token 时所有端点一律返回 401
    for method, path, body in [
        ("GET", "/api/mobile/v1/health", None),
        ("GET", "/api/mobile/v1/profiles", None),
        ("GET", "/api/mobile/v1/catalog", None),
        ("GET", "/api/mobile/v1/outbox", None),
        ("POST", "/api/mobile/v1/outbox", {"uids": ["B0001"]}),
        ("POST", "/api/mobile/v1/outbox/ack", {"ticket_ids": ["tkt-1"]}),
        ("GET", "/api/mobile/v1/bundle/B0001", None),
        ("GET", "/api/mobile/v1/progress/B0001", None),
        (
            "POST",
            "/api/mobile/v1/progress/B0001",
            {"device_id": "d1", "ordinal": 1, "block_id": "B01", "high_water_mark_ordinal": 1},
        ),
        ("POST", "/api/mobile/v1/cross-search", {"question": "测试"}),
    ]:
        status_no_auth, _, _ = _http_call(base, path, method=method, body=body)
        assert status_no_auth == 401, (method, path)
        status_bad, _, _ = _http_call(base, path, method=method, token="wrong-token", body=body)
        assert status_bad == 401, (method, path)

    # 非回环 Host 头携带合法 Bearer Token 时放行
    status, health_raw, _ = _http_call(
        base, "/api/mobile/v1/health", token=token, headers={"Host": "192.168.1.50:8780"}
    )
    assert status == 200
    assert json.loads(health_raw)["ok"] is True

    # profiles
    status, prof_raw, _ = _http_call(base, "/api/mobile/v1/profiles", token=token)
    assert status == 200
    prof_doc = json.loads(prof_raw)
    assert len(prof_doc["profiles"]) == 7

    # catalog & 过滤
    status, cat_raw, _ = _http_call(base, "/api/mobile/v1/catalog", token=token)
    assert status == 200
    cat = json.loads(cat_raw)
    assert cat["total"] == 2
    by_uid = {b["uid"]: b for b in cat["books"]}
    assert by_uid["B0001"]["logical_chapter_count"] == 3
    assert by_uid["B0001"]["has_companion_cache"] is True
    assert by_uid["B0001"]["canonical_version"] == 1
    assert by_uid["B0001"]["read_type"] == "history"
    assert by_uid["B0002"]["has_companion_cache"] is False
    assert by_uid["B0002"]["read_type"] == "literature"

    status, cat_cached_raw, _ = _http_call(base, "/api/mobile/v1/catalog?has_cache=1", token=token)
    assert status == 200
    assert [b["uid"] for b in json.loads(cat_cached_raw)["books"]] == ["B0001"]

    status, cat_q_raw, _ = _http_call(base, "/api/mobile/v1/catalog?q=%E5%91%90%E5%96%8A", token=token)
    assert status == 200
    assert [b["uid"] for b in json.loads(cat_q_raw)["books"]] == ["B0002"]

    # bundle 全量下载与 Range 206 分段续传逐字节拼接比对
    status_full, full_bytes, full_hdrs = _http_call(base, "/api/mobile/v1/bundle/B0001?include_cache=1", token=token)
    assert status_full == 200
    assert full_hdrs["accept-ranges"] == "bytes"
    etag = full_hdrs["etag"]
    full_sha = hashlib.sha256(full_bytes).hexdigest()
    assert etag == f'"{full_sha}"'

    # If-None-Match 命中 304
    status_304, _, _ = _http_call(
        base, "/api/mobile/v1/bundle/B0001?include_cache=1", token=token, headers={"If-None-Match": etag}
    )
    assert status_304 == 304

    # 分 3 段 Range 请求拼接还原
    total = len(full_bytes)
    p1_end = min(199, total - 1)
    p2_end = min(699, total - 1)
    ranges = [f"bytes=0-{p1_end}", f"bytes={p1_end + 1}-{p2_end}", f"bytes={p2_end + 1}-"]
    reassembled = bytearray()
    for rng in ranges:
        st, chunk, hdrs = _http_call(
            base,
            "/api/mobile/v1/bundle/B0001?include_cache=1",
            token=token,
            headers={"Range": rng},
        )
        assert st == 206
        assert hdrs["accept-ranges"] == "bytes"
        assert hdrs["etag"] == etag
        assert hdrs["content-range"].endswith(f"/{total}")
        reassembled.extend(chunk)
    assert bytes(reassembled) == full_bytes
    assert hashlib.sha256(bytes(reassembled)).hexdigest() == full_sha
    with zipfile.ZipFile(io.BytesIO(bytes(reassembled))) as zf:
        assert json.loads(zf.read("manifest.json").decode("utf-8"))["uid"] == "B0001"

    # 越界 Range 返回 416
    status_416, _, _ = _http_call(
        base,
        "/api/mobile/v1/bundle/B0001?include_cache=1",
        token=token,
        headers={"Range": f"bytes={total + 10}-"},
    )
    assert status_416 == 416


def test_gateway_outbox_progress_and_cross_search(gateway_fixture, tmp_path):
    base, token, root, captured, _service = gateway_fixture

    # Outbox 入队、持久化与 ACK
    status, add_raw, _ = _http_call(
        base,
        "/api/mobile/v1/outbox",
        method="POST",
        token=token,
        body={"uids": ["B0001", "B0002"], "include_cache": True},
    )
    assert status == 200
    added = json.loads(add_raw)["added"]
    assert [t["uid"] for t in added] == ["B0001", "B0002"]
    assert (root / "工作区" / "阅读" / "mobile_outbox.json").is_file()

    status, list_raw, _ = _http_call(base, "/api/mobile/v1/outbox", token=token)
    assert status == 200
    assert len(json.loads(list_raw)["tickets"]) == 2

    status, ack_raw, _ = _http_call(
        base,
        "/api/mobile/v1/outbox/ack",
        method="POST",
        token=token,
        body={"ticket_ids": [added[0]["ticket_id"]]},
    )
    assert status == 200
    ack_doc = json.loads(ack_raw)
    assert ack_doc["acked"] == [added[0]["ticket_id"]]
    assert ack_doc["remaining"] == 1
    assert ack_doc["tickets"][0]["uid"] == "B0002"

    # HTTP 进度分片多设备写入与合并
    st1, _, _ = _http_call(
        base,
        "/api/mobile/v1/progress/B0001",
        method="POST",
        token=token,
        body={
            "device_id": "mobile-1",
            "ordinal": 3,
            "block_id": "B05",
            "char_offset": 10,
            "high_water_mark_ordinal": 3,
        },
    )
    assert st1 == 200
    st2, _, _ = _http_call(
        base,
        "/api/mobile/v1/progress/B0001",
        method="POST",
        token=token,
        body={
            "device_id": "desktop-1",
            "ordinal": 1,
            "block_id": "B01",
            "char_offset": 0,
            "high_water_mark_ordinal": 2,
        },
    )
    assert st2 == 200
    st_get, prog_raw, _ = _http_call(base, "/api/mobile/v1/progress/B0001", token=token)
    assert st_get == 200
    prog = json.loads(prog_raw)
    assert prog["merged_high_water_mark_ordinal"] == 3
    assert len(prog["shards"]) == 2

    # 跨书检索代理（校验 stateless=true, mode=related, exclude_books, threshold=0.45 且上限 15 条）
    st_cs, cs_raw, _ = _http_call(
        base,
        "/api/mobile/v1/cross-search",
        method="POST",
        token=token,
        body={"uid": "B0001", "question": "楚汉相争的制度原因", "source_text": "秦二世元年"},
    )
    assert st_cs == 200
    cs_doc = json.loads(cs_raw)
    assert cs_doc["ok"] is True
    assert len(cs_doc["hits"]) == 15
    assert set(cs_doc["hits"][0].keys()) == {"uid", "title", "type", "relevance", "best_path", "best_line", "snippet"}
    assert len(captured) == 1
    assert captured[0]["mode"] == "related"
    assert captured[0]["stateless"] is True
    assert captured[0]["exclude_books"] == ["B0001"]
    assert captured[0]["threshold"] == 0.45

    # 8765 不可达时返回干净 HTTP 503 JSON
    dead_port = _free_port()
    srv_down, _ = build_server(
        root,
        bind="127.0.0.1",
        port=0,
        token=token,
        search_url=f"http://127.0.0.1:{dead_port}",
        quiet=True,
    )
    t_down = threading.Thread(target=srv_down.serve_forever, daemon=True)
    t_down.start()
    try:
        down_base = f"http://127.0.0.1:{srv_down.server_address[1]}"
        st_503, body_503, _ = _http_call(
            down_base,
            "/api/mobile/v1/cross-search",
            method="POST",
            token=token,
            body={"uid": "B0001", "question": "离线测试"},
        )
        assert st_503 == 503
        assert json.loads(body_503)["ok"] is False
    finally:
        srv_down.shutdown()
        srv_down.server_close()


def test_gateway_cli_generates_pairing_uri_and_token(tmp_path):
    root = _build_synthetic_workspace(tmp_path)
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bookcorpus.mobile_gateway",
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--root",
            str(root),
            "--quiet",
        ],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        pair_line = ""
        deadline = time.time() + 10
        assert proc.stdout is not None
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    raise AssertionError(f"gateway exited early: {proc.stderr.read() if proc.stderr else ''}")
                time.sleep(0.05)
                continue
            if "bc-pair://v1?" in line:
                pair_line = line.strip()
                break
        assert "bc-pair://v1?" in pair_line
        token = pair_line.split("token=", 1)[1].strip()
        assert len(token) == 64

        base = f"http://127.0.0.1:{port}"
        st_401, _, _ = _http_call(base, "/api/mobile/v1/health")
        assert st_401 == 401
        st_200, _, _ = _http_call(base, "/api/mobile/v1/health", token=token)
        assert st_200 == 200
    finally:
        proc.terminate()
        proc.wait(timeout=5)
