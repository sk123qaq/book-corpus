#!/usr/bin/env python3
"""交叉编码器精排封装与常驻 HTTP 服务：Reranker().score(query, texts) 按入参顺序返回打分。

只在独立精排环境（工作区/.venv-rerank）运行，支持 `--selftest` 与 `--serve`（默认 127.0.0.1:18081）。
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import ROOT

DEFAULT_MODEL = os.path.join(ROOT, "工作区", "models", "bge-reranker-v2-m3")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18081

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


class Reranker:
    def __init__(self, model_dir=None, device="cuda", max_len=512, batch=64):
        path = model_dir or os.environ.get("BOOKS_RERANK_MODEL") or DEFAULT_MODEL
        self.model_dir = path
        self.device = torch.device(device)
        self.max_len = max_len
        self.batch = batch
        self.tok = AutoTokenizer.from_pretrained(path)
        dtype = torch.float16 if self.device.type == "cuda" else torch.float32
        self.model = AutoModelForSequenceClassification.from_pretrained(path, dtype=dtype).to(self.device).eval()

    def warmup(self):
        """提前编译 ROCm/CUDA kernel，避免首条线上请求吃冷编译延迟。"""
        self.score("预热问题", ["这是一段用于预热交叉编码器推理图与核函数的中文测试文本。" * 10] * 8)
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)

    def score(self, query, texts):
        if not texts:
            return []
        # 按字数升序成批，短句先跑，减少 padding 浪费
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = [0.0] * len(texts)
        with torch.inference_mode():
            for s in range(0, len(order), self.batch):
                idx = order[s : s + self.batch]
                enc = self.tok(
                    [query] * len(idx),
                    [texts[i] for i in idx],
                    padding=True,
                    truncation=True,
                    max_length=self.max_len,
                    return_tensors="pt",
                ).to(self.device)
                pairs = self.model(**enc).logits.reshape(-1).float().tolist()
                for i, v in zip(idx, pairs, strict=False):
                    out[i] = float(v)
        return out


QUERY = "为什么离开老家以后，亲戚关系就淡了"
TEXTS = [
    "但在高度流动的现代社会，个人总是可以脱离任何一个特定的地方性共同体",
    "今天天气很好，我们去公园散步，看见很多花开了。",
    "资本主义的生产方式导致了商品的普遍化，劳动成为抽象劳动。",
]
THROUGHPUT_PAIRS = 256


def selftest(model_dir, device="cuda"):
    r = Reranker(model_dir=model_dir, device=device)
    for s, t in zip(r.score(QUERY, TEXTS), TEXTS, strict=False):
        print("%.4f\t%s" % (s, t[:24]))
    # 计时前先同步，避开异步 kernel 队列把耗时记到下一轮
    r.score(QUERY, [TEXTS[0]] * THROUGHPUT_PAIRS)  # 首批含 ROCm kernel 编译，不计时
    if r.device.type == "cuda":
        torch.cuda.synchronize(r.device)
    t0 = time.perf_counter()
    r.score(QUERY, [TEXTS[0]] * THROUGHPUT_PAIRS)
    if r.device.type == "cuda":
        torch.cuda.synchronize(r.device)
    ms = (time.perf_counter() - t0) * 1000
    print("%d pairs %.1f ms, %.1f pairs/s" % (THROUGHPUT_PAIRS, ms, THROUGHPUT_PAIRS * 1000 / ms))


def serve(model_dir=None, host=DEFAULT_HOST, port=DEFAULT_PORT, device="cuda", max_len=512, batch=64):
    t0 = time.perf_counter()
    reranker = Reranker(model_dir=model_dir, device=device, max_len=max_len, batch=batch)
    reranker.warmup()
    load_ms = (time.perf_counter() - t0) * 1000
    infer_lock = threading.Lock()
    dev_name = (
        torch.cuda.get_device_name(0)
        if reranker.device.type == "cuda" and torch.cuda.is_available()
        else str(reranker.device)
    )

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/health":
                self._send(
                    200,
                    {
                        "status": "ok",
                        "model": reranker.model_dir,
                        "device": dev_name,
                        "max_len": reranker.max_len,
                        "batch": reranker.batch,
                    },
                )
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/v1/rerank":
                self._send(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send(400, {"error": "bad_content_length"})
                return
            if length <= 0 or length > 8 * 1024 * 1024:
                self._send(400, {"error": "invalid_body_size"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._send(400, {"error": "invalid_json"})
                return
            query = str(payload.get("query") or "")
            texts = payload.get("texts")
            if not query or not isinstance(texts, list):
                self._send(400, {"error": "query_and_texts_required"})
                return
            clean_texts = [str(t or "") for t in texts]
            ts = time.perf_counter()
            with infer_lock:
                scores = reranker.score(query, clean_texts)
                if reranker.device.type == "cuda":
                    torch.cuda.synchronize(reranker.device)
            ms = round((time.perf_counter() - ts) * 1000, 1)
            self._send(200, {"scores": scores, "ms": ms, "count": len(scores)})

        def log_message(self, fmt, *args):
            pass

    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    print(
        f"rerank-server ready http://{host}:{port} device={dev_name} load={load_ms:.0f}ms model={reranker.model_dir}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--model-dir", default=None)
    a = ap.parse_args()
    if a.selftest:
        selftest(a.model_dir, device=a.device)
        return 0
    if a.serve:
        serve(
            model_dir=a.model_dir,
            host=a.host,
            port=a.port,
            device=a.device,
            max_len=a.max_len,
            batch=a.batch,
        )
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
