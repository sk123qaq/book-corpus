#!/usr/bin/env python3
"""Local OpenAI-compatible stub for driving the reading workbench without a real model.

  python3 .agents/skills/verify-book-corpus/scripts/mock_reading_model.py --port 18790

Point the reading server at it:

  OPENCODE_GO_API_KEY=sk-mock \
  READING_MODEL_URL=http://127.0.0.1:18790/v1/chat/completions \
  python apps/reading/server.py --root "$ROOT" --port 18771 --quiet

It answers three shapes: chapter-map prompts with a document built from the
prompt's blocks, diagram prompts with a fixed two-node flow, and everything
else with one sentence of plain text. It is an evidence stub, not a model
quality benchmark.
"""

import argparse
import json
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


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        system = "\n".join(m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system")
        user = next((m.get("content", "") for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")
        if "结构编辑" in system:
            content = json.dumps(_map_doc(json.loads(user)), ensure_ascii=False)
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
