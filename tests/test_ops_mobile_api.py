"""操作台 /api/mobile/* 全链路 API、local_ok 安全边界与不触碰 WRITE_ROUTES 回归测试。"""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OPS_SERVER_PATH = REPO / "apps" / "ops" / "server.py"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _load_ops_module():
    spec = importlib.util.spec_from_file_location("ops_server_mobile_test", OPS_SERVER_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_ops_root(tmp_path: Path) -> Path:
    root = tmp_path / "ops_mobile_ws"
    for rel in (
        "books/B0001/md",
        "books/B0001/source",
        "books/_收件箱",
        "build",
        "tables",
        "工作区/阅读",
        "工作区/运维/jobs",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    (root / "books" / "B0001" / "book.json").write_text(
        json.dumps(
            {
                "uid": "B0001",
                "title": "史记选读",
                "author": "司马迁",
                "subjects": ["人文社科", "历史"],
                "retention": "keep",
                "sources": [{"format": "epub", "bytes": 128, "sha256": "a" * 64, "primary_src": 1}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "books" / "B0001" / "md" / "01_项羽本纪.md").write_text(
        "# 项羽本纪\n\n项籍者，下相人也，字羽。\n", encoding="utf-8"
    )
    conn = sqlite3.connect(root / "build" / "library.db")
    conn.executescript(
        """
        CREATE TABLE book(uid TEXT PRIMARY KEY, title TEXT, author TEXT, subject1 TEXT,
          subject2 TEXT, path TEXT, series TEXT, volume TEXT, retention TEXT, path_state TEXT);
        CREATE TABLE source(uid TEXT, file TEXT, format TEXT, bytes INT, sha256 TEXT, primary_src INT);
        CREATE TABLE derived(uid TEXT, kind TEXT, status TEXT, chapters INT, bytes INT, source_sha256 TEXT);
        CREATE TABLE book_award(uid TEXT, award TEXT, year TEXT, result TEXT);
        """
    )
    conn.execute(
        "INSERT INTO book VALUES ('B0001','史记选读','司马迁','人文社科','历史','B0001','','','keep','active')"
    )
    conn.execute("INSERT INTO source VALUES ('B0001','books/B0001/source/a.epub','epub',128,'aaaa',1)")
    conn.execute("INSERT INTO derived VALUES ('B0001','md','done',1,100,'aaaa')")
    conn.commit()
    conn.close()
    return root


def _req(
    base: str,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    hdrs = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(f"{base}{path}", data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), {k.lower(): v for k, v in exc.headers.items()}


def test_mobile_routes_stay_outside_write_routes():
    mod = _load_ops_module()
    assert not any(route.startswith("/api/mobile/") for route in mod.WRITE_ROUTES)


def test_ops_mobile_full_api_flow(tmp_path: Path):
    mod = _load_ops_module()
    root = _build_ops_root(tmp_path)
    mobile_port = _free_port()
    env = mod.Env(str(root), mobile_port=mobile_port)
    app = mod.App(env)

    class _BoundHandler(mod.Handler):
        pass

    _BoundHandler.app = app
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _BoundHandler)
    ops_port = int(httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{ops_port}"

    try:
        # 1. local_ok() 安全守卫对所有 /api/mobile/* 生效
        st_bad_get, _, _ = _req(base, "GET", "/api/mobile/status", headers={"Host": "evil.example"})
        assert st_bad_get == 403
        st_bad_post, _, _ = _req(base, "POST", "/api/mobile/start", payload={}, headers={"Host": "evil.example"})
        assert st_bad_post == 403

        # 2. /api/services 包含第 5 张移动网关状态卡
        st_svc, raw_svc, _ = _req(base, "GET", "/api/services")
        assert st_svc == 200
        services = json.loads(raw_svc)["services"]
        mob_card = next((s for s in services if s["key"] == "mobile"), None)
        assert mob_card is not None
        assert mob_card["internal"] is True
        assert mob_card["url"] == "#mobile"
        assert mob_card["running"] is False

        # 3. 初始状态含离线二维码与候选地址
        st_st, raw_st, _ = _req(base, "GET", "/api/mobile/status")
        assert st_st == 200
        status_doc = json.loads(raw_st)
        assert status_doc["ok"] is True
        assert status_doc["running"] is False
        assert "<svg" in status_doc["pair_qr_svg"]
        assert "<svg" in status_doc["web_qr_svg"]
        assert status_doc["pair_uri"].startswith("bc-pair://v1?")
        assert "/m#t=" in status_doc["web_drop_uri"]

        # 4. 修改配置并启动托管网关
        st_cfg, raw_cfg, _ = _req(
            base,
            "POST",
            "/api/mobile/config",
            payload={"bind": "127.0.0.1", "preferred_host": "127.0.0.1", "allow_upload": True},
        )
        assert st_cfg == 200
        assert json.loads(raw_cfg)["status"]["bind"] == "127.0.0.1"

        st_start, raw_start, _ = _req(base, "POST", "/api/mobile/start", payload={})
        assert st_start == 200
        start_doc = json.loads(raw_start)
        assert start_doc["ok"] is True
        assert start_doc["status"]["running"] is True
        assert start_doc["status"]["managed"] is True

        # 服务卡同步显示运行中
        _, raw_svc2, _ = _req(base, "GET", "/api/services")
        mob_card2 = next(s for s in json.loads(raw_svc2)["services"] if s["key"] == "mobile")
        assert mob_card2["running"] is True

        # 5. 可读书目查询 + Outbox 增删清
        st_cat, raw_cat, _ = _req(base, "GET", "/api/mobile/catalog?q=%E5%8F%B2%E8%AE%B0")
        assert st_cat == 200
        cat_doc = json.loads(raw_cat)
        assert [b["uid"] for b in cat_doc["books"]] == ["B0001"]

        st_enq, raw_enq, _ = _req(
            base, "POST", "/api/mobile/outbox/enqueue", payload={"uids": ["B0001"], "include_cache": True}
        )
        assert st_enq == 200
        enq_doc = json.loads(raw_enq)
        assert len(enq_doc["tickets"]) == 1
        tid = enq_doc["tickets"][0]["ticket_id"]

        st_rm, raw_rm, _ = _req(base, "POST", "/api/mobile/outbox/remove", payload={"ticket_ids": [tid]})
        assert st_rm == 200
        assert json.loads(raw_rm)["tickets"] == []

        _req(base, "POST", "/api/mobile/outbox/enqueue", payload={"uids": ["B0001"], "include_cache": False})
        st_clr, raw_clr, _ = _req(base, "POST", "/api/mobile/outbox/clear", payload={})
        assert st_clr == 200
        assert json.loads(raw_clr)["removed"] == 1

        # 6. .bcbook 打包下载
        st_bun, bun_bytes, bun_hdrs = _req(base, "GET", "/api/mobile/bundle?uid=B0001&include_cache=1")
        assert st_bun == 200
        assert "application/zip" in bun_hdrs.get("content-type", "")
        with zipfile.ZipFile(io.BytesIO(bun_bytes)) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["uid"] == "B0001"
        assert len(manifest["book_source_sha256"]) == 64

        # 7. 模拟手机上报无 high_water_mark_ordinal 进度 → 操作台进度总览可见
        token = app._mobile_manager().load_config()["token"]
        gw_base = f"http://127.0.0.1:{mobile_port}"
        st_pg_post, _, _ = _req(
            gw_base,
            "POST",
            "/api/mobile/v1/progress/B0001",
            payload={"device_id": "pixel-9", "ordinal": 1, "block_id": "B01", "char_offset": 8},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert st_pg_post == 200

        st_pg_get, raw_pg, _ = _req(base, "GET", "/api/mobile/progress")
        assert st_pg_get == 200
        pg_books = json.loads(raw_pg)["books"]
        assert len(pg_books) == 1
        assert pg_books[0]["uid"] == "B0001"
        assert pg_books[0]["title"] == "史记选读"

        # 8. 密钥轮换并验证旧密钥失效、新密钥可用
        st_rot, raw_rot, _ = _req(base, "POST", "/api/mobile/token/rotate", payload={})
        assert st_rot == 200
        assert json.loads(raw_rot)["restarted"] is True
        new_token = app._mobile_manager().load_config()["token"]
        assert new_token != token
        st_old, _, _ = _req(gw_base, "GET", "/api/mobile/v1/health", headers={"Authorization": f"Bearer {token}"})
        assert st_old == 401
        st_new, _, _ = _req(gw_base, "GET", "/api/mobile/v1/health", headers={"Authorization": f"Bearer {new_token}"})
        assert st_new == 200

        # 9. 局域网拉取手机 BanRead 分享 + 一键投递到收件箱
        manifest_bytes = json.dumps(
            {
                "title": "局域网传书",
                "author": "测试",
                "includeBookFile": True,
                "bookFileName": "局域网传书.epub",
                "noteCount": 2,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        companion_bytes = json.dumps({"notes": [{"text": "摘录"}]}, ensure_ascii=False).encode("utf-8")
        book_blob = b"PK\x03\x04lan-pulled-book"

        class _LanHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:
                if self.headers.get("X-BanRead-Code") != "112233":
                    self.send_response(401)
                    self.end_headers()
                    return
                routes = {
                    "/share/v1/manifest": ("application/json", manifest_bytes),
                    "/share/v1/companion": ("application/json", companion_bytes),
                    "/share/v1/book": ("application/octet-stream", book_blob),
                }
                entry = routes.get(self.path)
                if entry is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                ctype, body = entry
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        lan_srv = ThreadingHTTPServer(("127.0.0.1", 0), _LanHandler)
        lan_port = int(lan_srv.server_address[1])
        lan_thread = threading.Thread(target=lan_srv.serve_forever, daemon=True)
        lan_thread.start()
        try:
            st_prev, raw_prev, _ = _req(
                base,
                "POST",
                "/api/mobile/lan-pull",
                payload={"url": f"http://127.0.0.1:{lan_port}/", "code": "112233", "action": "preview"},
            )
            assert st_prev == 200
            assert json.loads(raw_prev)["ok"] is True

            st_fetch, raw_fetch, _ = _req(
                base,
                "POST",
                "/api/mobile/lan-pull",
                payload={"url": f"http://127.0.0.1:{lan_port}/", "code": "112233", "action": "fetch"},
            )
            assert st_fetch == 200
            item_id = json.loads(raw_fetch)["item"]["item_id"]

            st_inb, raw_inb, _ = _req(base, "GET", "/api/mobile/inbox")
            assert st_inb == 200
            assert any(i["item_id"] == item_id for i in json.loads(raw_inb)["items"])

            st_imp, raw_imp, _ = _req(base, "POST", "/api/mobile/inbox/to-import", payload={"item_id": item_id})
            assert st_imp == 200
            imp_doc = json.loads(raw_imp)
            assert imp_doc["ok"] is True
            assert (root / "books" / "_收件箱" / imp_doc["name"]).is_file()
        finally:
            lan_srv.shutdown()
            lan_srv.server_close()

        # 10. 停止网关
        st_stop, raw_stop, _ = _req(base, "POST", "/api/mobile/stop", payload={})
        assert st_stop == 200
        assert json.loads(raw_stop)["status"]["running"] is False
    finally:
        app.mobile_stop()
        app.runner.stopping = True
        httpd.shutdown()
        httpd.server_close()
