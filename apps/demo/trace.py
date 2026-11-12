#!/usr/bin/env python3
"""组装一条检索 trace：切词 → 词法循环 → 打分选章 → 书卡 → 原文。

只读 build/检索/index.db、build/library.db 与 books/**.md。
切词用一次 pi 调用并缓存到 缓存/terms/<问题+思考等级 sha1>.json；失败退回概念表包含匹配。
fresh=True 跳过切词缓存，前端「重新检索」走这条路。
不跑 B4：book_cards.run 的 expand 固定为 {}。
"""

import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata


def _package_scripts():
    try:
        from bookcorpus.paths import scripts_dir

        return scripts_dir()
    except ImportError:
        return os.path.join(os.path.dirname(os.path.dirname(HERE)), "src", "bookcorpus", "scripts")


HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = _package_scripts()
for _p in (SCRIPTS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import book_cards
import book_query
import book_score
import book_title
import lib_common as L
from thinking import DEFAULT as DEFAULT_THINKING
from thinking import normalize as normalize_thinking

ROOT = L.ROOT

INDEX_DB = os.path.join(ROOT, "build", "检索", "index.db")
LIB_DB = os.path.join(ROOT, "build", "library.db")
LIBRARY = os.path.join(ROOT, "books")
CACHE_DIR = os.path.join(HERE, "缓存", "terms")
CONCEPTS = os.path.join(ROOT, "tables", "概念表.tsv")
ACTIONS = os.path.join(ROOT, "tables", "概念表-动作词.tsv")

try:  # 项目配的模型与思考等级；没装包时退回写死的那一套
    from bookcorpus import pimodel

    _CFG = pimodel.load(ROOT)
    MODEL = pimodel.ref(_CFG["values"]) or "opencode-go/deepseek-v4.1-flash"
    PI = [
        *pimodel.pi_head(),
        "-p",
        "--no-tools",
        "--no-session",
        "--no-context-files",
        "--no-skills",
        "--no-extensions",
    ]
    DEFAULT_THINKING = normalize_thinking(pimodel.thinking(_CFG["values"]), DEFAULT_THINKING)

    def pi_flags(level):
        return pimodel.model_flags(_CFG["values"], level)
except ImportError:
    pimodel, _CFG = None, None
    MODEL = "opencode-go/deepseek-v4.1-flash"
    PI = ["pi", "-p", "--no-tools", "--no-session", "--no-context-files", "--no-skills", "--no-extensions"]

    def pi_flags(level):
        return ["--thinking", level, "--model", MODEL]


PI_TIMEOUT = 90
MAX_TERMS = 6
TERM_VERSION = "terms-v4"
STAGE_CHARS = 900
STAGE_BEFORE = 700

_META_CACHE = {"at": 0.0, "value": None}


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def read_table(path, kind="concept"):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            key = (row.get("key") or "").strip()
            if not key:
                continue
            aliases = [a.strip() for a in (row.get("aliases") or "").split(",") if a.strip()]
            out[key] = aliases
    return out


def load_concepts():
    return read_table(CONCEPTS)


def load_actions():
    out = []
    if not os.path.exists(ACTIONS):
        return out
    with open(ACTIONS, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            term = (row.get("term") or "").strip()
            if term:
                out.append(term)
    return out


# ---- 切词 -----------------------------------------------------------


def _prompt(question):
    concepts = load_concepts()
    actions = load_actions()
    table = "\n".join("%s：%s" % (k, ",".join(v)) for k, v in concepts.items())
    return (
        "你是书库全文检索的切词器。把问题拆成 2 到 6 个用于检索的中文词，并给出 3 到 5 组同义说法。\n"
        "规则：\n"
        '- 只回 JSON，形如 {"terms":["词1","词2"],"groups":[{"name":"组名","variants":["说法1","说法2"]}]}，不要解释、不要代码块。\n'
        "- terms 必须是单个词，不能带空格；优先名词与概念，用书里可能原样出现的词；疑问词、介词、连词不要。\n"
        "- groups 用于第二遍组覆盖检索：覆盖人物、事件、动作、身份；每组 2 到 6 个同义、近义、书里可能用的说法；不要只重复 terms。\n"
        "- 这些是动作词，不要放进 terms：%s\n"
        "- 以下概念键与别名可优先用（key：aliases）：\n%s\n"
        "问题：%s" % ("、".join(actions), table, question)
    )


def _clean_term(text):
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"^[\s\"'“”‘’《》〈〉「」『』\[\]{}()（）]+|[\s\"'“”‘’《》〈〉「」『』\[\]{}()（）]+$", "", t)
    t = re.sub(r"\s+", "", t)
    return t[:24]


def _clean_variant(text):
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"^[\s\"'“”‘’《》〈〉「」『』\[\]{}()（）]+|[\s\"'“”‘’《》〈〉「」『』\[\]{}()（）]+$", "", t)
    t = re.sub(r"\s+", "", t)
    return t[:32]


