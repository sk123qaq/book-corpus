#!/usr/bin/env python3
"""Local OpenAI-compatible stub for driving the reading workbench without a real model.

  python3 .agents/skills/verify-book-corpus/scripts/mock_reading_model.py --port 18790

Point the reading server at it:

  OPENCODE_GO_API_KEY=sk-mock \
  READING_MODEL_URL=http://127.0.0.1:18790/v1/chat/completions \
  python apps/reading/server.py --root "$ROOT" --port 18771 --quiet

It answers three shapes: chapter-map prompts with a document built from the
prompt's blocks, diagram/extension prompts with a schema-valid blueprint built
from the schema hint in the system prompt, and everything else with one
sentence of plain text. It is an evidence stub, not a model quality benchmark.
"""

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _map_doc(payload):
    chapter = payload.get("chapter") or {}
    book = payload.get("book") or {}
    units = []
    non_semantic = []
    for index, block in enumerate(payload.get("blocks") or [], start=1):
        bid = block.get("id")
        text = (block.get("text") or "").strip()
        if not bid:
            continue
        if not text:
            non_semantic.append({"block": bid, "reason": "no text"})
            continue
        units.append(
            {
                "id": "U%02d" % index,
                "kind": "claim",
                "importance": "detail",
                "hard_info": False,
                "summary": "第 %d 个原文块" % index,
                "quotes": [{"block": bid, "text": text[:8]}],
                "source_blocks": [bid],
                "relations": [],
            }
        )
    return {
        "schema": 1,
        "uid": chapter.get("uid") or book.get("uid"),
        "n": chapter.get("n") or payload.get("n"),
        "source_sha256": chapter.get("source_sha256"),
        "prompt_version": "chapter-model-v1",
        "units": units,
        "sections": [{"id": "S01", "title": "全文", "summary": "全章", "unit_ids": [u["id"] for u in units]}],
        "terms": [],
        "figures": [],
        "non_semantic": non_semantic,
        "folds": [],
    }


FIGURE = {
    "caption": "两步关系",
    "family": "flow",
    "reading_note": "从原文到补充",
    "summary": "[原文] 只讲选区本身。",
    "data": {
        "nodes": [{"id": "N1", "label": "原文"}, {"id": "N2", "label": "补充"}],
        "edges": [{"from": "N1", "to": "N2"}],
    },
}

DAG_BLUEPRINTS = {
    "argument_map", "lineage_dag", "power_hierarchy", "spatial_route",
    "relation_graph", "flow_swimlane", "decision_tree", "derivation_dag",
    "system_flow", "causal_loop",
}
CURVE_BLUEPRINTS = {"tension_curve", "sensitivity_chart", "metric_chart"}
TABLE_BLUEPRINTS = {"term_contrast", "concept_matrix", "element_matrix"}


def _pick_blocks(text, default="B01"):
    ids = re.findall(r"\bB\d{2,}\b", text or "")
    out = []
    for bid in ids:
        if bid not in out:
            out.append(bid)
    return (out or [default])[:3]


