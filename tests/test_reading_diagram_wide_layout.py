"""Wide zoom-modal layout and spatial expand-all regression checks for diagram_renderer.js."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "apps" / "reading" / "web" / "assets"

sys.path.insert(0, str(REPO / "tests" / "manual" / "demo"))
import shot


def _available_chrome() -> str | None:
    env = os.environ.get("CHROME_BIN")
    if env and Path(env).exists():
        return env
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _build_deep_causality_figure() -> dict:
    """Nine-rank chain with multi-block anchors simulating B0042/04 causality."""
    roles = ["structural", "trigger", "mechanism", "turning_point", "consequence"]
    nodes = [
        {
            "id": f"C{i}",
            "role": roles[min(len(roles) - 1, (i - 1) // 2)],
            "label": f"因果节点{i}",
            "detail": f"说明第{i}步的制度诱因与局势推进，用于验证宽屏展开后的排版尺寸与零重叠计算。",
            "blocks": [f"B{b:02d}" for b in range(i, min(i + 7, 35))],
            "source": "原文",
        }
        for i in range(1, 10)
    ]
    edges = [{"from": f"C{i}", "to": f"C{i + 1}", "label": f"推进{i}"} for i in range(1, 9)]
    edges.append({"from": "C3", "to": "C7", "label": "远因制约", "kind": "constraint"})
    return {
        "blueprint": "historical_causality",
        "caption": "九层因果链路验证",
        "note": "用于验证宽屏双排折行与块号折叠。",
        "data": {"nodes": nodes, "edges": edges},
    }


def _build_five_tier_power_figure() -> dict:
    """Five-rank hierarchy with up to 3 nodes per rank simulating B0325/05 power structure."""
    specs = [
        ("P1", "faction", ["B01", "B02", "B03", "B04", "B05", "B06"]),
        ("P2", "ally", ["B04", "B07"]),
        ("P3", "faction", ["B08", "B09", "B10"]),
        ("P4", "sovereign", ["B11", "B12", "B13", "B14", "B15"]),
        ("P5", "constraint", ["B03", "B05", "B16"]),
        ("P6", "faction", ["B17", "B18", "B19", "B20", "B21"]),
        ("P7", "rival", ["B22", "B23", "B24"]),
        ("P8", "ally", ["B25", "B26"]),
        ("P9", "rival", ["B27", "B28", "B29"]),
        ("P10", "faction", ["B30", "B31"]),
        ("P11", "constraint", ["B32", "B33", "B34", "B35", "B36"]),
        ("P12", "constraint", ["B11", "B12", "B31", "B33", "B34"]),
    ]
    nodes = [
        {
            "id": nid,
            "role": role,
            "label": f"阵营节点{nid}",
            "detail": f"势力{nid}的盟约义务、兵力约束与决策立场。",
            "blocks": blks,
            "source": "原文",
        }
        for nid, role, blks in specs
    ]
    edges = [
        {"from": "P1", "to": "P3", "label": "同盟", "relation": "alliance"},
        {"from": "P2", "to": "P4", "label": "制衡", "relation": "hostile"},
        {"from": "P3", "to": "P6", "label": "统治", "relation": "command"},
        {"from": "P4", "to": "P7", "label": "主攻", "relation": "hostile"},
        {"from": "P5", "to": "P8", "label": "中立保证", "relation": "constraint"},
        {"from": "P6", "to": "P9", "label": "施压", "relation": "hostile"},
        {"from": "P7", "to": "P12", "label": "抗衡", "relation": "hostile"},
        {"from": "P8", "to": "P10", "label": "改组", "relation": "constraint"},
        {"from": "P9", "to": "P12", "label": "实施", "relation": "constraint"},
    ]
    return {
        "blueprint": "power_hierarchy",
        "caption": "五层权力结构验证",
        "note": "用于验证五层权力图在宽屏下横排紧凑布局。",
        "data": {"nodes": nodes, "edges": edges},
    }


def _build_dense_spatial_figure() -> dict:
    """Twelve clustered horizontal spatial nodes simulating B0325/05 Western Front campaign."""
    coords = [
        (12, 26),
        (24, 28),
        (33, 30),
        (42, 34),
        (51, 32),
        (60, 34),
        (72, 28),
        (86, 22),
        (46, 56),
        (28, 72),
        (64, 62),
        (78, 74),
    ]
    nodes = [
        {
            "id": f"S{i}",
            "role": "corridor" if i % 2 else "stronghold",
            "label": f"战区节点{i}",
            "zone": "西线走廊",
            "x": x,
            "y": y,
            "detail": (
                f"第{i}处战区承担主力翼侧包抄、铁道推进或要塞封锁任务，"
                "展开全部说明后卡片明显增高，需要画布重排以保持零重叠。"
            ),
            "blocks": [f"B{10 + i:02d}", f"B{20 + i:02d}", f"B{30 + i:02d}"],
            "source": "原文",
        }
        for i, (x, y) in enumerate(coords, 1)
    ]
    edges = [
        {"from": f"S{i}", "to": f"S{i + 1}", "kind": "advance", "label": f"推进{i}", "route_group": "右翼"}
        for i in range(1, 12)
    ]
    return {
        "blueprint": "spatial_route",
        "caption": "十二节点空间态势验证",
        "note": "用于验证空间图展开说明后无卡片重叠。",
        "data": {"mode": "campaign", "scope_note": "西线战役推演", "nodes": nodes, "edges": edges},
    }


def _build_wide_fan_power_figure() -> dict:
    """Three-rank wide fan (8 in rank 0) simulating B0325/05 power structure."""
    nodes = [
        {
            "id": f"F{i}",
            "role": "faction",
            "label": f"各方势力{i}",
            "detail": "制度或同盟约束说明。",
            "blocks": ["B01", "B02", "B03", "B04", "B05"],
            "source": "原文",
        }
        for i in range(1, 9)
    ] + [
        {
            "id": "M1",
            "role": "sovereign",
            "label": "总参谋部",
            "detail": "核心统筹。",
            "blocks": ["B06", "B07"],
            "source": "原文",
        },
        {"id": "T1", "role": "rival", "label": "比利时", "detail": "中立防线。", "blocks": ["B08"], "source": "原文"},
        {"id": "T2", "role": "rival", "label": "法国", "detail": "主攻目标。", "blocks": ["B09"], "source": "原文"},
    ]
    edges = [{"from": f"F{i}", "to": "M1", "label": f"约束{i}", "relation": "constraint"} for i in range(1, 9)] + [
        {"from": "M1", "to": "T1", "label": "借道", "relation": "hostile"},
        {"from": "M1", "to": "T2", "label": "主攻", "relation": "hostile"},
    ]
    return {
        "blueprint": "power_hierarchy",
        "caption": "宽扇入三层权力结构",
        "note": "用于验证宽扇入权力图默认适应比例。",
        "data": {"nodes": nodes, "edges": edges},
    }


def test_diagram_renderer_js_syntax() -> None:
    res = subprocess.run(
        ["node", "--check", str(ASSETS / "diagram_renderer.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr


def test_wide_zoom_layout_and_spatial_expand_all() -> None:
    chrome = _available_chrome()
    if not chrome:
        pytest.skip("No headless Chrome/Chromium available")

    harness_html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="/assets/tokens.css">
<link rel="stylesheet" href="/assets/workbench.css">
</head>
<body>
<div id="host"></div>
<script src="/assets/common.js"></script>
<script src="/assets/diagram_renderer.js"></script>
</body>
</html>"""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/test.html":
                raw = harness_html.encode("utf-8")
                ctype = "text/html; charset=utf-8"
            elif self.path.startswith("/assets/"):
                rel = self.path[len("/assets/") :].split("?", 1)[0]
                target = (ASSETS / rel).resolve()
                if ASSETS not in target.parents or not target.is_file():
                    self.send_error(404)
                    return
                raw = target.read_bytes()
                ctype = "text/css; charset=utf-8" if rel.endswith(".css") else "application/javascript; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    shot.CHROME = chrome
    proc, profile, ws_url = shot.launch(1440, 900, "about:blank", 0)
    cdp = shot.connect_cdp(ws_url)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
    )
    cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{port}/test.html"})
    try:
        figures = {
            "causality": _build_deep_causality_figure(),
            "power": _build_five_tier_power_figure(),
            "power_fan": _build_wide_fan_power_figure(),
            "spatial": _build_dense_spatial_figure(),
        }

        def eval_js(expr: str) -> object:
            res = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
            if "exceptionDetails" in res:
                raise AssertionError(f"JS exception: {res['exceptionDetails']}")
            return (res.get("result") or {}).get("value")

        deadline = time.time() + 10
        while time.time() < deadline:
            if eval_js("Boolean(window.RD && window.RD.diagrams && window.RD.diagrams.openZoomModal)"):
                break
            time.sleep(0.1)

        out = eval_js(
            f"""(() => {{
              const figs = {json.dumps(figures, ensure_ascii=False)};
              const results = {{}};
              for (const [k, fig] of Object.entries(figs)) {{
                window.RD.diagrams.openZoomModal(fig);
                const body = document.getElementById('zoom-body');
                const qa1 = window.RD.diagrams.runCollisionAudit(body);
                const fitText1 = document.getElementById('btn-zoom-fit').textContent || '';
                const pct1 = parseInt(fitText1, 10) || 100;
                const moreCount = body.querySelectorAll('.blk-anchor-more').length;
                const expBtn = document.getElementById('btn-zoom-expand-all');
                expBtn.click();
                const qa2 = window.RD.diagrams.runCollisionAudit(body);
                const fitText2 = document.getElementById('btn-zoom-fit').textContent || '';
                const moreAfterExp = body.querySelectorAll('.blk-anchor-more').length;
                document.getElementById('btn-zoom-close').click();
                results[k] = {{
                  overlaps1: qa1.overlaps,
                  pct1,
                  fitText1,
                  moreCount,
                  overlaps2: qa2.overlaps,
                  fitText2,
                  moreAfterExp,
                }};
              }}
              return results;
            }})()"""
        )
        assert isinstance(out, dict)
        assert out["causality"]["overlaps1"] == 0
        assert out["causality"]["overlaps2"] == 0
        assert out["causality"]["pct1"] >= 60, out["causality"]
        assert out["causality"]["moreCount"] > 0
        assert out["causality"]["moreAfterExp"] == 0

        assert out["power"]["overlaps1"] == 0
        assert out["power"]["overlaps2"] == 0
        assert out["power"]["pct1"] >= 60, out["power"]
        assert out["power"]["moreCount"] > 0
        assert out["power"]["moreAfterExp"] == 0

        assert out["power_fan"]["overlaps1"] == 0
        assert out["power_fan"]["overlaps2"] == 0
        assert out["power_fan"]["pct1"] >= 60, out["power_fan"]

        assert out["spatial"]["overlaps1"] == 0
        assert out["spatial"]["overlaps2"] == 0, out["spatial"]
        assert out["spatial"]["pct1"] >= 60, out["spatial"]
    finally:
        cdp.ws.close()
        shot._terminate(proc)
        shutil.rmtree(profile, ignore_errors=True)
        srv.shutdown()
        srv.server_close()