def _parse_extract(stdout):
    raw = stdout or ""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return [], []
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return [], []
    if not isinstance(data, dict):
        return [], []
    terms = []
    for item in data.get("terms") or []:
        if not isinstance(item, str):
            continue
        for part in re.split(r"[\s,，、;；]+", item):
            part = _clean_term(part)
            if part and part not in terms:
                terms.append(part)
    groups = []
    for g in data.get("groups") or []:
        if not isinstance(g, dict):
            continue
        variants = []
        for v in g.get("variants") or []:
            if isinstance(v, str):
                v = _clean_variant(v)
                if v and v not in variants:
                    variants.append(v)
        if variants:
            name = _clean_term(g.get("name") or "") or variants[0]
            groups.append({"name": name, "variants": variants[:8]})
    return terms[:MAX_TERMS], groups[:5]


FALLBACK_HEAD = re.compile(
    r"^(?:请问|请|帮我|帮忙|麻烦|我想知道|想知道|介绍一下|介绍下|详细介绍下|详细介绍|说说|讲讲|告诉我|解释一下|解释下|分析一下|分析|查一下|查查|看看|看下|列一下|列出|能否|可以)+"
)
FALLBACK_TAIL = re.compile(r"(?:吗|呢|吧|啊|呀|的)+$")
FALLBACK_SPLIT = re.compile(
    r"(?:是怎么|怎么样|怎么|如何|为什么|为何|哪些|哪几种|哪几个|哪一个|哪个|谁|什么|多少|多久|多大|是否|有没有)"
)


def _strip_question(question):
    """去掉问句的礼貌语、疑问词与句末语气词，留下可检索的实词。"""
    text = unicodedata.normalize("NFKC", question or "")
    text = re.sub(r"\s+", "", text)
    text = text.strip("？?！!。.，,、；;：:")
    text = FALLBACK_HEAD.sub("", text)
    text = FALLBACK_TAIL.sub("", text)
    return text.strip("，,、；;：:？?！!。.")


def _fallback_prompt(question):
    """第一次切词退化时再问一次，只要名词短语，不要整句。"""
    return (
        "把问题拆成 3 到 5 个用于书库全文检索的中文名词短语，每个短语 2 到 8 个字。\n"
        "不要疑问词，不要整句，不要解释。只回一行 JSON：\n"
        '{"terms":["短语1","短语2"],"groups":[{"name":"组名","variants":["说法1","说法2"]}]}\n'
        "问题：%s" % question
    )


