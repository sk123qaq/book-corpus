#!/usr/bin/env python3
"""回答阶段：检索层取多段原文窗口 → pi agent 归纳 → 服务端逐条回查引文。

  from answer import answer_question
  answer_question("《三体》里为什么程心刚成为执剑人没多久三体就入侵了地球",
                  "文学/类型小说/刘慈欣/三体/死神永生",
                  "库/文学/类型小说/刘慈欣/三体/死神永生/md/03_第二部....md", 1180,
                  ["程心", "执剑人", "三体", "威慑", "黑暗森林", "威慑度"])

步骤（见 ANSWER-BRIEF.md）：
  1. book_readme.readme_toc 拿 path → 章名
  2. lib_fts_cjk.query_expr 拼表达式，qsearch.search 取同书候选段
  3. 固定当前命中在第一段，最多 6 段，同一章最多 2 段
  4. book_expand.make_packet 取有界窗口，合并重叠，最多 4 个窗口、合计 12000 字
  5. 拼提示词调 `pi -p --no-tools --no-session --no-context-files --skill ...`
  6. 解析第一个 JSON 对象，服务端回查每条引文，行号以文件实际行号为准
  7. 缓存到 缓存/answers/<sha1>.json（键含 PROMPT_VERSION 与思考等级），原子写；
     use_cache=False（页面「重新检索」）跳过读缓存，重新调模型

多书（前端全选）时请求带 sources，每本取三段窗口，一次提示词要按问题结构逐书对照回答，引文按书归并。

只读 目录/检索/index.db 与 库/**.md；只写 缓存/answers/。
不跑 B4、不写 expand、不重建索引；pi 只开 book_grep 与 read，证据全部由服务端给。
"""
import bisect
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "工作区", "脚本")
for _p in (SCRIPTS, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import book_expand  # noqa: E402
import book_readme  # noqa: E402
import defpass  # noqa: E402
import lib_fts_cjk as cjk  # noqa: E402
import qsearch  # noqa: E402
from thinking import DEFAULT as DEFAULT_THINKING, normalize as normalize_thinking  # noqa: E402

INDEX_DB = os.path.join(ROOT, "目录", "检索", "index.db")
LIBRARY_DB = os.path.join(ROOT, "目录", "library.db")
LIBRARY = os.path.join(ROOT, "库")
CACHE_DIR = os.path.join(HERE, "缓存", "answers")
SKILL = "/home/shike/.agents/skills/book-corpus-qa/SKILL.md"
MODEL = "opencode-go/deepseek-v4.1-flash"
PI_TIMEOUT = 240
PROMPT_VERSION = "answer-v12"
PROMPT_VERSION_MULTI = "answer-v19-multi"

QSEARCH_LIMIT = 100
DEFPASS_TOP = 4
MAX_CHAPTERS = 30
MAX_PASSAGES = 24
MAX_PER_CHAPTER = 2
MAX_WINDOWS = 6
WINDOW_CHARS = 3500
TOTAL_CHARS = 14000
PROMPT_MAX = 26000
MAX_CITATIONS = 6
QUOTE_PROBE = 20

MAX_SOURCES = 8
MULTI_QSEARCH_LIMIT = 60
MULTI_WINDOWS_PER_BOOK = 3
MULTI_CHAPTERS = 4
MULTI_QUOTA_MIN = 2200
MULTI_QUOTA_MAX = 3400
PROMPT_MAX_MULTI = 30000
MAX_CITATIONS_MULTI = 8

# 思考等级由页面选择（默认 high）。低档 4 窗提示词大约从 40 秒档降到 20 秒档，
# 引文回查照样过；档位进缓存键，换档不会命中上一档的答案。
EXT = os.path.join(HERE, "readonly_tools.ts")


def pi_cmd(thinking):
    return ["pi", "-p", "--no-session", "--no-context-files", "--skill", SKILL,
            "-e", EXT, "--tools", "book_grep,read", "--model", MODEL,
            "--thinking", thinking]

PROMPT = """你是书库问答的回答阶段。依据这一本书的原文回答，用自然、通顺的中文。

问题：{question}
书：{book}
本书 md 目录：{md}

你可以用两个只读工具：
- book_grep(pattern, path?, max?)：在本书 md 目录里搜正则，返回 file:line。
- read：读命中的章节文件。

先看候选章与候选窗口。候选章是按检索分排的前 {n} 章，窗口是其中最相关的几段。窗口不够，或要确认「是谁」「结局」「身份」，用 book_grep 搜人物、事件、同义说法，再 read 命中的章。窗口里只有一句主张、看不到展开时，用 book_grep 搜这个主张的关键词，读到展开段再写。不要写文件。
注意：原文里的「应该」「十有八九」「猜测」是推测，不是结论。涉及「是谁」「结局」，必须找到身份或结果被确认的段落（令牌、名号、旁白、后文），并往后多读一到两章；推测不能写进答案。

答案按问题本身的结构写，不按章节顺序写。用 Markdown 小标题分段，标题行以「## 」开头。

1. 开头 1 到 3 句给判断，放在第一个小标题之前，不加标题。问「是谁」「结局」的，开头就点名，不要留到最后一段。书里的说法和问题里的说法不一样，或者这本书只答了问题的一半，也先在开头说清。
2. 中间 2 到 4 个「## 小标题」。小标题是判断句，不要用章名、不要用「首先/其次」、不要编号。标题下面 3 到 5 句，按「是什么、为什么、后果」展开，不按章节顺序复述。
   正文默认不写书名和作者名，读者已经知道问的是哪本书。同一个概念有两种说法、两个数字时，合并成一处再并列写清差别（一处说什么，另一处说什么，差在哪），不要一一交代出处，不要写「作者先……后……」。
   每节至少用两处原文的事实支撑。同一件事书里讲了两遍，合并成一句，不要按章复述。
3. 书里前后不一致时（旁白给的结论和某个角色的说法相反，两章给出不同数字或不同人），用「## 分歧」单独一段，写清不一致的是哪一步：谁说的、说什么、哪一处证据更硬。没有就不写。不要写「侧重点不同」。
4. 书没有讲到问题的一部分时，最后可选「## 缺口」一段，写清书停在哪一步。没有就不写。
5. 不要以「《书名》：」开头写任何一行，不要一章一段，不要用「书中分别提到 A、B、C」这种列举预告开头。
6. 300 到 600 字。问题只要一句结论能答完（某句话在哪、书里有没有提过），写领句加一节就够。
7. 不写路径、行号、「原文写明」「根据原文」。依据放在 citations，最多 6 条，按这几处补齐：领句的判断一条；每个小标题的判断一条；问题里点到的每个人、每件事、每个数字各一条，落在它被确认的那一段；回答纠正问题里的说法（例如「不是他做的」「不是这个原因」）时，纠正所依据的那段必须引。一句话里合并了两处原文，两处各给一条。quote 逐字来自原文，path 用本书 md 目录里的文件路径。

书里确实没有回答时，先用 book_grep 搜同义词与别名，确认搜不到再写「书里没有直接回答」并置 not_found，不要拿相邻话题凑一段。
输出一行 JSON，不要代码块，不要解释。answer 里用 \\n 换行，节标题行以 ## 开头：
{{"answer":"开头判断\\n\\n## 小标题\\n正文\\n\\n## 分歧\\n...\\n\\n## 缺口\\n...","citations":[{{"path":"库/...md","line":1166,"quote":"逐字引文"}}],"confidence":"high|medium|low","not_found":false}}

候选章（前 {n} 章）：
{chapters}

候选窗口：
{evidence}
"""

MULTI_PROMPT = """你是书库问答的回答阶段。这次要同时用几本书的原文回答同一个问题，用自然、通顺的中文。

问题：{question}

{coverage}要用的书（{n} 本，每条后面是这本书的 md 目录）：
{books}

你可以用两个只读工具：
- book_grep(pattern, path?, max?)：在这些书的 md 目录里搜正则，返回 file:line。
- read：读命中的章节文件。

先看各书的候选章与候选窗口。候选章是各书按检索分排的前 4 章，窗口是其中最相关的几段。窗口不够，或要确认「是谁」「结局」「身份」，用 book_grep 搜人物、事件、同义说法，再 read 命中的章。不要写文件。
每本书至少确认一次它的机制章：窗口里只有一句主张、看不到展开时，用 book_grep 在该书 md 目录里搜这个主张的关键词，读到展开段再写。
注意：原文里的「应该」「十有八九」「猜测」是推测，不是结论。涉及「是谁」「结局」，必须找到身份或结果被确认的段落，并往后多读一到两章；推测不能写进答案。

答案按问题本身的结构写，不要按书排。用 Markdown 小标题分段，标题行以「## 」开头。

1. 开头 1 到 3 句给判断，放在第一个小标题之前，不加标题。库里的说法和问题里的说法不一样，或者只有一两本书真的讨论这个说法，先说这件事，并点名是哪几本。判断库里的措辞时只用「库内覆盖」里查过的词，没查过的说法不要断言库里没有。不要写「只有两三本」「一本讲……的著作」这种模糊指代。
2. 中间 2 到 4 个「## 小标题」。小标题是判断句，不要用书名、不要用「首先/其次」、不要编号。标题下面 3 到 5 句，按「是什么、为什么、后果」展开，不按来源排。
   正文默认不出现作者名和书名，也不要写「甲说」「乙认为」「丙指出」。同一个概念有几本书给了不同定义或口径，先合并成一个定义，再把口径差异并列写清（窄口径算什么，宽口径还算什么，差多少），不写谁提的。
   每段至少合并两处以上原文的事实；同一件事有几本书说，合并成一句，不要逐本复述。只有两本书的说法冲突、必须点名区分时才写书名，冲突本身放进分歧段。
   citations 只给正文里用到的原文，点不点名都行；某本确实无关，就在缺口里点名它没有相关内容。
3. 几本书在前提上不一致时用「## 分歧」单独一段，写清不一致的是哪一步：先做什么、谁是主体、外部条件算不算。分歧按观点写，不按书写；需要说明哪本书持哪一方时直接写《书名》，不要写「一本讲央地关系的著作」。不要写「侧重点不同」。
4. 最后可选「## 缺口」一段，点名到书：《书名》只有一句话、《书名》没有展开到融资环节。不要写「一本文学研究」「一本讲央地关系的著作」。没有就不写。
5. 不要以「《书名》：」开头写任何一行，不要一本书一段，不要用「几本书分别指向 A、B、C」这种列举预告开头。
6. 600 到 1000 字。不写路径、行号、「原文写明」「根据原文」。
7. 不要写成文献综述。不要写「甲把它界定为……；乙把口径放得更宽……；丙则从……入手」。要写「它首先是一套……；窄口径只算……，宽口径还包括……；它出现的原因是……」。

依据放在 citations，quote 必须逐字来自原文，path 用那本书 md 目录里的文件路径。尽量引到至少 2 个不同书的原文；只有所有书都没有相关原文时才写「书里没有直接回答」并置 not_found。
输出一行 JSON，不要代码块，不要解释。answer 里用 \\n 换行，节标题行以 ## 开头：
{{"answer":"开头判断\\n\\n## 小标题\\n正文\\n\\n## 分歧\\n...\\n\\n## 缺口\\n...","citations":[{{"path":"库/...md","line":1166,"quote":"逐字引文"}}],"confidence":"high|medium|low","not_found":false}}

各书候选章：
{chapters}

各书候选窗口：
{evidence}

{nowindow}
"""


# ---- 小工具 ---------------------------------------------------------

def _rel(path):
    """统一成 库/... 形式的相对路径（正斜杠）。"""
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    full = raw if os.path.isabs(raw) else os.path.join(ROOT, raw)
    try:
        rel = os.path.relpath(os.path.realpath(full), ROOT)
    except ValueError:
        return ""
    if rel.startswith(".."):
        return ""
    return rel.replace(os.sep, "/")


def _inside_library(rel):
    if not rel.startswith("库/") or not rel.endswith(".md"):
        return False
    real = os.path.realpath(os.path.join(ROOT, rel))
    lib = os.path.realpath(LIBRARY)
    try:
        inside = os.path.commonpath([lib, real]) == lib
    except ValueError:
        inside = False
    return inside and os.path.isfile(real)


def book_from_path(rel):
    """库/文学/.../死神永生/md/03_x.md → 文学/.../死神永生"""
    if "/md/" in rel:
        return rel[2:].split("/md/")[0]
    return ""


def _norm(text):
    return re.sub(r"\s+", "", text or "")


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def _ok(**kwargs):
    out = {"ok": True, "cached": False, "ms": 0.0, "model": MODEL,
           "thinking": DEFAULT_THINKING, "answer": "",
           "lead": "", "sections": [], "dissent": "", "gap": "",
           "citations": [], "passages": [], "confidence": "low",
           "not_found": False, "error": "", "mode": "single", "sources": []}
    out.update(kwargs)
    return out


def _ms(t0):
    return round((time.time() - t0) * 1000, 1)


def _passages(windows):
    return [{"path": w["path"], "start": w["start"], "end": w["end"],
             "chars": w["chars"], "title": w["title"]} for w in windows]


def _err(message, **kwargs):
    out = _ok(error=message)
    out["ok"] = False
    out.update(kwargs)
    return out


def cache_key(question, book, path, line, terms, groups=None,
              thinking=DEFAULT_THINKING):
    payload = {"q": question, "book": book, "path": path, "line": int(line),
               "terms": sorted({str(t) for t in terms if str(t).strip()}),
               "groups": groups or [],
               "v": PROMPT_VERSION, "model": MODEL, "thinking": thinking}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def multi_cache_key(question, sources, terms, groups=None,
                    thinking=DEFAULT_THINKING):
    """多书回答的键：书单顺序也算输入，换掉一本书就不再命中。"""
    payload = {"q": question,
               "sources": [[s["book"], s["path"], int(s["line"])] for s in sources],
               "terms": sorted({str(t) for t in terms if str(t).strip()}),
               "groups": groups or [],
               "v": PROMPT_VERSION_MULTI, "model": MODEL, "thinking": thinking}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _normalize_sources(sources):
    """把请求里的 sources 收成去重、去库外、按 path 保序的 Source 列表。"""
    out, seen = [], set()
    for item in sources or []:
        if not isinstance(item, dict):
            continue
        rel = _rel(item.get("path"))
        if not rel or not _inside_library(rel) or rel in seen:
            continue
        book = book_from_path(rel)
        if not book:
            continue
        try:
            line = max(1, int(item.get("line") or 1))
        except (TypeError, ValueError):
            line = 1
        first = defpass.first_content_line(os.path.join(ROOT, rel))
        if line < first:
            line = first
        title = item.get("title")
        seen.add(rel)
        out.append({"book": book,
                    "title": title.strip() if isinstance(title, str) else "",
                    "path": rel, "line": line})
        if len(out) >= MAX_SOURCES:
            break
    return out


def _read_cached(cache_path, t0, thinking=DEFAULT_THINKING):
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cached = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or not cached.get("ok"):
        return None
    cached = dict(cached)
    cached.setdefault("mode", "single")
    cached.setdefault("sources", [])
    cached["thinking"] = thinking
    cached["cached"] = True
    cached["ms"] = _ms(t0)
    return cached


# ---- 证据窗口 -------------------------------------------------------

def _chapter_titles(book):
    try:
        entries, status = book_readme.readme_toc(book)
    except (OSError, ValueError):
        return {}, "missing"
    return {e["path"]: e["title"] for e in entries}, status


def _title_for(titles, path):
    t = titles.get(path)
    if t:
        return t
    name = os.path.basename(path)
    if name.endswith(".md"):
        name = name[:-3]
    return book_readme.title_from_name(name) or name


def _lines(path):
    try:
        return book_expand.read_lines(path)
    except OSError:
        return []


def _group_lists(groups, terms):
    out = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        variants = [str(v).strip() for v in (g.get("variants") or []) if str(v).strip()]
        if variants:
            out.append(variants)
    if not out:
        out = [[str(t)] for t in terms if str(t).strip()]
    return out


def _cand_window(lines, line, span=12):
    a = max(1, int(line) - span)
    b = min(len(lines), int(line) + span)
    return "\n".join(lines[a - 1:b])


def pick_passages(con, book, path, line, terms, groups, limit=QSEARCH_LIMIT):
    """当前命中 + qsearch 前 limit 的组覆盖二遍 + 按章聚类候选。

    返回 (picked, chapters)。picked 是窗口候选，chapters 是给 agent 的候选章地图。
    """
    first = defpass.first_content_line(os.path.join(ROOT, path))
    if int(line) < first:
        line = first
    current = {"path": path, "line": int(line), "score": None, "coverage": None}
    group_lists = _group_lists(groups, terms)
    expr = " OR ".join("(%s)" % e for e in (cjk.query_expr(t) for t in terms) if e)
    if not expr:
        return [current], []
    try:
        hits, _elapsed = qsearch.search(con, expr, terms, [], limit, 40, False, 2000)
    except sqlite3.Error:
        return [current], []
    book_hits = [h for h in hits if h.get("book") == book and h.get("path")]
    titles, _status = _chapter_titles(book)

    by_path = {}
    for h in book_hits:
        p = h["path"]
        cur = by_path.get(p)
        if cur is None or float(h.get("score") or 0) > float(cur.get("score") or 0):
            by_path[p] = h
    chapters = sorted(by_path.values(), key=lambda h: -float(h.get("score") or 0))[:MAX_CHAPTERS]
    chapter_rows = [{"path": h["path"], "line": int(h.get("line") or 1),
                     "score": float(h.get("score") or 0),
                     "coverage": h.get("coverage"),
                     "title": _title_for(titles, h["path"])} for h in chapters]

    # 组覆盖二遍：只在前 100 个同书候选上打分，避免全库扫描把 README 和无关章冲上来
    lines_cache = {}
    scored = []
    for h in book_hits:
        p = h["path"]
        ln = int(h.get("line") or 1)
        if p == path and abs(ln - int(line)) <= 8:
            continue
        lines = lines_cache.get(p)
        if lines is None:
            lines = _lines(p)
            lines_cache[p] = lines
        if not lines:
            continue
        cov, gs = defpass.score(_cand_window(lines, ln), group_lists)
        if cov >= 2:
            scored.append((cov, gs, h, ln))
    scored.sort(key=lambda x: (-x[0], -x[1], -float(x[2].get("score") or 0)))
    seen = set()
    def_hits = []
    for cov, gs, h, ln in scored:
        key = (h["path"], ln)
        if key in seen:
            continue
        seen.add(key)
        def_hits.append({"path": h["path"], "line": ln, "score": h.get("score"),
                         "coverage": cov, "group_score": gs})
        if len(def_hits) >= DEFPASS_TOP:
            break

    picked, used = [current], {(path, int(line))}
    for item in def_hits + [{"path": h["path"], "line": int(h.get("line") or 1),
                             "score": h.get("score"), "coverage": h.get("coverage")}
                            for h in chapters]:
        key = (item["path"], item["line"])
        if key in used:
            continue
        if item["path"] == path and abs(item["line"] - int(line)) <= 8:
            continue
        used.add(key)
        picked.append(item)
        if len(picked) >= MAX_PASSAGES:
            break
    return picked, chapter_rows


def _prefix(lines):
    pref = [0]
    for line in lines:
        pref.append(pref[-1] + len(line) + 1)
    return pref


def _window_text(lines, pref, start, end, mid, cap):
    """从 [start, end] 里切出不超过 cap 的整行窗口，优先保住 mid 那一行。

    返回 (a, b, text)：a、b 是真正给出去的行，text 就是这几行，行号与内容对得上。
    """
    total = len(lines)
    a = max(1, min(total, start))
    b = max(a, min(total, end))
    mid = max(a, min(b, mid))
    while b > a and pref[b] - pref[a - 1] > cap:
        if b - mid >= mid - a and b > mid:
            b -= 1
        elif a < mid:
            a += 1
        else:
            b -= 1
    text = "\n".join(lines[a - 1:b])
    if len(text) > cap:
        text = text[:cap]
    if a > 1:
        heading = None
        for i in range(a - 1, max(0, a - 41), -1):
            if lines[i - 1].lstrip().startswith("#"):
                heading = lines[i - 1]
                break
        if heading and heading not in text:
            text = heading + "\n" + text
    return a, b, text


def build_windows(chosen, titles, status, total_chars=TOTAL_CHARS,
                  window_chars=WINDOW_CHARS, max_windows=MAX_WINDOWS):
    """按段取 make_packet 窗口，同文件重叠就合并；titles、status 按书分。

    chosen 每项要带 book、path、line；单书就是一个 book 的映射。
    """
    cache, prefix, meta = {}, {}, {}
    order, per_path = [], {}
    for p in chosen:
        path = p["path"]
        book = p.get("book", "")
        lines = cache.get(path)
        if lines is None:
            lines = _lines(path)
            cache[path] = lines
            prefix[path] = _prefix(lines)
        if not lines:
            continue
        title = _title_for(titles.get(book) or {}, path)
        meta[path] = (book, title)
        packet = book_expand.make_packet(book, path, title, lines, p["line"], 2,
                                         window_chars, status.get(book, "missing"))
        if path not in per_path:
            per_path[path] = []
            order.append(path)
        per_path[path].append((packet.window_start, packet.window_end, p["line"]))

    merged = []
    for path in order:
        for start, end, line in sorted(per_path[path]):
            cur = merged[-1] if merged and merged[-1]["path"] == path else None
            if cur and start <= cur["end"] + 1:
                cur["end"] = max(cur["end"], end)
            else:
                merged.append({"path": path, "start": start, "end": end, "mid": line})

    floor = min(800, max(200, window_chars // 2))
    windows, used = [], 0
    for item in merged:
        if len(windows) >= max_windows:
            break
        remaining = total_chars - used
        if remaining < floor:
            break
        cap = min(window_chars, remaining)
        start, end, text = _window_text(cache[item["path"]], prefix[item["path"]],
                                        item["start"], item["end"], item["mid"], cap)
        if not text.strip():
            continue
        book, title = meta[item["path"]]
        windows.append({"path": item["path"], "book": book, "title": title,
                        "start": start, "end": end, "chars": len(text), "text": text})
        used += len(text)
    return windows


def build_prompt(question, book, windows, chapters):
    blocks = ["%s:%d-%d\n%s" % (w["path"], w["start"], w["end"], w["text"])
              for w in windows]
    rows = []
    for i, c in enumerate(chapters, 1):
        cov = "-" if c.get("coverage") is None else "%.2f" % float(c["coverage"])
        rows.append("%d. %s:%d 《%s》 score=%.2f cov=%s"
                    % (i, c["path"], c["line"], c["title"], c["score"], cov))
    prompt = PROMPT.format(question=question, book=book, md="库/%s/md" % book,
                           n=len(rows), chapters="\n".join(rows),
                           evidence="\n\n".join(blocks))
    while len(prompt) > PROMPT_MAX and len(blocks) > 1:
        blocks.pop()
        prompt = PROMPT.format(question=question, book=book, md="库/%s/md" % book,
                               n=len(rows), chapters="\n".join(rows),
                               evidence="\n\n".join(blocks))
    return prompt


def _book_label(source):
    return source.get("title") or source["book"].rsplit("/", 1)[-1]


def _coverage(terms):
    """库级覆盖：用全库段落倒排数每个词的命中段数与书数。

    扫全部 terms，跳掉 count>200 的常见词，按词长降序保留最多 3 个；全被跳过
    时保留 count 最小的那个。只表示这个词在库里有没有、有多少，不是答案证据。
    查询失败返回空串，吞掉异常。
    """
    terms = [str(t).strip() for t in terms or [] if str(t).strip()]
    if not terms:
        return ""
    ordered = sorted(terms, key=len, reverse=True)  # 长词在前，同长保持原顺序
    try:
        con = sqlite3.connect("file:%s?mode=ro" % INDEX_DB, uri=True)
    except sqlite3.Error:
        return ""
    stats = []
    try:
        for term in ordered:
            expr = cjk.query_expr(term)
            if not expr:
                continue
            count = con.execute(
                "SELECT count(*) FROM para_fts WHERE para_fts MATCH ?",
                (expr,)).fetchone()[0]
            nbooks = con.execute(
                "SELECT count(DISTINCT p.book) FROM para_fts f JOIN para p ON p.id=f.rowid "
                "WHERE para_fts MATCH ?", (expr,)).fetchone()[0]
            rows = con.execute(
                "SELECT p.book, count(*) c FROM para_fts f JOIN para p ON p.id=f.rowid "
                "WHERE para_fts MATCH ? GROUP BY p.book ORDER BY c DESC LIMIT 2",
                (expr,)).fetchall()
            stats.append({"term": term, "count": count, "nbooks": nbooks,
                          "books": [(b, c) for b, c in rows]})
    except sqlite3.Error:
        return ""
    finally:
        con.close()
    if not stats:
        return ""
    picked = [s for s in stats if s["count"] <= 200][:3]
    if not picked:
        picked = [min(stats, key=lambda s: s["count"])]

    # 书名：目录/library.db 的 book 表按 path 取 title，查不到用路径最后一段
    titles = {}
    try:
        bcon = sqlite3.connect("file:%s?mode=ro" % LIBRARY_DB, uri=True)
        try:
            for s in picked:
                for b, _c in s["books"]:
                    if b not in titles:
                        row = bcon.execute(
                            "SELECT title FROM book WHERE path = ?", (b,)).fetchone()
                        titles[b] = row[0] if row else b.rsplit("/", 1)[-1]
        finally:
            bcon.close()
    except sqlite3.Error:
        pass

    def label(book):
        return titles.get(book) or book.rsplit("/", 1)[-1]

    lines = ["库内覆盖（全库段落命中数，只表示这个词在库里有没有、有多少，不是答案证据）："]
    for s in picked:
        books = s["books"]
        if len(books) == 1:
            book_part = "《%s》" % label(books[0][0])
        elif len(books) >= 2:
            book_part = "《%s》%d，《%s》%d" % (
                label(books[0][0]), books[0][1], label(books[1][0]), books[1][1])
        else:
            book_part = ""
        lines.append("- 「%s」%d 段 / %d 本%s"
                     % (s["term"], s["count"], s["nbooks"],
                        ("：" + book_part) if book_part else ""))
    return "\n".join(lines)


def multi_windows(plans):
    """每本书一个小预算：每扇窗 quota//MULTI_WINDOWS_PER_BOOK，先铺各书第一窗，再铺后面。"""
    n = len(plans)
    quota = max(MULTI_QUOTA_MIN, min(MULTI_QUOTA_MAX, 18000 // n))
    per_book = []
    for plan in plans:
        book = plan["source"]["book"]
        per_book.append(build_windows(plan["chosen"], {book: plan["titles"]},
                                      {book: plan["status"]}, total_chars=quota,
                                      window_chars=max(200, quota // MULTI_WINDOWS_PER_BOOK),
                                      max_windows=MULTI_WINDOWS_PER_BOOK))
    out = []
    for i in range(max([len(ws) for ws in per_book] or [0])):
        out.extend(ws[i] for ws in per_book if i < len(ws))
    return out


def _drop_last_window(windows, rank):
    """超长时从排名最后的书开始删它最后一扇窗；一本书至少留一扇。"""
    for book in sorted({w["book"] for w in windows}, key=lambda b: -rank.get(b, 0)):
        idxs = [i for i, w in enumerate(windows) if w["book"] == book]
        if len(idxs) > 1:
            windows.pop(idxs[-1])
            return True
    return False


def build_multi_prompt(question, plans, windows, coverage=""):
    """一次提示词装下所有书：库级覆盖 + 书名行 + 每书 4 章地图 + 按书分组的窗口。"""
    rank = {p["source"]["book"]: i for i, p in enumerate(plans)}
    labels = {p["source"]["book"]: _book_label(p["source"]) for p in plans}
    coverage = coverage.strip()
    if coverage:
        coverage += "\n\n"
    keep = list(windows)
    while True:
        book_rows, chapter_blocks = [], []
        for plan in plans:
            book = plan["source"]["book"]
            md = "库/%s/md" % book
            book_rows.append("- 《%s》 book=%s md=%s" % (labels[book], book, md))
            rows = ["《%s》 md=%s" % (labels[book], md)]
            for i, c in enumerate(plan["chapters"], 1):
                cov = "-" if c.get("coverage") is None else "%.2f" % float(c["coverage"])
                rows.append("%d. %s:%d 《%s》 score=%.2f cov=%s"
                            % (i, c["path"], c["line"], c["title"], c["score"], cov))
            chapter_blocks.append("\n".join(rows))
        blocks = ["《%s》 %s:%d-%d\n%s" % (labels.get(w["book"], ""), w["path"],
                                          w["start"], w["end"], w["text"])
                  for w in keep]
        empty = [labels[p["source"]["book"]] for p in plans
                 if not any(w["book"] == p["source"]["book"] for w in keep)]
        nowindow = ""
        if empty:
            nowindow = ("无相关原文窗口的书：《%s》（这些书在回答里要写明没有相关原文）"
                        % "》《".join(empty))
        prompt = MULTI_PROMPT.format(
            question=question, coverage=coverage, n=len(plans),
            books="\n".join(book_rows),
            chapters="\n\n".join(chapter_blocks), evidence="\n\n".join(blocks),
            nowindow=nowindow)
        if len(prompt) <= PROMPT_MAX_MULTI or not _drop_last_window(keep, rank):
            return prompt


# ---- 调模型 ---------------------------------------------------------

def call_pi(prompt, book, books=None, thinking=DEFAULT_THINKING):
    """返回 (raw_text, error)。失败时 raw_text 为 None。"""
    roots = ["库/%s/md" % b for b in (books or [book])]
    env = dict(os.environ)
    env["BOOK_MD"] = roots[0]
    env["BOOK_MDS"] = json.dumps(roots, ensure_ascii=False)
    try:
        res = subprocess.run(pi_cmd(normalize_thinking(thinking)) + [prompt],
                             capture_output=True, text=True,
                             timeout=PI_TIMEOUT, cwd=ROOT, env=env)
    except subprocess.TimeoutExpired:
        return None, "回答超时（%d 秒）" % PI_TIMEOUT
    except OSError as exc:
        return None, "调不起 pi：%s" % exc.__class__.__name__
    if res.returncode != 0:
        tail = " ".join((res.stderr or "").split())[:240]
        return None, "模型调用失败（rc=%d）%s" % (res.returncode, ("：" + tail) if tail else "")
    out = res.stdout or ""
    if not out.strip():
        return None, "模型没有输出"
    return out, ""


def parse_reply(raw):
    """去掉代码块围栏，取第一个括号配平的 JSON 回答对象。

    只认带 answer/lead/sections/citations 这类字段的对象。外层坏了的时候，
    里面的 citation 对象也能配平，但它不是回答，不能拿来当 reply。
    """
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
                    if isinstance(obj, dict) and any(
                            k in obj for k in ("answer", "lead", "sections",
                                               "citations", "confidence", "not_found")):
                        return obj
                    break
        start = text.find("{", start + 1)
    return None


def _split_answer(text):
    """把带「## 小标题」的 answer 拆成 lead、sections、dissent、gap。

    第一个小标题之前是 lead；标题以分歧/不同/差异开头的块是 dissent，以缺口/不足/
    欠缺开头的块是 gap，其余是 section。空块丢掉，最多 4 节。
    """
    blocks, lead_lines, cur = [], [], None
    for line in text.splitlines():
        m = re.match(r"^\s*#{1,4}\s+(.+?)\s*$", line)
        if m:
            cur = [m.group(1).strip(), []]
            blocks.append(cur)
        elif cur is None:
            lead_lines.append(line)
        else:
            cur[1].append(line)
    lead = "\n".join(lead_lines).strip()
    sections, dissent, gap = [], "", ""
    for heading, lines in blocks:
        body = "\n".join(lines).strip()
        if not heading or not body:
            continue
        if not dissent and re.match(r"^(分歧|不同|差异)", heading):
            dissent = body
        elif not gap and re.match(r"^(缺口|不足|欠缺|未展开)", heading):
            gap = body
        elif len(sections) < 4:
            sections.append({"heading": heading, "body": body})
    return lead, sections, dissent, gap


def _shape_answer(reply):
    """单书与多书共用：优先把 answer 里的「## 小标题」拆成结构，旧字段仍然接受。"""
    text = reply.get("answer")
    text = text.strip() if isinstance(text, str) else ""
    if text and re.search(r"^\s*#{1,4}\s+\S", text, re.M):
        lead, sections, dissent, gap = _split_answer(text)
    else:
        lead = reply.get("lead")
        lead = lead.strip() if isinstance(lead, str) else ""
        dissent = reply.get("dissent")
        dissent = dissent.strip() if isinstance(dissent, str) else ""
        gap = reply.get("gap")
        gap = gap.strip() if isinstance(gap, str) else ""
        sections = []
        raw_sections = reply.get("sections")
        if isinstance(raw_sections, list):
            for item in raw_sections:
                if isinstance(item, dict):
                    heading, body = item.get("heading"), item.get("body")
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    heading, body = item
                else:
                    continue
                if not isinstance(heading, str) or not isinstance(body, str):
                    continue
                heading, body = heading.strip(), body.strip()
                if not heading or not body:
                    continue
                sections.append({"heading": heading, "body": body})
                if len(sections) >= 4:
                    break

    parts = [lead]
    for s in sections:
        parts.append(s["heading"] + "\n" + s["body"])
    if dissent:
        parts.append(dissent)
    if gap:
        parts.append(gap)
    answer = "\n\n".join(p for p in parts if p)
    if not answer and text:
        answer = text
    return lead, sections, dissent, gap, answer


# ---- 回查引文 -------------------------------------------------------

class _ChapterIndex(object):
    """按去空白文本回查：先逐行，再整章（跨行引文），行号以文件为准。"""

    def __init__(self, rel):
        self.rel = rel
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
            self.lines = fh.read().splitlines()
        self.norm_lines = [_norm(l) for l in self.lines]
        self.offsets, self.full = [], []
        pos = 0
        for nl in self.norm_lines:
            self.offsets.append(pos)
            self.full.append(nl)
            pos += len(nl)
        self.full = "".join(self.full)

    def locate(self, needle):
        if not needle:
            return 0
        for i, nl in enumerate(self.norm_lines):
            if needle in nl:
                return i + 1
        idx = self.full.find(needle)
        if idx < 0:
            return 0
        return bisect.bisect_right(self.offsets, idx)


def _locate_in(rel, needle, indexes):
    idx = indexes.get(rel)
    if idx is None:
        try:
            idx = _ChapterIndex(rel)
        except OSError:
            return 0
        indexes[rel] = idx
    return idx.locate(needle)


def verify_citations(cites, windows, hit_path="", hit_line=0, sources=None):
    """只留回查通过的引文；行号用文件实际行号，不信模型报的。

    单书：回查后再按「离命中行近的排前面」排序并截到 6 条，固定的第一段就是命中段，
    邻近的引文最能支撑它。多书：path 必须落在本次书单内，按书轮取，最多 8 条。
    """
    if sources:
        return _verify_multi(cites, windows, sources)
    evidence = [w["path"] for w in windows]
    basenames = {}
    for p in evidence:
        basenames.setdefault(os.path.basename(p), []).append(p)
    indexes = {}
    out, seen, dropped = [], set(), 0
    for item in cites or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        quote = item.get("quote")
        if not isinstance(quote, str):
            dropped += 1
            continue
        needle = _norm(quote)[:QUOTE_PROBE]
        if len(needle) < 4:
            dropped += 1
            continue
        rel = _rel(item.get("path"))
        if not _inside_library(rel):
            same = basenames.get(os.path.basename(rel or ""), [])
            rel = same[0] if len(same) == 1 else ""
        if not rel or not _inside_library(rel):
            dropped += 1
            continue
        line = _locate_in(rel, needle, indexes)
        if not line:
            dropped += 1
            continue
        key = (rel, line)
        if key in seen:
            continue
        seen.add(key)
        title = next((w["title"] for w in windows if w["path"] == rel), "")
        out.append({"path": rel, "line": line, "quote": quote.strip(),
                    "chapter": title, "verified": True})
    out.sort(key=lambda c: (c["path"] != hit_path, abs(c["line"] - hit_line)))
    return out[:MAX_CITATIONS], dropped


def _verify_multi(cites, windows, sources):
    """多书回查：不许跨书同名文件兑底，书只能来自本次书单。"""
    scope = {s["book"]: s for s in sources}
    chapter_of = {w["path"]: w["title"] for w in windows}
    indexes, seen, dropped = {}, set(), 0
    by_book = {}
    for item in cites or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        quote = item.get("quote")
        if not isinstance(quote, str):
            dropped += 1
            continue
        needle = _norm(quote)[:QUOTE_PROBE]
        if len(needle) < 4:
            dropped += 1
            continue
        rel = _rel(item.get("path"))
        if not _inside_library(rel):
            dropped += 1
            continue
        book = book_from_path(rel)
        if book not in scope:
            dropped += 1
            continue
        line = _locate_in(rel, needle, indexes)
        if not line:
            dropped += 1
            continue
        key = (rel, line)
        if key in seen:
            continue
        seen.add(key)
        by_book.setdefault(book, []).append({
            "path": rel, "line": line, "quote": quote.strip(),
            "chapter": chapter_of.get(rel, ""), "verified": True,
            "book": book, "book_title": _book_label(scope[book]),
        })
    for book, items in by_book.items():
        seed = int(scope[book].get("line") or 1)
        items.sort(key=lambda c: abs(c["line"] - seed))
    out = []
    for round_i in range(MAX_CITATIONS_MULTI):
        added = False
        for src in sources:
            items = by_book.get(src["book"]) or []
            if round_i < len(items):
                out.append(items[round_i])
                added = True
                if len(out) >= MAX_CITATIONS_MULTI:
                    return out, dropped
        if not added:
            break
    return out, dropped


# ---- 主流程 ---------------------------------------------------------

def answer_question(question, book, path, line=1, terms=None, groups=None,
                    use_cache=True, sources=None, thinking=DEFAULT_THINKING):
    """返回 Answer dict（结构见 ANSWER-BRIEF.md 的「数据形状」）。

    use_cache=False 即前端「重新检索」：跳过答案缓存并重新调模型，本次结果照样回写。
    """
    t0 = time.time()
    question = (question or "").strip()
    if not question:
        return _err("question 为空", ms=_ms(t0))
    thinking = normalize_thinking(thinking)
    terms = [str(t).strip() for t in (terms or []) if str(t).strip()][:12]
    groups = [g for g in (groups or []) if isinstance(g, dict) and g.get("variants")]
    scope = _normalize_sources(sources)
    if len(scope) >= 2:
        return _answer_multi(question, scope, terms, groups, use_cache, t0, thinking)
    return _answer_single(question, book, path, line, terms, groups, use_cache, t0,
                          thinking)


def _answer_single(question, book, path, line, terms, groups, use_cache, t0,
                   thinking=DEFAULT_THINKING):
    rel = _rel(path)
    if not _inside_library(rel):
        return _err("path 不在 库/ 下或不是 .md", ms=_ms(t0), thinking=thinking)
    try:
        line = max(1, int(line or 1))
    except (TypeError, ValueError):
        line = 1
    book = (book or "").strip() or book_from_path(rel)
    first = defpass.first_content_line(os.path.join(ROOT, rel))
    if line < first:
        line = first

    key = cache_key(question, book, rel, line, terms, groups, thinking)
    cache_path = os.path.join(CACHE_DIR, key + ".json")
    if use_cache:
        cached = _read_cached(cache_path, t0, thinking)
        if cached:
            return cached

    titles, status = _chapter_titles(book)
    con = sqlite3.connect("file:%s?mode=ro" % INDEX_DB, uri=True)
    try:
        chosen, chapters = pick_passages(con, book, rel, line, terms, groups)
    finally:
        con.close()
    for item in chosen:
        item["book"] = book
    windows = build_windows(chosen, {book: titles}, {book: status})
    if not windows:
        return _err("没有取到原文窗口", ms=_ms(t0), thinking=thinking)

    prompt = build_prompt(question, book, windows, chapters)
    raw, error = call_pi(prompt, book, thinking=thinking)
    if raw is None:
        return _err(error, passages=_passages(windows), ms=_ms(t0),
                    thinking=thinking)
    reply = parse_reply(raw)
    if reply is None:
        head = " ".join((raw or "").split())[:200]
        return _err("模型没有返回 JSON：%s" % head, passages=_passages(windows),
                    ms=_ms(t0), thinking=thinking)

    lead, sections, dissent, gap, answer = _shape_answer(reply)
    not_found = bool(reply.get("not_found")) or not answer
    citations, _dropped = verify_citations(reply.get("citations"), windows, rel, line)
    confidence = reply.get("confidence")
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    error = ""
    if len(citations) >= 2:
        pass
    elif citations:
        confidence = "low"
        error = "引文回查不足（%d 条）" % len(citations)
    else:
        confidence = "low"
        error = "引文回查不足（0 条）"
    if not answer:
        answer = "未找到原文依据"

    out = _ok(answer=answer, citations=citations, passages=_passages(windows),
              confidence=confidence, not_found=not_found, error=error, ms=_ms(t0),
              lead=lead, sections=sections, dissent=dissent, gap=gap,
              thinking=thinking)
    try:
        _write_json(cache_path, out)
    except OSError:
        pass
    return out


def _answer_multi(question, sources, terms, groups, use_cache, t0,
                  thinking=DEFAULT_THINKING):
    """多书：每本取三段，一次提示词逐书回答，引文按书归并。"""
    key = multi_cache_key(question, sources, terms, groups, thinking)
    cache_path = os.path.join(CACHE_DIR, key + ".json")
    if use_cache:
        cached = _read_cached(cache_path, t0, thinking)
        if cached:
            return cached

    plans = []
    con = sqlite3.connect("file:%s?mode=ro" % INDEX_DB, uri=True)
    try:
        for src in sources:
            titles, status = _chapter_titles(src["book"])
            picked, chapters = pick_passages(con, src["book"], src["path"], src["line"],
                                             terms, groups, MULTI_QSEARCH_LIMIT)
            chosen = [picked[0]] if picked and int(picked[0].get("line") or 0) > 1 else []
            for item in picked[1:]:
                if len(chosen) >= MULTI_WINDOWS_PER_BOOK:
                    break
                chosen.append(item)
            plans.append({"source": src, "titles": titles, "status": status,
                          "chosen": [dict(item, book=src["book"]) for item in chosen],
                          "chapters": chapters[:MULTI_CHAPTERS]})
    finally:
        con.close()

    windows = multi_windows(plans)
    if not windows:
        return _err("没有取到原文窗口", passages=[], ms=_ms(t0), thinking=thinking)
    coverage = _coverage(terms)
    prompt = build_multi_prompt(question, plans, windows, coverage)
    raw, reply, error = None, None, ""
    for _attempt in range(2):
        raw, error = call_pi(prompt, sources[0]["book"],
                             [s["book"] for s in sources], thinking=thinking)
        if raw is None:
            break
        reply = parse_reply(raw)
        if reply is None:
            error = "模型没有返回 JSON"
            continue
        _lead, _sections, _dissent, _gap, _answer = _shape_answer(reply)
        if _answer or reply.get("not_found"):
            break
        # 模型给了 JSON 但没有任何正文，换一次提醒再要一遍，别当成「库里没有」。
        error = "模型没有给出答案结构"
        prompt += ("\n\n上一次没有给出正文。这次只输出一个 JSON 对象，"
                   "answer 里用 ## 小标题分成至少两节，分歧用 ## 分歧，缺口用 ## 缺口。")
    if raw is None:
        return _err(error, passages=_passages(windows), ms=_ms(t0),
                    thinking=thinking)
    if reply is None:
        head = " ".join((raw or "").split())[:200]
        return _err("%s：%s" % (error or "模型没有返回 JSON", head),
                    passages=_passages(windows), ms=_ms(t0), thinking=thinking)

    lead, sections, dissent, gap, answer = _shape_answer(reply)
    if not answer and not reply.get("not_found"):
        return _err(error or "模型没有给出答案结构", passages=_passages(windows),
                    ms=_ms(t0), thinking=thinking)
    not_found = bool(reply.get("not_found")) or not answer
    citations, _dropped = verify_citations(reply.get("citations"), windows,
                                           sources=sources)
    confidence = reply.get("confidence")
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    covered = {c["book"] for c in citations}
    error = ""
    if len(citations) >= 2 and len(covered) >= 2:
        pass
    elif len(citations) >= 2:
        error = "引文只覆盖 1 本书"
    elif citations:
        confidence = "low"
        error = "引文回查不足（%d 条）" % len(citations)
    else:
        confidence = "low"
        error = "引文回查不足（0 条）"
    if not answer:
        answer = "未找到原文依据"

    counts = {}
    for c in citations:
        counts[c["book"]] = counts.get(c["book"], 0) + 1
    out = _ok(answer=answer, lead=lead, sections=sections, dissent=dissent, gap=gap,
              citations=citations, passages=_passages(windows),
              confidence=confidence, not_found=not_found, error=error, ms=_ms(t0),
              thinking=thinking)
    out["mode"] = "multi"
    out["sources"] = [{"book": s["book"], "title": _book_label(s), "path": s["path"],
                       "line": s["line"], "citations": counts.get(s["book"], 0)}
                      for s in sources]
    try:
        _write_json(cache_path, out)
    except OSError:
        pass
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--book", default="")
    ap.add_argument("--path", required=True)
    ap.add_argument("--line", type=int, default=1)
    ap.add_argument("--terms", default="")
    ap.add_argument("--thinking", default=DEFAULT_THINKING)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)
    terms = [t for t in re.split(r"[\s,，]+", args.terms) if t]
    out = answer_question(args.question, args.book, args.path, args.line, terms,
                          use_cache=not args.no_cache, thinking=args.thinking)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
