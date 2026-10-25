#!/usr/bin/env python3
"""交叉编码器精排封装：Reranker().score(query, texts) 按入参顺序返回打分。

实验台用，未接入 book_query；定位与边界见 qa-检索-2026-10-22-书级融合/P3-重排器-实测-2026-10-24.md。"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_MODEL = os.path.join(ROOT, "工作区", "models", "bge-reranker-v2-m3")

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import torch  # noqa: E402
from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: E402


class Reranker:
    def __init__(self, model_dir=None, device="cuda", max_len=512, batch=64):
        path = model_dir or DEFAULT_MODEL
        self.device = torch.device(device)
        self.max_len = max_len
        self.batch = batch
        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = (AutoModelForSequenceClassification.from_pretrained(
            path, dtype=torch.float16).to(self.device).eval())

    def score(self, query, texts):
        if not texts:
            return []
        # 按字数升序成批，短句先跑，减少 padding 浪费
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = [0.0] * len(texts)
        with torch.inference_mode():
            for s in range(0, len(order), self.batch):
                idx = order[s:s + self.batch]
                enc = self.tok([query] * len(idx), [texts[i] for i in idx],
                               padding=True, truncation=True, max_length=self.max_len,
                               return_tensors="pt").to(self.device)
                pairs = self.model(**enc).logits.reshape(-1).float().tolist()
                for i, v in zip(idx, pairs):
                    out[i] = v
        return out


QUERY = "为什么离开老家以后，亲戚关系就淡了"
TEXTS = [
    "但在高度流动的现代社会，个人总是可以脱离任何一个特定的地方性共同体",
    "今天天气很好，我们去公园散步，看见很多花开了。",
    "资本主义的生产方式导致了商品的普遍化，劳动成为抽象劳动。",
]
THROUGHPUT_PAIRS = 256


def selftest(model_dir):
    r = Reranker(model_dir=model_dir)
    for s, t in zip(r.score(QUERY, TEXTS), TEXTS):
        print("%.4f\t%s" % (s, t[:24]))
    # 计时前先同步，避开异步 kernel 队列把耗时记到下一轮
    r.score(QUERY, [TEXTS[0]] * THROUGHPUT_PAIRS)  # 首批含 ROCm kernel 编译，不计时
    torch.cuda.synchronize(r.device)
    t0 = time.perf_counter()
    r.score(QUERY, [TEXTS[0]] * THROUGHPUT_PAIRS)
    torch.cuda.synchronize(r.device)
    ms = (time.perf_counter() - t0) * 1000
    print("%d pairs %.1f ms, %.1f pairs/s" % (THROUGHPUT_PAIRS, ms, THROUGHPUT_PAIRS * 1000 / ms))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--model-dir", default=None)
    a = ap.parse_args()
    if a.selftest:
        selftest(a.model_dir)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
