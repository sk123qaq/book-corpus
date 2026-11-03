#!/usr/bin/env python3
"""追问判定：这一问是不是接着上一轮问。

模型判一次，结果按「问题 + 最近 3 轮 + 提示词版本」缓存。模型不可用或输出
解析不了时走词面兜底，兜底只看二字组重合，不调模型。

`check(question, previous)` 返回
{"related": bool, "reason": str, "against": [int], "ms": float,
 "source": "model"|"cache"|"fallback", "thinking": str}
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

CACHE_DIR = os.path.join(HERE, "缓存", "related")
MODEL = "opencode-go/deepseek-v4.1-flash"
PI_TIMEOUT = 60
PROMPT_VERSION = "related-v1"
WINDOW = 3
REASON_CHARS = 40
STOP = set("的是了和与在什么怎么为什么")
MIN_OVERLAP = 3
PI = ["pi", "-p", "--no-tools", "--no-session", "--no-context-files",
      "--no-skills", "--no-extensions", "--model", MODEL]

PROMPT = """判断「新问题」是不是在接着「上一轮」问，只回一行 JSON。

判据：
- related 为 true：新问题依赖上一轮的问题或答案才问得通；或明确要求就上一轮的人、事、说法、章节继续展开、比较、举例。
- related 为 false：另起主题、换书、同一本书里换一件不相干的事。

{turns}
新问题：{question}

只输出一行 JSON，不要代码块，不要解释：
{{"related":true|false,"reason":"不超过 40 字"}}
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def _ms(t0):
    return round((time.time() - t0) * 1000, 1)


def _clip(text, limit=REASON_CHARS):
    return " ".join(str(text or "").split())[:limit]


def _clean_previous(previous):
    """收成按 n 升序、只留最后 WINDOW 轮的 [{n, question, answer}]。"""
    rows = []
    for item in previous or []:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        rows.append({"n": n,
                     "question": str(item.get("question") or "").strip(),
                     "answer": str(item.get("answer") or "").strip()})
    rows.sort(key=lambda r: r["n"])
    return rows[-WINDOW:]


def _turns_block(previous):
    """上一轮写给模型看全，更早的两轮压成一行，只作参考。"""
    prev = previous[-1]
    blocks = ["上一轮（第 %d 问）：\n问题：%s\n回答：%s"
              % (prev["n"], prev["question"], prev["answer"] or "（未完成）")]
    older = previous[:-1]
    if older:
        blocks.append("更早的轮次（参考，不是判定对象）：\n" + "\n".join(
            "第 %d 问：%s / 答：%s" % (t["n"], t["question"], t["answer"][:300])
            for t in older))
    return "\n\n".join(blocks) + "\n"


def _cache_path(question, previous):
    payload = {"q": question,
               "prev": [[t["n"], t["question"], t["answer"]] for t in previous],
               "v": PROMPT_VERSION}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return os.path.join(CACHE_DIR, hashlib.sha1(raw.encode("utf-8")).hexdigest() + ".json")


def _call(prompt, thinking):
    """调 pi 判一次。返回 (stdout, error)；失败时 stdout 为 None。"""
    try:
        res = subprocess.run(PI + ["--thinking", thinking, prompt],
                             capture_output=True, text=True,
                             timeout=PI_TIMEOUT, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return None, "判定超时（%d 秒）" % PI_TIMEOUT
    except OSError as exc:
        return None, "调不起 pi：%s" % exc.__class__.__name__
    if res.returncode != 0:
        return None, "模型调用失败（rc=%d）" % res.returncode
    if not (res.stdout or "").strip():
        return None, "模型没有输出"
    return res.stdout, ""


def parse_reply(raw):
    """取第一个括号配平的 JSON 对象，只认带布尔 related 的。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    start = text.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except ValueError:
                        obj = None
                    if isinstance(obj, dict) and isinstance(obj.get("related"), bool):
                        return obj
                    break
        start = text.find("{", start + 1)
    return None


def _grams(text):
    """去掉非字词字符与停用字后，取所有二字组。"""
    body = re.sub(r"[\W_]+", "", str(text or ""))
    body = "".join(ch for ch in body if ch not in STOP)
    return {body[i:i + 2] for i in range(len(body) - 1)}


def _fallback(question, previous):
    """词面兜底：与最近几轮的问题+答案比二字组，重合 3 个以上算 related。"""
    mine = _grams(question)
    against, best = [], 0
    for turn in previous:
        hits = len(mine & _grams(turn["question"] + turn["answer"]))
        if hits >= MIN_OVERLAP:
            against.append(turn["n"])
        best = max(best, hits)
    if against:
        reason = "与第 %s 问有 %d 个二字重合" % ("、".join(str(n) for n in against), best)
    else:
        reason = "与最近 %d 轮最多重合 %d 个二字组" % (len(previous), best)
    return {"related": bool(against), "reason": _clip(reason), "against": against}


def check(question, previous, use_cache=True, thinking="low"):
    """previous: [{"n": 1, "question": "…", "answer": "…"}]，answer 传上一轮回答全文（server 不再截断）。

    只比较最后 3 个 previous。第 1 问（没有 previous）直接判 unrelated，不调模型。
    """
    t0 = time.time()
    question = str(question or "").strip()
    thinking = str(thinking or "low").strip().lower() or "low"
    previous = _clean_previous(previous)
    if not previous:
        return {"related": False, "reason": "没有上一轮", "against": [],
                "ms": _ms(t0), "source": "fallback", "thinking": thinking}

    cache_path = _cache_path(question, previous)
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
        except (OSError, ValueError):
            cached = None
        if isinstance(cached, dict) and isinstance(cached.get("related"), bool):
            return {"related": cached["related"],
                    "reason": _clip(cached.get("reason")),
                    "against": [int(n) for n in cached.get("against") or []
                                if isinstance(n, int)],
                    "ms": _ms(t0), "source": "cache", "thinking": thinking}

    prompt = PROMPT.format(turns=_turns_block(previous), question=question)
    raw, _error = _call(prompt, thinking)
    reply = parse_reply(raw) if raw else None
    if reply is not None:
        out = {"related": reply["related"],
               "reason": _clip(reply.get("reason")) or ("相关" if reply["related"] else "无关"),
               "against": [t["n"] for t in previous],
               "ms": _ms(t0), "source": "model", "thinking": thinking}
        try:
            _write_json(cache_path, dict(out, question=question,
                                         prev=[t["n"] for t in previous],
                                         at=_now()))
        except OSError:
            pass
        return out

    out = _fallback(question, previous)
    out.update({"ms": _ms(t0), "source": "fallback", "thinking": thinking})
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--prev", action="append", default=[],
                    help="上一轮，格式 n:问题，可重复")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)
    previous = []
    for item in args.prev:
        n, _, text = item.partition(":")
        previous.append({"n": int(n or 0), "question": text, "answer": ""})
    print(json.dumps(check(args.question, previous, use_cache=not args.no_cache),
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