def _fallback_terms(question):
    """概念表包含匹配；再不行去掉疑问脚手架，按问句切出 2 到 6 段。"""
    concepts = load_concepts()
    found = []
    for key, aliases in concepts.items():
        for cand in [key, *aliases]:
            if len(cand) >= 2 and cand in question and cand not in found:
                found.append(cand)
                break
    found = sorted(found, key=lambda x: (-len(x), x))[:MAX_TERMS]
    if found:
        return found
    text = _strip_question(question)
    if not text:
        return []
    chunks = [part for part in FALLBACK_SPLIT.split(text) if part]
    if len(chunks) == 1 and len(text) > 12:
        chunks = [part for part in re.split(r"[被把对向给从]", text) if part]
    out = []
    for part in chunks:
        part = part.strip("的了是在有和与及讲说写做")
        for piece in re.split(r"[被把]", part) if len(part) > 14 else [part]:
            piece = piece.strip("的了是在有和与及讲说写做")
            piece = piece[:24]
            if len(piece) >= 2 and piece not in out:
                out.append(piece)
    if not out:
        out = [text[:24]]
    return out[:MAX_TERMS]


def _cache_path(question, thinking):
    raw = "\n".join((TERM_VERSION, thinking, question.strip()))
    key = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()
    return os.path.join(CACHE_DIR, key + ".json")


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def extract_terms(question, thinking=DEFAULT_THINKING, use_cache=True):
    """返回 {terms, groups, source, ms, note}。source: model|cache|degraded。

    thinking 进缓存键：换档就是另一份切词。use_cache=False 时跳过读缓存，
    但本次模型结果仍写回，下一次同档提问直接命中。
    """
    thinking = normalize_thinking(thinking)
    path = _cache_path(question, thinking)
    if use_cache and os.path.exists(path):
        t0 = time.time()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            terms = [t for t in data.get("terms", []) if isinstance(t, str) and t]
            groups = data.get("groups") or []
            if terms and groups:
                return {
                    "terms": terms[:MAX_TERMS],
                    "groups": groups,
                    "source": "cache",
                    "thinking": thinking,
                    "ms": round((time.time() - t0) * 1000, 1),
                    "model": MODEL,
                }
        except (OSError, ValueError):
            pass

    t0 = time.time()
    note = ""
    terms, groups = [], []
    try:
        res = subprocess.run(  # noqa: S603 argv 固定为解释器+本仓脚本/常量，不含外部输入
            PI + pi_flags(thinking) + [_prompt(question)], capture_output=True, text=True, timeout=PI_TIMEOUT, cwd=ROOT
        )
        terms, groups = _parse_extract(res.stdout) if res.returncode == 0 else ([], [])
        if len(terms) < 2 or not groups:
            retry = subprocess.run(  # noqa: S603 同上
                PI + pi_flags(thinking) + [_fallback_prompt(question)],
                capture_output=True,
                text=True,
                timeout=PI_TIMEOUT,
                cwd=ROOT,
            )
            more_terms, more_groups = _parse_extract(retry.stdout) if retry.returncode == 0 else ([], [])
            if len(more_terms) >= 2 and more_groups:
                terms, groups = more_terms, more_groups
                note = "第一次切词不可用，已重试"
        # 读侧认 terms 且 groups；一条词也缓存，退化结果下次不必再等模型
        if terms and groups:
            ms = round((time.time() - t0) * 1000, 1)
            _write_json(
                path,
                {
                    "question": question,
                    "terms": terms,
                    "groups": groups,
                    "source": "model",
                    "model": MODEL,
                    "thinking": thinking,
                    "ms": ms,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
            )
            return {
                "terms": terms,
                "groups": groups,
                "source": "model",
                "thinking": thinking,
                "ms": ms,
                "model": MODEL,
                "note": note,
            }
        note = note or ("模型没给出可用的词组" if res.returncode == 0 else "模型返回不可用（rc=%d）" % res.returncode)
    except (OSError, subprocess.SubprocessError) as exc:
        note = "切词调用失败：%s" % exc.__class__.__name__
    ms = round((time.time() - t0) * 1000, 1)
    terms = _fallback_terms(question)
    groups = [{"name": t, "variants": [t]} for t in terms]
    if terms and groups:
        _write_json(
            path,
            {
                "question": question,
                "terms": terms,
                "groups": groups,
                "source": "degraded",
                "model": MODEL,
                "thinking": thinking,
                "ms": ms,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
        )
    return {
        "terms": terms,
        "groups": groups,
        "source": "degraded",
        "thinking": thinking,
        "ms": ms,
        "model": MODEL,
        "note": note,
    }


# ---- 原文 -----------------------------------------------------------


def safe_md_path(path):
    """把请求里的路径解析成 books/ 下的真实 .md 路径；不合法抛 ValueError。"""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 为空")
    raw = path.strip()
    if not raw.endswith(".md"):
        raise ValueError("只读 .md")
    full = raw if os.path.isabs(raw) else os.path.join(ROOT, raw)
    real = os.path.realpath(full)
    lib = os.path.realpath(LIBRARY)
    try:
        inside = os.path.commonpath([lib, real]) == lib
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(real):
        raise ValueError("路径不在 books/ 下或文件不存在")
    return real


def chapter_title(text, path):
    head = text[:2000]
    m = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
    if m:
        t = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", m.group(1), re.M)
        if t:
            return t.group(1).strip()
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    if m:
        return m.group(1).strip()
    name = os.path.basename(path)[:-3]
    return re.sub(r"^\d+[_、.\- ]*", "", name) or name


def _clean_line(line):
    s = line.rstrip()
    if s.strip() in ("---", "***", "___", "..."):
        return ""
    if re.match(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$", s):
        return ""
    s = re.sub(r"\[\[(\d+)\]\]\([^)]*\)", "", s)
    if re.match(r"^#\s", s):
        return ""
    s = re.sub(r"^#{2,6}\s*", "", s)
    if re.match(r"^\s*(<[^>]+>)\s*$", s):
        return ""
    return s


def read_chapter(path, line=1, chars=STAGE_CHARS, before=STAGE_BEFORE):
    """从 1-based line 起取前置 before 字 + 命中整行 + 后置约 chars 字的正文。

    命中行原样保留（不清理），所以 text[hit_start:hit_end] 一定等于 hit_text。
    前置按整行从命中行往上收，收到「再加一行就超过 before」为止；YAML 前置块跳过。
    返回 dict（不含 ok 字段）。
    """
    real = safe_md_path(path)
    t0 = time.time()
    with open(real, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    lines = text.splitlines()
    total_lines = len(lines)
    title = chapter_title(text, real)
    try:
        start = max(1, int(line))
    except (TypeError, ValueError):
        start = 1
    start = min(start, max(1, total_lines))
    content_start = 1
    if lines and lines[0].strip() == "---":
        close = 0
        for i in range(1, min(len(lines), 80)):
            if lines[i].strip() == "---":
                close = i
                break
        if close:
            content_start = close + 2
            if start < content_start:
                start = content_start
    # 命中行是整行原文；落在空行上时往下找最近的非空行，避免高亮块空着
    for _ in range(4):
        if start > total_lines or (lines[start - 1].strip() if lines else ""):
            break
        start += 1
    start = min(start, max(1, total_lines))
    hit_text = lines[start - 1] if lines else ""

    budget = max(0, int(before or 0))
    pre, used = [], 0
    idx = start - 1
    while idx >= content_start:
        s = _clean_line(lines[idx - 1])
        if not s.strip():
            idx -= 1
            continue
        if used + len(s) + 1 > budget:
            if pre:
                break
            # 单行就超预算时，只留离命中行最近的尾巴
            s = s[-budget:] if budget else ""
            if not s:
                break
        pre.append(s)
        used += len(s) + 1
        idx -= 1
    pre.reverse()
    pre_text = "\n".join(pre)

    def u16(s):
        """JS 的 length/slice 按 UTF-16 码元数，增补平面字符算 2。"""
        return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)

    hit_start = u16(pre_text) + (1 if pre_text else 0)
    hit_end = hit_start + u16(hit_text)

    blocks, used_after = [], 0
    for raw in lines[start:]:
        s = _clean_line(raw)
        if not s.strip():
            continue
        if used_after + len(s) > chars:
            if not blocks:
                # 首个非空行就超预算：与前置方向同口径，只留头部，不整行吞下
                blocks.append(s[:chars])
                used_after += chars
            break
        blocks.append(s)
        used_after += len(s)
        if used_after >= chars:
            break
    if not blocks and not pre_text:
        tail = [ln for ln in lines if ln.strip()][-12:]
        blocks = [_clean_line(ln) for ln in tail if _clean_line(ln)]
        blocks = blocks[-1:] if blocks else []
    after_text = "\n".join(b for b in blocks if b)

    parts = [hit_text]
    if pre_text:
        parts.insert(0, pre_text)
    if after_text:
        parts.append(after_text)
    body = "\n".join(parts)
    book_root = real.split(os.sep + "md" + os.sep)[0]
    has_html = os.path.isfile(os.path.join(book_root, "html", "index.html"))
    return {
        "path": path,
        "title": title,
        "text": body,
        "html": has_html,
        "line": start,
        "hit_line": start,
        "hit_start": hit_start,
        "hit_end": hit_end,
        "hit_text": hit_text,
        "before": before,
        "before_chars": len(pre_text),
        "total_lines": total_lines,
        "window_chars": len(body),
        "ms": round((time.time() - t0) * 1000, 1),
    }


# ---- trace ----------------------------------------------------------


def _norm(text):
    return re.sub(r"\s+", "", text or "")


def resolve_line(path, snippet):
    """卡片段落 → md 行号。para 表没有 path 索引，直接读章节文件按行窗口匹配。"""
    probe = _norm(snippet)[:16]
    if not probe:
        return 1
    try:
        real = safe_md_path(path)
        with open(real, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except (OSError, ValueError):
        return 1
    for i in range(len(lines)):
        window = ""
        for j in range(i, min(i + 6, len(lines))):
            window += _norm(lines[j])
            if probe in window:
                return i + 1
            if len(window) > len(probe) + 200:
                break
    return 1


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def meta(refresh=False):
    """书库规模。缺库或索引为空时计数是 None，不是 0：0 只表示真读到了 0 行。"""
    now = time.time()
    if not refresh and _META_CACHE["value"] and now - _META_CACHE["at"] < 60:
        return _META_CACHE["value"]
    out = {
        "books": None,
        "paras": None,
        "chapters": None,
        "index_built_at": "",
        "library": "missing",
        "index": "missing",
    }
    if os.path.exists(LIB_DB):
        lib = _ro(LIB_DB)
        try:
            out["books"] = lib.execute("SELECT count(*) FROM book").fetchone()[0]
        finally:
            lib.close()
        out["library"] = "ok"
    if os.path.exists(INDEX_DB):
        con = _ro(INDEX_DB)
        try:
            vals = dict(con.execute("SELECT key,value FROM meta"))
            paras = _int_or_none(vals.get("paras"))
            if paras is None:
                paras = con.execute("SELECT count(*) FROM para").fetchone()[0]
            out["index_built_at"] = vals.get("built_at") or ""
        finally:
            con.close()
        if paras:
            out["paras"] = paras
            out["chapters"] = _int_or_none(vals.get("chapters"))
        out["index"] = "ok" if paras else "empty"
    _META_CACHE.update(at=now, value=out)
    return out


def _rounds_terms(plan):
    best = None
    for r in plan.rounds:
        if r.n == plan.best_round:
            best = r
            break
    return list(best.terms) if best else list(plan.terms)


def _row_book(row):
    return row["book"] if isinstance(row, dict) else row.book


def _pick_rows(rows, limit, book):
    """限长但保 focus：focus 书不在前 limit 条时挤掉末位换进来。"""
    out = rows[:limit]
    if book and book not in [_row_book(r) for r in out]:
        extra = next((r for r in rows if _row_book(r) == book), None)
        if extra is not None:
            out = ([*out[: limit - 1], extra]) if limit > 1 else [extra]
    return out


def _focus_pick(plan, focus):
    """精确 focus 的定位：优先 focus 章，其次 ranking 的 best_path，都没有就空。"""
    book = focus["book"]
    for entry in plan["chapters"]:
        if entry["book"] != book:
            continue
        sel = entry.get("selected") or []
        if sel:
            return {
                "book": book,
                "title": entry["title"],
                "chapter": sel[0]["chapter_title"],
                "path": sel[0]["chapter_path"],
                "line": sel[0]["line"],
                "card": 0,
                "pinned": True,
            }
    for r in plan["ranking"]:
        if r["book"] == book and r.get("best_path"):
            return {
                "book": book,
                "title": r["title"],
                "chapter": chapter_title("", r["best_path"]),
                "path": r["best_path"],
                "line": r["best_line"],
                "card": 0,
                "pinned": True,
            }
    return {
        "book": book,
        "title": focus.get("title") or "",
        "chapter": "",
        "path": "",
        "line": 0,
        "card": 0,
        "pinned": True,
        "focus_empty": True,
    }


def _pick_default(plan, cardset, select, focus=None):
    """默认结果：精确 focus → 卡片 → next → top1。返回带 book/chapter 的定位。"""
    if focus and focus.get("status") == "exact" and focus.get("book"):
        return _focus_pick(plan, focus)
    cards = cardset["cards"]
    nxt = cardset["next"]
    if cards:
        idx = select - 1 if 1 <= select <= len(cards) else 0
        c = cards[idx]
        return {
            "book": c["book"],
            "title": c["title"],
            "chapter": c["best_chapter_title"],
            "path": c["best_chapter"],
            "line": c.get("line", 1),
            "card": idx + 1,
        }
    if nxt:
        idx = select - 1 if 1 <= select <= len(nxt) else 0
        b = nxt[idx]
        sel = b.get("selected") or []
        if sel:
            return {
                "book": b["book"],
                "title": b["title"],
                "chapter": sel[0]["chapter_title"],
                "path": sel[0]["chapter_path"],
                "line": sel[0]["line"],
                "card": 0,
            }
    if plan["ranking"]:
        r = plan["ranking"][0]
        return {
            "book": r["book"],
            "title": r["title"],
            "chapter": chapter_title("", r["best_path"]),
            "path": r["best_path"],
            "line": r["best_line"],
            "card": 0,
        }
    return None


def build(question, select=0, thinking=DEFAULT_THINKING, fresh=False):
    question = (question or "").strip()
    if not question:
        raise ValueError("question 为空")
    thinking = normalize_thinking(thinking)
    understand = extract_terms(question, thinking=thinking, use_cache=not fresh)
    terms = understand["terms"]

    con = _ro(INDEX_DB)
    try:
        books = book_cards.load_books()
        book_meta = book_score.load_meta()
        hints = book_cards.query_hints(question, books, book_cards.load_concepts())
        focus = book_title.resolve_title(question, books, book_meta, hint=hints.get("title", ""))
        t_plan = time.time()
        plan = book_query.run(terms, con=con, limit=8, max_chapters=3, focus=focus)
        plan_ms = plan.ms
        cardset = book_cards.run(
            question, terms=plan.terms or terms, question=question, expand={}, feedback=None, con=con, focus=focus
        )
        cards_ms = cardset.ms

        card_dicts = []
        for _i, c in enumerate(cardset.cards, 1):
            row = {
                "book": c.book,
                "rank": c.rank,
                "title": c.title,
                "type": c.type,
                "chapters": c.chapters,
                "best_chapter": c.best_chapter,
                "best_chapter_title": c.best_chapter_title,
                "snippet": c.snippet,
                "matched_groups": list(c.matched_groups),
                "score": c.score,
                "reason": c.reason,
                "readme_status": c.readme_status,
                "line": resolve_line(c.best_chapter, c.snippet),
            }
            card_dicts.append(row)

        plan_dict = {
            "query": plan.query,
            "terms": list(plan.terms),
            "nouns": list(plan.nouns),
            "action_dropped": list(plan.action_dropped),
            "skipped": list(plan.skipped),
            "stop": plan.stop,
            "best_round": plan.best_round,
            "ms": plan_ms,
            "scheme": "best_support",
            "rounds": [
                {
                    "n": r.n,
                    "kind": r.kind,
                    "terms": list(r.terms),
                    "groups": list(r.groups),
                    "hits": r.hits,
                    "top1": r.top1,
                    "top1_score": r.top1_score,
                    "top2_score": r.top2_score,
                    "margin": r.margin,
                    "cover_paras": r.cover_paras,
                    "A": r.A,
                    "B": r.B,
                    "C": r.C,
                    "stop": r.stop,
                    "ms": r.ms,
                    "skipped": r.skipped,
                }
                for r in plan.rounds
            ],
            "ranking": [
                {
                    "book": b.book,
                    "title": b.title,
                    "type": b.type,
                    "chapters": b.chapters,
                    "score": round(b.score, 4),
                    "coverage": round(b.coverage, 4),
                    "groups": list(b.groups),
                    "total_groups": b.total_groups,
                    "best_path": b.best_path,
                    "best_line": b.best_line,
                    "best_text": " ".join((b.best_text or "").split())[:300],
                    "support": b.support,
                    "pinned": b.pinned,
                }
                for b in _pick_rows(plan.ranking, 8, focus.get("book"))
            ],
            "chapters": [
                {
                    "book": c["book"],
                    "title": c["title"],
                    "book_score": c["book_score"],
                    "pinned": bool(c.get("pinned")),
                    "selected": [
                        {
                            "chapter_path": s["chapter_path"],
                            "chapter_title": s["chapter_title"],
                            "line": s["line"],
                            "score": s["score"],
                            "snippet": s.get("snippet", ""),
                        }
                        for s in c["selected"]
                    ],
                }
                for c in _pick_rows(plan.chapters, 3, focus.get("book"))
            ],
        }

        cards_dict = {
            "trigger": bool(cardset.trigger),
            "reasons": list(cardset.reasons),
            "hints": dict(cardset.hints),
            "cards": card_dicts,
            "next": cardset.next,
            "ms": cards_ms,
            "uncertainty": cardset.uncertainty,
            "terms": list(cardset.terms),
        }

        pick = _pick_default(plan_dict, cards_dict, select, focus)
        t_res = time.time()
        result = None
        if pick and pick.pop("focus_empty", False):
            result = {
                "book": pick["book"],
                "book_title": pick["title"],
                "chapter": "",
                "path": "",
                "line": 0,
                "text": "",
                "focus_empty": True,
            }
        elif pick:
            result = read_chapter(pick["path"], pick["line"], STAGE_CHARS)
            result.update(
                {"book": pick["book"], "book_title": pick["title"], "chapter": pick["chapter"], "card": pick["card"]}
            )
        result_ms = round((time.time() - t_res) * 1000, 1)

        rounds_ms = round(sum(r.ms for r in plan.rounds), 1)
        stage_ms = {
            "understand": understand["ms"],
            "rounds": rounds_ms,
            "rank": round(max(0.0, plan_ms - rounds_ms), 1),
            "cards": cards_ms,
            "result": result_ms,
        }
        return {
            "question": question,
            "thinking": thinking,
            "extract": understand,
            "plan": plan_dict,
            "focus": focus,
            "cards": cards_dict,
            "result": result,
            "stage_ms": stage_ms,
            "meta": meta(),
            "total_ms": round((time.time() - t_plan) * 1000 + understand["ms"], 1),
        }
    finally:
        con.close()


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--select", type=int, default=0)
    ap.add_argument("--thinking", default=DEFAULT_THINKING)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    trace = build(args.question, args.select, thinking=args.thinking, fresh=args.fresh)
    text = json.dumps(trace, ensure_ascii=False)
    if args.out:
        _write_json(args.out, trace)
        print(args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
