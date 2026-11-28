"""纯 Python QR 编码矩阵自解码校验 + MobileGatewayManager 生命周期/安全/LAN 拉取测试。"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bookcorpus.mobile_gateway import manager as mgr_mod
from bookcorpus.mobile_gateway import qr as qr_mod


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---- 1. Pure-Python QR round-trip verifier ----------------------------
_FORMAT_COORDS_TOP_LEFT = (
    (8, 0),
    (8, 1),
    (8, 2),
    (8, 3),
    (8, 4),
    (8, 5),
    (8, 7),
    (8, 8),
    (7, 8),
    (5, 8),
    (4, 8),
    (3, 8),
    (2, 8),
    (1, 8),
    (0, 8),
)


def _gf_poly_eval(poly: list[int], x: int) -> int:
    acc = 0
    for coef in poly:
        acc = qr_mod._gf_mul(acc, x) ^ coef
    return acc


def _decode_qr_byte_payload(matrix: qr_mod.QrMatrix) -> bytes:
    """独立按 ISO/IEC 18004 规范验证定位符、格式/版本信息、RS 伴随式为 0 并反向解出 Byte 模式载荷。"""
    v = matrix.version
    size = matrix.size
    mods = matrix.modules
    assert size == 17 + 4 * v

    # 1. 验证三个 7x7 定位符
    for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
        for dr in range(7):
            for dc in range(7):
                want = (dr in (0, 6) or dc in (0, 6)) or (2 <= dr <= 4 and 2 <= dc <= 4)
                assert mods[top + dr][left + dc] is want, (v, top, left, dr, dc)

    # 2. 验证暗模块与定时线
    assert mods[4 * v + 9][8] is True
    for i in range(8, size - 8):
        assert mods[6][i] is (i % 2 == 0)
        assert mods[i][6] is (i % 2 == 0)

    # 3. 验证格式信息（左上与拆分双份一致）与掩码号
    fmt_bits = 0
    for idx, (r, c) in enumerate(_FORMAT_COORDS_TOP_LEFT):
        if mods[r][c]:
            fmt_bits |= 1 << (14 - idx)
    raw_fmt = fmt_bits ^ 0x5412
    data5 = raw_fmt >> 10
    assert (data5 >> 3) == 0b00  # ECC-M
    mask_id = data5 & 0b111
    assert qr_mod._format_bits(mask_id) == fmt_bits
    assert mask_id == matrix.mask

    # 4. Version 7+ 验证 18-bit 版本信息
    if v >= 7:
        want_vbits = qr_mod._version_bits(v)
        got_a = 0
        got_b = 0
        for i in range(18):
            r = i // 3
            c = size - 11 + (i % 3)
            if mods[r][c]:
                got_a |= 1 << i
            if mods[c][r]:
                got_b |= 1 << i
        assert got_a == want_vbits
        assert got_b == want_vbits

    # 5. 重建功能位掩码并逆向读取全部数据+纠错码字
    _, is_func = qr_mod._build_function_grid(v)
    bits: list[int] = []
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if not is_func[r][c]:
                    val = mods[r][c]
                    if qr_mod._mask_condition(mask_id, r, c):
                        val = not val
                    bits.append(1 if val else 0)
        col -= 2
        upward = not upward

    codewords = []
    for i in range(0, len(bits) - (len(bits) % 8), 8):
        byte = 0
        for b in bits[i : i + 8]:
            byte = (byte << 1) | b
        codewords.append(byte)

    total_data_cw, ec_per_block, g1_blocks, g1_data, g2_blocks, g2_data = qr_mod._ECC_M_SPECS[v]
    num_blocks = g1_blocks + g2_blocks
    expected_total_cw = total_data_cw + num_blocks * ec_per_block
    assert len(codewords) == expected_total_cw

    # 6. 解交织并验证每个块的 Reed-Solomon 伴随式全为 0
    block_data_lens = [g1_data] * g1_blocks + [g2_data] * g2_blocks
    data_blocks: list[list[int]] = [[] for _ in range(num_blocks)]
    ec_blocks: list[list[int]] = [[] for _ in range(num_blocks)]
    pos = 0
    max_data = max(block_data_lens)
    for idx in range(max_data):
        for b in range(num_blocks):
            if idx < block_data_lens[b]:
                data_blocks[b].append(codewords[pos])
                pos += 1
    for _ in range(ec_per_block):
        for b in range(num_blocks):
            ec_blocks[b].append(codewords[pos])
            pos += 1
    assert pos == len(codewords)

    for b in range(num_blocks):
        full_block = data_blocks[b] + ec_blocks[b]
        for j in range(ec_per_block):
            syndrome = _gf_poly_eval(full_block, qr_mod._GF_EXP[j])
            assert syndrome == 0, f"RS syndrome failed for v={v} block={b} root={j}"

    # 7. 解出 Byte 模式（0100）原始字节
    data_bytes = [byte for blk in data_blocks for byte in blk]
    bitstream = "".join(f"{byte:08b}" for byte in data_bytes)
    mode = int(bitstream[0:4], 2)
    assert mode == 0b0100
    len_bits = 8 if v <= 9 else 16
    length = int(bitstream[4 : 4 + len_bits], 2)
    start = 4 + len_bits
    out = bytearray()
    for i in range(length):
        out.append(int(bitstream[start + i * 8 : start + (i + 1) * 8], 2))
    return bytes(out)


@pytest.mark.parametrize(
    ("payload", "min_version"),
    [
        ("hello", 1),
        ("http://192.168.1.10:8780/m#t=" + ("a" * 64), 5),
        (
            "bc-pair://v1?base=http%3A%2F%2F192.168.1.50%3A8780&token=" + ("f" * 64),
            6,
        ),
        (
            "bc-pair://v1?base=http%3A%2F%2Fbook-corpus-workstation.tail12345.ts.net%3A8780&token="
            + ("0123456789abcdef" * 4)
            + "&label=测试中文配对码",
            7,
        ),
    ],
)
def test_qr_encoder_roundtrip_and_white_svg(payload: str, min_version: int):
    mat = qr_mod.encode_qr(payload)
    assert mat.version >= min_version
    decoded = _decode_qr_byte_payload(mat)
    assert decoded.decode("utf-8") == payload

    svg = qr_mod.render_svg(payload, border=4, module_px=4)
    assert svg.startswith("<svg ")
    assert 'fill="#FFFFFF"' in svg
    assert 'fill="#111111"' in svg
    expected_dim = (mat.size + 8) * 4
    assert f'viewBox="0 0 {expected_dim} {expected_dim}"' in svg


def test_qr_encoder_rejects_oversized_payload():
    with pytest.raises(ValueError):
        qr_mod.encode_qr("x" * 600)


# ---- 2. MobileGatewayManager lifecycle, security & LAN pull -----------
def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "books" / "B0001" / "md").mkdir(parents=True)
    (root / "books" / "B0001" / "source").mkdir(parents=True)
    (root / "books" / "_收件箱").mkdir(parents=True)
    (root / "工作区" / "阅读").mkdir(parents=True)
    (root / "工作区" / "运维").mkdir(parents=True)
    (root / "books.toml").write_text("schema = 2\n", encoding="utf-8")
    (root / "books" / "B0001" / "book.json").write_text(
        json.dumps({"uid": "B0001", "title": "测试书", "author": "作者", "subjects": ["文学"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "books" / "B0001" / "md" / "01_正文.md").write_text("# 第一章\n\n正文。\n", encoding="utf-8")
    return root


def _health_status(port: int, token: str) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/mobile/v1/health",
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def test_manager_config_permissions_lifecycle_and_token_secrecy(tmp_path: Path):
    root = _make_root(tmp_path)
    port = _free_port()
    mgr = mgr_mod.MobileGatewayManager(root, port_override=port)

    cfg = mgr.load_config()
    assert cfg["port"] == port
    assert len(cfg["token"]) == 64
    assert mgr.config_path.is_file()
    mode = mgr.config_path.stat().st_mode & 0o777
    assert mode == 0o600

    # 新实例读取同一份持久化密钥
    mgr2 = mgr_mod.MobileGatewayManager(root, port_override=port)
    assert mgr2.load_config()["token"] == cfg["token"]

    try:
        started = mgr.start()
        assert started["ok"] is True
        st = started["status"]
        assert st["running"] is True
        assert st["managed"] is True
        pid = st["pid"]
        assert isinstance(pid, int)
        assert pid > 0

        # 密钥绝不能暴露在 /proc/<pid>/cmdline 或日志文件中
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.is_file():
            cmdline_text = cmdline_path.read_bytes().decode("utf-8", "replace")
            assert cfg["token"] not in cmdline_text
        if mgr.log_path.is_file():
            assert cfg["token"] not in mgr.log_path.read_text(encoding="utf-8", errors="replace")

        assert _health_status(port, cfg["token"]) == 200
        assert _health_status(port, "0" * 64) == 401

        # 轮换密钥后自动重启：旧密钥 401、新密钥 200
        old_token = cfg["token"]
        rotated = mgr.rotate_token()
        assert rotated["ok"] is True
        assert rotated["restarted"] is True
        new_token = mgr.load_config()["token"]
        assert new_token != old_token
        assert _health_status(port, old_token) == 401
        assert _health_status(port, new_token) == 200

        stopped = mgr.stop()
        assert stopped["ok"] is True
        assert stopped["status"]["running"] is False
        assert not mgr.pid_path.exists()
    finally:
        mgr.stop()


def test_manager_detects_external_port_conflict_and_refuses_to_kill(tmp_path: Path):
    class _DummyHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            self.send_response(401)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _DummyHandler)
    ext_port = int(srv.server_address[1])
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        root = _make_root(tmp_path)
        mgr = mgr_mod.MobileGatewayManager(root, port_override=ext_port)
        st = mgr.status()
        assert st["state"] == "external"
        assert st["managed"] is False
        assert st["external_conflict"] is True

        stop_res = mgr.stop()
        assert stop_res["ok"] is False
        assert "占用" in stop_res["error"] or "托管" in stop_res["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_enumerate_host_candidates_filters_virtual_and_tun(monkeypatch: pytest.MonkeyPatch):
    fake_ip_json = json.dumps(
        [
            {"ifname": "singbox_tun", "addr_info": [{"family": "inet", "local": "172.19.0.1"}]},
            {"ifname": "docker0", "addr_info": [{"family": "inet", "local": "172.17.0.1"}]},
            {"ifname": "vethabc123", "addr_info": [{"family": "inet", "local": "10.0.0.5"}]},
            {"ifname": "wlp3s0", "addr_info": [{"family": "inet", "local": "192.168.1.42"}]},
            {"ifname": "tailscale0", "addr_info": [{"family": "inet", "local": "100.88.12.34"}]},
        ]
    )
    monkeypatch.setattr(mgr_mod.shutil, "which", lambda _name: "/usr/sbin/ip")
    monkeypatch.setattr(
        mgr_mod.subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess(args=[], returncode=0, stdout=fake_ip_json, stderr=""),
    )
    info = mgr_mod.enumerate_host_candidates()
    cands = info["candidates"]
    hosts = [c["host"] for c in cands]
    filtered_hosts = [f["host"] for f in info["filtered"]]
    assert "192.168.1.42" in hosts
    assert "100.88.12.34" in hosts
    assert "127.0.0.1" in hosts
    assert "172.19.0.1" not in hosts
    assert "172.17.0.1" not in hosts
    assert "10.0.0.5" not in hosts
    assert {"172.19.0.1", "172.17.0.1", "10.0.0.5"} <= set(filtered_hosts)
    kinds = {c["host"]: c["kind"] for c in cands}
    assert kinds["192.168.1.42"] == "lan"
    assert kinds["100.88.12.34"] == "tailscale"
    assert kinds["127.0.0.1"] == "loopback"


def test_lan_pull_ssrf_auth_fetch_and_send_to_import(tmp_path: Path):
    root = _make_root(tmp_path)
    mgr = mgr_mod.MobileGatewayManager(root, port_override=_free_port())

    # SSRF 守卫：拒绝公网地址与非法 scheme
    with pytest.raises(ValueError):
        mgr.lan_pull_preview("http://8.8.8.8:9721/", "123456")
    with pytest.raises(ValueError):
        mgr.lan_pull_preview("file:///etc/passwd", "123456")

    # 构造模拟手机 BanRead 9721 EphemeralLanHttpServer（/share/v1/manifest, /companion, /book）
    manifest_bytes = json.dumps(
        {
            "title": "手机读完的书",
            "author": "某作家",
            "workId": "work-1",
            "bookSourceSha256": "c" * 64,
            "mapCount": 2,
            "noteCount": 5,
            "qaCount": 1,
            "includeBookFile": True,
            "bookFileName": "手机读完的书.epub",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    companion_bytes = json.dumps({"notes": [{"text": "摘录1"}]}, ensure_ascii=False).encode("utf-8")
    book_bytes = b"PK\x03\x04banread-book"
    expected_code = "654321"

    class _MockBanReadHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            if self.headers.get("X-BanRead-Code") != expected_code:
                self.send_response(401)
                self.end_headers()
                return
            payload_map = {
                "/share/v1/manifest": ("application/json", manifest_bytes),
                "/share/v1/companion": ("application/json", companion_bytes),
                "/share/v1/book": ("application/octet-stream", book_bytes),
            }
            entry = payload_map.get(self.path)
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

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockBanReadHandler)
    lan_port = int(srv.server_address[1])
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{lan_port}/"
        # 错配对码 → ValueError（HTTP 401）
        with pytest.raises(ValueError, match="401"):
            mgr.lan_pull_preview(url, "000000")

        # 正确配对码 → 预览成功
        prev = mgr.lan_pull_preview(url, expected_code)
        assert prev["ok"] is True
        assert prev["manifest"]["title"] == "手机读完的书"

        # 拉取并落盘到 mobile_inbox
        fetched = mgr.lan_pull_fetch(url, expected_code)
        assert fetched["ok"] is True
        item = fetched["item"]
        assert item["title"] == "手机读完的书"
        assert item["has_book"] is True
        assert item["source"] == "lan_pull"
        assert item["note_count"] == 5

        listed = mgr.list_mobile_inbox()
        assert len(listed) == 1
        assert listed[0]["item_id"] == item["item_id"]

        # 一键投递原书到 books/_收件箱/
        sent = mgr.send_inbox_book_to_import(item["item_id"])
        assert sent["ok"] is True
        inbox_file = root / "books" / "_收件箱" / sent["name"]
        assert inbox_file.is_file()
        assert inbox_file.read_bytes() == book_bytes

        listed_after = mgr.list_mobile_inbox()
        assert listed_after[0]["imported_to_inbox"] == sent["name"]
    finally:
        srv.shutdown()
        srv.server_close()
        assert not (root / "books" / "_收件箱" / ".part").exists()


def test_lan_pull_blocks_http_redirect_ssrf(tmp_path: Path):
    """3xx 重定向到公网/云元数据地址必须被拦截，防止绕过私网 SSRF 守卫。"""
    root = _make_root(tmp_path)
    mgr = mgr_mod.MobileGatewayManager(root, port_override=_free_port())
    manifest_bytes = json.dumps(
        {"title": "重定向测试书", "includeBookFile": True, "bookFileName": "redirect.epub"},
        ensure_ascii=False,
    ).encode("utf-8")
    companion_bytes = b"{}"

    class _RedirectHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            if self.path == "/share/v1/manifest" and self.headers.get("X-BanRead-Code") == "111111":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()
                return
            if self.path == "/share/v1/manifest":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(manifest_bytes)))
                self.end_headers()
                self.wfile.write(manifest_bytes)
                return
            if self.path == "/share/v1/companion":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(companion_bytes)))
                self.end_headers()
                self.wfile.write(companion_bytes)
                return
            if self.path == "/share/v1/book":
                self.send_response(302)
                self.send_header("Location", "http://8.8.8.8/evil.epub")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    lan_port = int(srv.server_address[1])
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{lan_port}/"
        with pytest.raises(ValueError, match="重定向"):
            mgr.lan_pull_preview(url, "111111")
        with pytest.raises(ValueError, match="重定向"):
            mgr.lan_pull_fetch(url, "222222", include_book=True)
    finally:
        srv.shutdown()
        srv.server_close()


def test_send_inbox_book_to_import_enforces_zip_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """解压 .bcshare 内原书超过 MAX_LAN_BOOK_BYTES 时必须中断、清理 .part 并抛 ValueError。"""
    import zipfile

    root = _make_root(tmp_path)
    mgr = mgr_mod.MobileGatewayManager(root, port_override=_free_port())
    monkeypatch.setattr(mgr_mod, "MAX_LAN_BOOK_BYTES", 1024)

    item_dir = mgr.mobile_inbox_dir / "up-20261128-zipbomb"
    item_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = item_dir / "bomb.bcshare"
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "share_manifest.json",
            json.dumps({"title": "超限样本", "includeBookFile": True}, ensure_ascii=False),
        )
        zf.writestr("book/bomb.epub", b"A" * 2048)

    meta = mgr.register_uploaded_share_package(pkg_path, "bomb.bcshare")
    assert meta["has_book"] is True

    with pytest.raises(ValueError, match="体积上限"):
        mgr.send_inbox_book_to_import(meta["item_id"])

    inbox_dir = root / "books" / "_收件箱"
    assert not (inbox_dir / "bomb.epub").exists()
    assert not list(inbox_dir.glob("*.part"))
