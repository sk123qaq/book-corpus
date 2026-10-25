#!/usr/bin/env python3
"""按需扩展：只给选中的章做摘要，渐进披露，不整章投喂。

  book_expand.py 礼治                     默认档：3 本 × 1 章，最多 3 轮，9 次调用
  book_expand.py 礼治 --mode deep         深挖：3 本 × 2 章，最多 3 轮，18 次
  book_expand.py 礼治 --mode max          最大：8 本 × 1 章，最多 3 轮，24 次硬上限
  book_expand.py 礼治 --off               关闭扩展，等价 B3 的章列表
  book_expand.py 礼治 --background        先回首轮，扩展在子进程里跑
  book_expand.py 礼治 --json

五层披露（窗口按行号，围绕最佳命中行，不从章首截）：
  L0 元数据 + 最佳命中前后各 200 字左右，不给正文
  L1 最佳命中前后各 20 行，最多 2,000 字；章本身更短时给整章
  L2 前后各 60 行，最多 6,000 字，窗口截断时带上最近的小节标题
  L3 定向读取：模型给行范围或 grep 词，最多 10 个命中、每个前后 5 行、总 4,000 字
  L4 全文，只给 20,000 字以内的章，且只有 --max 允许

摘要必须带 evidence_lines，且落在已经给过的窗口里；queries 必须 3 条，
每条至少两个窗口文本里没出现的 bigram。不合格的回复直接丢弃，不写缓存。

缓存：目录/检索/expand_cache/<sha1>.json，键含章节 mtime 与窗口哈希，
章改动即失效；不同窗口分开缓存。缓存命中零模型调用。
只读 库/ 与 index.db，只写 expand_cache/。
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import book_readme
import book_score
import lib_fts_cjk as cjk
import qsearch

DB = book_score.DB
LIBDB = book_score.LIBDB
CACHE = os.path.join(ROOT, "目录", "检索", "expand_cache")
PROMPT_VERSION = "b4-v1"

MODES = {
    # 操作人 2026-10-23 定 B：接受提供方 8-50 秒/次，保留 24 次硬上限。
    # default 3 本×1 章×3 轮=9；deep 3×2×3=18；max 8×3×1=24（广扫，不再多轮）。
    "default": {"books": 3, "chapters": 1, "rounds": 3, "calls": 9, "workers": 3,
                "budget": 60.0, "allow_l2": True, "allow_l3": True, "allow_l4": False,
                "window_chars": 2000, "timeout": 40.0},
    "deep": {"books": 3, "chapters": 2, "rounds": 3, "calls": 18, "workers": 4,
             "budget": 80.0, "allow_l2": True, "allow_l3": True, "allow_l4": False,
             "window_chars": 6000, "timeout": 40.0},
    "max": {"books": 8, "chapters": 1, "rounds": 3, "calls": 24, "workers": 6,
            "budget": 120.0, "allow_l2": True, "allow_l3": True, "allow_l4": True,
            "window_chars": 6000, "timeout": 40.0},
}
MAX_FULL_CHARS = 20000
CONTEXT_LINES = 5
LEVEL_CHARS = {0: 400, 1: 2000, 2: 6000, 3: 4000, 4: MAX_FULL_CHARS}


@dataclass
class ChapterPacket:
    path: str
    title: str
    chars: int
    lines: int
    best_line: int
    window_start: int
    window_end: int
    window_text: str
    level: int
    has_before: bool
    has_after: bool
    readme_status: str
    neighbor_text: str = ""
    ranges: list = field(default_factory=list)


@dataclass
class ChapterResult:
    path: str
    title: str
    book: str
    level: int
    status: str
    summary: str = ""
    evidence_lines: list = field(default_factory=list)
    queries: list = field(default_factory=list)
    reason: str = ""
    calls: int = 0
    rounds: int = 0
    neighbor_used: bool = False
    cached: bool = False
    window_chars: int = 0
    window_max: int = 0
    window_start: int = 0
    window_end: int = 0
    input_chars: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def full_path(path):
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def read_lines(path):
    with open(full_path(path), encoding="utf-8", errors="ignore") as fh:
        return fh.read().split("\n")


def window_for(lines, best_line, level, window_chars):
    """以最佳命中行为中心取窗口。返回 (start, end, text, has_before, has_after)。"""
    total = len(lines)
    best_line = max(1, min(total, best_line))
    if level == 4:
        text = "\n".join(lines)
        return 1, total, text, False, False
    span = {0: 3, 1: 20, 2: 60}.get(level, 20)
    start, end = max(1, best_line - span), min(total, best_line + span)
    while True:
        chunk = "\n".join(lines[start - 1:end])
        if len(chunk) <= window_chars or (start == 1 and end == total):
            break
        if end - best_line >= best_line - start and end > best_line:
            end -= 1
        elif start < best_line:
            start += 1
        elif end > best_line:
            end -= 1
        else:
            break
    text = "\n".join(lines[start - 1:end])
    if len(text) > window_chars:
        text = text[:window_chars]
    has_before, has_after = start > 1, end < total
    if has_before and level >= 2:
        heading = None
        for i in range(start - 1, max(0, start - 41), -1):
            if lines[i - 1].lstrip().startswith("#"):
                heading = lines[i - 1]
                break
        if heading and heading not in text:
            text = heading + "\n" + text
    return start, end, text, has_before, has_after


def boundary_window(lines, side, max_chars):
    """邻章的 L1 窗口：前一章取尾部，后一章取头部。"""
    total = len(lines)
    if side == "prev":
        end = total
        start = end
        while start > 1 and len("\n".join(lines[start - 2:end])) <= max_chars:
            start -= 1
    else:
        start = 1
        end = start
        while end < total and len("\n".join(lines[start - 1:end + 1])) <= max_chars:
            end += 1
    text = "\n".join(lines[start - 1:end])[:max_chars]
    return start, end, text


def neighbor_block(nbrs, max_chars):
    """把前一章与后一章的 L1 拼成一段。缺哪边就少哪边。"""
    out = []
    for side, label in (("prev", "前一章"), ("next", "后一章")):
        n = (nbrs or {}).get(side)
        if not n:
            continue
        path, title = n
        lines = read_lines(path)
        a, b, text = boundary_window(lines, side, max_chars)
        out.append("【%s《%s》第 %d 到 %d 行】\n%s" % (label, title, a, b, text))
    return "\n\n".join(out)


def make_packet(book, path, title, lines, best_line, level, max_chars, readme_status,
                start=None, end=None):
    """窗口按行。给了 start/end 时保证覆盖这一段（不超过 max_chars）。"""
    total = len(lines)
    if start is None or end is None:
        a, b, text, has_before, has_after = window_for(lines, best_line, level, max_chars)
    else:
        lo, hi = max(1, min(start, end)), min(total, max(start, end))
        mid = (lo + hi) // 2
        span = 60 if level >= 2 else 20
        a, b = max(1, min(lo, mid - span)), min(total, max(hi, mid + span))
        while len("\n".join(lines[a - 1:b])) > max_chars and (a < lo or b > hi):
            if b - hi >= lo - a and b > hi:
                b -= 1
            elif a < lo:
                a += 1
            else:
                b -= 1
        text = "\n".join(lines[a - 1:b])[:max_chars]
        has_before, has_after = a > 1, b < total
    return ChapterPacket(path=path, title=title,
                         chars=sum(len(x) + 1 for x in lines), lines=total,
                         best_line=max(1, min(total, best_line)), window_start=a,
                         window_end=b, window_text=text, level=level,
                         has_before=has_before, has_after=has_after,
                         readme_status=readme_status)


def clamp_context(value):
    return max(1, min(CONTEXT_LINES, int(value or CONTEXT_LINES)))


def merge_ranges(nums):
    out = []
    for n in sorted(nums):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def targeted(lines, request, max_chars):
    """L3：按行范围或 grep 词定向读取。返回 (ranges, text)，ranges 是真正给出的行区间。"""
    kind = (request or {}).get("type")
    total = len(lines)
    if kind == "lines":
        try:
            a, b = int(request.get("start")), int(request.get("end"))
        except (TypeError, ValueError):
            return None
        if not (1 <= a <= b <= total):
            return None
        text = "\n".join(lines[a - 1:b])[:max_chars]
        return [(a, b)], text
    if kind == "grep":
        terms = [str(t).strip() for t in (request.get("terms") or []) if str(t).strip()]
        if not terms:
            return None
        context = clamp_context(request.get("context"))
        used, out, size, hits = [], [], 0, 0
        for i, line in enumerate(lines, 1):
            if hits >= 10 or size >= max_chars:
                break
            if not any(t in line for t in terms):
                continue
            hits += 1
            a, b = max(1, i - context), min(total, i + context)
            for j in range(a, b + 1):
                if j in used:
                    continue
                if size + len(lines[j - 1]) + 1 > max_chars:
                    break
                used.append(j)
                out.append(lines[j - 1])
                size += len(lines[j - 1]) + 1
        if not used:
            return None
        return merge_ranges(used), "\n".join(out)
    return None


def prompt_for(question, book, packet, extra=""):
    head = ("你是图书检索助手。用户的问题：%s\n"
            "书：%s\n本章：%s\n路径：%s\n总字数 %d，总行数 %d，最佳命中行 %d。\n"
            % (question, book, packet.title, packet.path, packet.chars, packet.lines,
               packet.best_line))
    if packet.window_text:
        head += ("下面是第 %d 到 %d 行（层级 L%d），只依据这些内容，不要补充没出现的细节。\n"
                 % (packet.window_start, packet.window_end, packet.level))
    body = ("---\n%s\n---\n" % packet.window_text) if packet.window_text else ""
    if packet.neighbor_text:
        body += ("\n【本章的邻章片段，只作背景】\n" + packet.neighbor_text + "\n"
                 "引用只能来自本章窗口的行号，邻章片段不能当引用。\n")
    rules = ('只输出一个 JSON 对象，不加代码围栏。够回答时：\n'
             '{"need_more": false, "summary": "本章与问题相关的 30 到 120 字概括", '
             '"evidence_lines": [88, 92], "queries": ["检索式1", "检索式2", "检索式3"]}\n'
             '不够时输出：\n'
             '{"need_more": true, "reason": "缺什么", "request": {"type": "lines", "start": 60, "end": 140}}\n'
             '或 {"need_more": true, "reason": "...", "request": {"type": "grep", "terms": ["支配", "强制"], "context": 5}}\n'
             '或 {"need_more": true, "reason": "...", "request": {"type": "full"}}\n'
             "约束：evidence_lines 必须是上面给出的行号；queries 恰好 3 条，每条至少两个原文没出现的词。\n"
             "检索式正例（原文没有「次贷」「衍生品」）：\"房贷风险是怎么转手的\"、\"评级机构为什么不可信\"。\n"
             "不要照抄本章的专名和术语。")
    return head + extra + body + rules


def parse_json(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except ValueError:
            return None


def out_of_text_bigrams(query, window_text):
    q = re.sub(r"[^\u4e00-\u9fff]+", " ", query)
    grams = set()
    for seg in q.split():
        for i in range(len(seg) - 1):
            grams.add(seg[i:i + 2])
    return [g for g in grams if g not in window_text]


def validate_reply(obj, given_ranges, window_text, level):
    """返回 (ok, reply_or_reason)。given_ranges 是 [(start, end), ...]。"""
    if not isinstance(obj, dict):
        return False, "不是 JSON 对象"
    if obj.get("need_more"):
        req = obj.get("request") or {}
        kind = req.get("type")
        if kind == "full":
            return True, {"need_more": True, "request": req, "reason": obj.get("reason", "")}
        if kind == "lines":
            try:
                a, b = int(req.get("start")), int(req.get("end"))
            except (TypeError, ValueError):
                return False, "行范围不是整数"
            if a > b or a < 1:
                return False, "行范围越界"
            return True, {"need_more": True, "request": {"type": "lines", "start": a, "end": b},
                          "reason": obj.get("reason", "")}
        if kind == "grep":
            terms = [str(t).strip() for t in (req.get("terms") or []) if str(t).strip()]
            if not terms:
                return False, "grep 词为空"
            return True, {"need_more": True,
                          "request": {"type": "grep", "terms": terms,
                                      "context": clamp_context(req.get("context"))},
                          "reason": obj.get("reason", "")}
        return False, "未知的请求类型"
    summary = str(obj.get("summary") or "").strip()
    if not summary:
        return False, "摘要是空的"
    if len(summary) < 20:
        return False, "摘要过短"
    if len(summary) > 300:
        return False, "摘要过长"
    evidence = obj.get("evidence_lines") or []
    try:
        evidence = [int(x) for x in evidence]
    except (TypeError, ValueError):
        return False, "evidence_lines 不是整数"
    if not evidence:
        return False, "evidence_lines 为空"
    for line in evidence:
        if not any(a <= line <= b for a, b in given_ranges):
            return False, "evidence_lines 越界：第 %d 行不在已给窗口里" % line
    queries = [str(q).strip() for q in (obj.get("queries") or []) if str(q).strip()]
    if len(queries) < 3:
        return False, "检索式少于三条"
    for q in queries[:3]:
        if len(out_of_text_bigrams(q, window_text)) < 2:
            return False, "检索式正文外词不足：%s" % q
    return True, {"need_more": False, "summary": summary, "evidence_lines": evidence,
                  "queries": queries[:3]}


def cache_path(path, level, window_text, prompt, mtime):
    key = sha1("|".join([path, "expand", str(level), sha1(window_text),
                         sha1(prompt), "%.0f" % mtime, PROMPT_VERSION]))
    return os.path.join(CACHE, key + ".json")


def cache_get(path, level, window_text, prompt, mtime):
    cp = cache_path(path, level, window_text, prompt, mtime)
    if not os.path.exists(cp):
        return None
    try:
        with open(cp, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def cache_put(path, level, window_text, prompt, mtime, payload):
    os.makedirs(CACHE, exist_ok=True)
    cp = cache_path(path, level, window_text, prompt, mtime)
    tmp = cp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, cp)
    return cp


EFFORT = os.environ.get("BOOK_EXPAND_EFFORT", "low")
NEIGHBORS = os.environ.get("BOOK_EXPAND_NEIGHBORS", "") == "1"
MAX_OUT = int(os.environ.get("BOOK_EXPAND_MAX_OUT", "6000"))


def default_call(prompt, timeout):
    """opencode-go 的 deepseek-v4.1-flash。effort 与 max_tokens 按 B4 实测定。"""
    import urllib.request
    import build_expand
    body = {"model": build_expand.OPENCODE_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_OUT, "stream": False, "reasoning_effort": EFFORT}
    req = urllib.request.Request(
        build_expand.OPENCODE_URL, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + json.load(open(build_expand.AUTH))["opencode-go"]["key"],
                 "Content-Type": "application/json",
                 "User-Agent": build_expand.OPENCODE_UA,
                 "x-opencode-session": build_expand.OPENCODE_SESSION})
    import time
    import urllib.error
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            break
        except (urllib.error.URLError, ConnectionError) as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError) or attempt:
                raise
            time.sleep(3)
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    usage = d.get("usage") or {}
    return msg.get("content") or "", {"input_tokens": usage.get("prompt_tokens", 0),
                                      "output_tokens": usage.get("completion_tokens", 0)}


def run_chapter(question, book, path, title, best_line, mode, call, deadline,
                readme_status="ok", use_cache=True, counter=None, nbrs=None,
                use_neighbors=False):
    cfg = MODES[mode]
    lines = read_lines(path)
    total_chars = sum(len(x) + 1 for x in lines)
    mtime = os.stat(full_path(path)).st_mtime
    res = ChapterResult(path=path, title=title, book=book, level=1, status="ok")
    first = int(os.environ.get("BOOK_EXPAND_FIRST_LEVEL", cfg.get("first", 1)))
    given, packet = [], make_packet(book, path, title, lines, best_line, first,
                                    cfg["window_chars"] if first == 1 else LEVEL_CHARS[first],
                                    readme_status)
    for round_no in range(1, cfg["rounds"] + 1):
        if time.monotonic() > deadline:
            res.status, res.reason = "partial", "预算用尽"
            return res
        if counter is not None and counter["cap"] is not None \
                and counter["used"] >= counter["cap"]:
            res.status, res.reason = "partial", "调用次数用尽"
            return res
        prompt = prompt_for(question, book, packet,
                            "上一轮你请求了更多内容，下面是补上的部分。\n" if round_no > 1 else "")
        if packet.window_text:
            given.extend(packet.ranges or [(packet.window_start, packet.window_end)])
        hit = cache_get(path, packet.level, packet.window_text, prompt, mtime) if use_cache else None
        if hit:
            res.cached = True
            reply = hit.get("reply") or {}
            obj = {"need_more": False, "summary": reply.get("summary", ""),
                   "evidence_lines": reply.get("evidence_lines", []),
                   "queries": reply.get("queries", [])}
            ok, parsed = validate_reply(obj, given, packet.window_text, packet.level)
            if not ok:
                res.status, res.reason = "partial", "缓存内容不合格：%s" % parsed
                return res
        else:
            if counter is not None:
                with counter["lock"]:
                    if counter["cap"] is not None and counter["used"] >= counter["cap"]:
                        res.status, res.reason = "partial", "调用次数用尽"
                        return res
                    counter["used"] += 1
            try:
                txt, usage = call(prompt, cfg["timeout"])
            except Exception as exc:
                res.status, res.reason = "partial", "调用失败：%s" % type(exc).__name__
                return res
            res.calls += 1
            res.tokens_in += int((usage or {}).get("input_tokens") or 0)
            res.tokens_out += int((usage or {}).get("output_tokens") or 0)
            res.input_chars += len(prompt)
            obj = parse_json(txt)
            ok, parsed = validate_reply(obj, given, packet.window_text, packet.level)
            if not ok:
                res.status, res.reason = "partial", "回复不合格：%s" % parsed
                return res
        res.rounds = round_no
        res.level = packet.level
        res.window_start, res.window_end = packet.window_start, packet.window_end
        res.window_chars += len(packet.window_text)
        res.window_max = max(res.window_max, len(packet.window_text))
        if not parsed.get("need_more"):
            res.summary = parsed["summary"]
            res.evidence_lines = parsed["evidence_lines"]
            res.queries = parsed["queries"]
            if not res.cached:
                cache_put(path, packet.level, packet.window_text, prompt, mtime,
                          {"reply": {"summary": res.summary,
                                     "evidence_lines": res.evidence_lines,
                                     "queries": res.queries},
                           "level": packet.level, "prompt_version": PROMPT_VERSION})
            res.status = "cached" if res.cached else "ok"
            return res
        req = parsed["request"]
        if round_no >= cfg["rounds"]:
            res.status, res.reason = "partial", "轮数用完：" + str(parsed.get("reason") or "")
            return res
        kind = req.get("type")
        if use_neighbors and nbrs and round_no == 1:
            text = neighbor_block(nbrs, LEVEL_CHARS[1])
            if text:
                packet = ChapterPacket(path, title, total_chars, len(lines), best_line,
                                       0, 0, "", 1, False, False, readme_status, text)
                res.neighbor_used = True
                continue
        if kind == "lines":
            a, b = req["start"], req["end"]
            if not (1 <= a <= b <= len(lines)):
                res.status, res.reason = "partial", "行范围越界"
                return res
            if not cfg["allow_l3"]:
                res.status, res.reason = "partial", "默认档不允许定向读取"
                return res
            mid = (a + b) // 2
            if cfg["allow_l2"] and b - a + 1 <= 120:
                packet = make_packet(book, path, title, lines, mid, 2,
                                     cfg["window_chars"], readme_status, start=a, end=b)
            else:
                text = "\n".join(lines[a - 1:b])[:LEVEL_CHARS[3]]
                packet = ChapterPacket(path, title, total_chars, len(lines), best_line,
                                       a, b, text, 3, a > 1, b < len(lines), readme_status)
        elif kind == "grep":
            if not cfg["allow_l3"]:
                res.status, res.reason = "partial", "默认档不允许定向读取"
                return res
            got = targeted(lines, req, LEVEL_CHARS[3])
            if not got:
                res.status, res.reason = "partial", "grep 没有命中"
                return res
            ranges, text = got
            a, b = ranges[0][0], ranges[-1][1]
            packet = ChapterPacket(path, title, total_chars, len(lines), best_line, a, b,
                                   text, 3, a > 1, b < len(lines), readme_status,
                                   ranges=ranges)
        elif kind == "full":
            if not cfg["allow_l4"]:
                res.status, res.reason = "partial", "本档不允许全文"
                return res
            if total_chars > MAX_FULL_CHARS:
                res.status, res.reason = "partial", "章超过 20000 字，拒绝全文"
                return res
            packet = make_packet(book, path, title, lines, best_line, 4,
                                 LEVEL_CHARS[4], readme_status)
        else:
            res.status, res.reason = "partial", "未知请求"
            return res
    res.status, res.reason = "partial", "轮数用完"
    return res


def collect_units(question, con, meta, expand, mode, limit=None):
    """回答 B3 的章列表并选出要扩展的单元。"""
    terms = [t for t in re.split(r"[\s,]+", question) if t]
    expr = " OR ".join("(%s)" % cjk.query_expr(t) for t in terms if cjk.query_expr(t))
    hits, _ = qsearch.search(con, expr, terms, [], 2000, 40, False, 2000)
    ranked = book_score.rank(hits, terms, "best_support", [], meta, expand)
    cfg = {"books": 10, "chapters": 3} if mode == "off" else MODES[mode]
    units, books = [], []
    for b in ranked[:cfg["books"]]:
        toc, status = book_readme.readme_toc(b.book)
        chapters = book_readme.select([r for r in hits if r["book"] == b.book], terms, terms, toc)
        order = {e["path"]: i for i, e in enumerate(toc)}
        for c in chapters[:cfg["chapters"]]:
            i = order.get(c["chapter_path"])
            nbrs = {}
            if i is not None:
                if i > 0:
                    nbrs["prev"] = (toc[i - 1]["path"], toc[i - 1]["title"])
                if i + 1 < len(toc):
                    nbrs["next"] = (toc[i + 1]["path"], toc[i + 1]["title"])
            units.append((b.book, c["chapter_path"], c["chapter_title"], c["line"], status, nbrs))
        books.append({"book": b.book, "chapters": len(toc), "readme_status": status,
                      "has_prefix": b.has_prefix, "has_queries": b.has_queries,
                      "selected": [{"chapter_path": c["chapter_path"],
                                    "chapter_title": c["chapter_title"],
                                    "line": c["line"], "score": c["score"]} for c in chapters]})
    return {"terms": terms, "hits": len(hits), "books": books, "units": units}


def run(question, mode="default", call=None, con=None, meta=None, expand=None,
        use_cache=True, limit=None):
    con = con or sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    meta = meta if meta is not None else book_score.load_meta(LIBDB)
    expand = expand if expand is not None else book_score.load_expand(con)
    t0 = time.monotonic()
    base = collect_units(question, con, meta, expand, mode)
    if mode == "off":
        return {"question": question, "mode": mode,
                "ms": round((time.monotonic() - t0) * 1000, 1), "calls": 0,
                "rounds": 0, "cached": 0, "books": base["books"], "chapters": [],
                "tokens_in": 0, "tokens_out": 0, "input_chars": 0, "partial": 0}
    cfg = dict(MODES[mode])
    cfg["neighbors"] = NEIGHBORS
    deadline = t0 + cfg["budget"]
    call = call or default_call
    counter = {"used": 0, "cap": cfg["calls"], "lock": threading.Lock()}
    results = []
    with ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
        futures = [pool.submit(run_chapter, question, book, path, title, line, mode, call,
                               deadline, status, use_cache, counter, nbrs, NEIGHBORS)
                   for book, path, title, line, status, nbrs in base["units"]]
        for f in futures:
            results.append(f.result())
    results.sort(key=lambda r: (r.book, r.path))
    return {
        "question": question, "mode": mode,
        "ms": round((time.monotonic() - t0) * 1000, 1),
        "calls": sum(r.calls for r in results),
        "rounds": sum(r.rounds for r in results),
        "cached": sum(1 for r in results if r.cached),
        "partial": sum(1 for r in results if r.status == "partial"),
        "books": base["books"],
        "chapters": [asdict(r) for r in results],
        "tokens_in": sum(r.tokens_in for r in results),
        "tokens_out": sum(r.tokens_out for r in results),
        "input_chars": sum(r.input_chars for r in results),
        "budget_s": cfg["budget"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--mode", default="default", choices=["default", "deep", "max", "off"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--background", action="store_true",
                    help="先打首轮结果，扩展交子进程，完成后写 --out")
    ap.add_argument("--no-wait", action="store_true", help="等同 --background")
    ap.add_argument("--out", default="")
    ap.add_argument("--explain", action="store_true")
    a = ap.parse_args(argv)
    if a.background or a.no_wait:
        out = a.out or os.path.join("/tmp", "book_expand_%d.json" % int(time.time()))
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        meta = book_score.load_meta(LIBDB)
        expand = book_score.load_expand(con)
        first = collect_units(a.query, con, meta, expand, a.mode)
        print(json.dumps({"first_round": first, "refined_out": out}, ensure_ascii=False,
                         indent=1))
        subprocess.Popen([sys.executable, os.path.abspath(__file__), a.query,
                          "--mode", a.mode, "--json", "--out", out],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return 0
    res = run(a.query, a.mode)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print("%s  %s  %.0f ms  调用 %d  轮 %d  缓存 %d  partial %d  tokens %d/%d"
          % (a.query, a.mode, res["ms"], res["calls"], res["rounds"], res["cached"],
             res["partial"], res["tokens_in"], res["tokens_out"]), file=sys.stderr)
    for c in res["chapters"]:
        print("%-42s L%d %-7s 行 %d-%d 字 %d  %s" % (
            c["path"].split("/md/")[-1], c["level"], c["status"], c["window_start"],
            c["window_end"], c["window_chars"], c["title"]))
        if c["summary"]:
            print("   摘要：" + c["summary"])
            print("   证据行：%s" % ",".join(map(str, c["evidence_lines"])))
            if a.explain:
                print("   检索式：" + " | ".join(c["queries"]))
        if c["reason"]:
            print("   原因：" + c["reason"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