def _blueprint_payload(system, user):
    m = re.search(r'"blueprint":\s*"([a-z_]+)"', system or "")
    blueprint = m.group(1) if m else "argum" + "ent_map"
    blocks = _pick_blocks(user)
    leaf = lambda i: blocks[i % len(blocks)]
    base = {
        "blueprint": blueprint,
        "caption": "结构骨架",
        "reading_note": "先看节点，再点块号回原文复核",
        "summary": "[原文] 依据章节真实段落抽出的结构。",
    }
    if blueprint in DAG_BLUEPRINTS or blueprint == "fallback_list":
        base["data"] = {
            "nodes": [
                {"id": "N1", "role": "premise", "label": "起点", "detail": "章节开篇", "blocks": [leaf(0)], "source": "原文"},
                {"id": "N2", "role": "inference", "label": "转折", "detail": "推进一层", "blocks": [leaf(1)], "source": "原文"},
                {"id": "N3", "role": "conclusion", "label": "落点", "detail": "结论", "blocks": [leaf(2)], "source": "补充"},
            ],
            "edges": [{"from": "N1", "to": "N2"}, {"from": "N2", "to": "N3"}],
        }
    elif blueprint in CURVE_BLUEPRINTS:
        base["data"] = {
            "yLabel": "强度",
            "points": [
                {"beat": "起", "tension": 20, "emotion": "铺垫", "blocks": [leaf(0)], "source": "原文"},
                {"beat": "承", "tension": 55, "emotion": "升温", "blocks": [leaf(1)], "source": "原文"},
                {"beat": "合", "tension": 85, "emotion": "爆发", "blocks": [leaf(2)], "source": "原文"},
            ],
        }
    elif blueprint == "parallel_timeline":
        base["data"] = {
            "lanes": ["主线", "副线"],
            "rows": [
                {"time": "起", "cells": [
                    {"title": "开端", "note": "", "blocks": [leaf(0)], "source": "原文"},
                    {"title": "前史", "note": "", "blocks": [leaf(1)], "source": "补充"},
                ]},
                {"time": "合", "cells": [
                    {"title": "收束", "note": "", "blocks": [leaf(2)], "source": "原文"},
                    None,
                ]},
            ],
        }
    elif blueprint == "quadrant_matrix":
        base["data"] = {
            "xAxis": "横轴（低 → 高）",
            "yAxis": "纵轴（低 → 高）",
            "quadrants": [
                {"pos": "q-top-left", "title": "左上", "items": [{"text": "要点甲", "blocks": [leaf(0)], "source": "原文"}]},
                {"pos": "q-top-right", "title": "右上", "items": [{"text": "要点乙", "blocks": [leaf(1)], "source": "补充"}]},
            ],
        }
    else:
        base["data"] = {
            "columns": ["列一", "列二", "列三"],
            "rows": [{"key": "行一", "everyday": "日常义", "author": "作者义", "blocks": [leaf(0)], "source": "原文"}],
        }
    return base


def _extension_payload(system, user):
    blocks = _pick_blocks(user)
    if "extension_checklist" in (system or ""):
        return {
            "blueprint": "extension_checklist",
            "caption": "行动自检",
            "reading_note": "适用前置条件",
            "summary": "按条目逐项自检。",
            "items": [
                {"text": "检查项一", "detail": "避坑说明", "checked": False, "blocks": [blocks[0]], "source": "原文"},
            ],
        }
    return {
        "blueprint": "extension_dossier",
        "caption": "意象词典",
        "reading_note": "阅读体验要点",
        "summary": "两层基调。",
        "items": [{"title": "意象甲", "body": "舞台潜台词", "blocks": [blocks[0]], "source": "原文"}],
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        system = "\n".join(m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system")
        user = next((m.get("content", "") for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")
        if "结构编辑" in system:
            content = json.dumps(_map_doc(json.loads(user)), ensure_ascii=False)
        elif "extension_checklist" in system or "extension_dossier" in system:
            content = json.dumps(_extension_payload(system, user), ensure_ascii=False)
        elif '"blueprint":' in system:
            content = json.dumps(_blueprint_payload(system, user), ensure_ascii=False)
        elif "关系图" in system or "family" in system:
            content = json.dumps(FIGURE, ensure_ascii=False)
        else:
            content = "白话解析：这段原文说的是字面意思。"
        if body.get("stream"):
            cut = max(1, len(content) // 2)
            lines = []
            for index, chunk in enumerate((content[:cut], content[cut:])):
                item = {"choices": [{"delta": {"content": chunk}}]}
                if index == 1:
                    item["choices"][0]["finish_reason"] = "stop"
                    item["usage"] = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
                lines.append("data: " + json.dumps(item, ensure_ascii=False) + "\n\n")
            lines.append("data: [DONE]\n\n")
            raw = "".join(lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        raw = json.dumps(
            {
                "id": "stub",
                "model": body.get("model") or "stub",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18790)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
