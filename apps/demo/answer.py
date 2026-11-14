#!/usr/bin/env python3
"""回答阶段：检索层取多段原文窗口 → pi agent 归纳 → 服务端逐条回查引文。

  from answer import answer_question
  answer_question("《三体》里为什么程心刚成为执剑人没多久三体就入侵了地球",
                  "文学/类型小说/刘慈欣/三体/死神永生",
                  "books/B0123/md/03_第二部....md", 1180,
                  ["程心", "执剑人", "三体", "威慑", "黑暗森林", "威慑度"])

步骤（见 ANSWER-BRIEF.md）：
  1. book_readme.readme_toc 拿 path → 章名
  2. lib_fts_cjk.query_expr 拼表达式，qsearch.search 取同书候选段
  3. 固定当前命中在第一段，最多 24 段，同一章最多 2 段
  4. book_expand.make_packet 取有界窗口，合并重叠，最多 5 个窗口、合计 18000 字
  5. 拼提示词调 `pi -p --tools book_grep,read --no-session --no-context-files --skill ...`
  6. 解析第一个 JSON 对象，服务端回查每条引文，行号以文件实际行号为准
  7. 缓存到 缓存/answers/<sha1>.json（键含 PROMPT_VERSION 与思考等级），原子写；
     use_cache=False（页面「重新检索」）跳过读缓存，重新调模型

多书（前端全选）时请求带 sources，每本最多两窗，一次提示词写完整篇，引文按书归并。
点名一本书（focus.status == "exact"）时把该书写手排在最前、最多拿一半证据预算，
其余书补角度；整篇仍是一次模型调用，不拆主答与补充。

只读 build/检索/index.db 与 books/**.md；只写 缓存/answers/。
不跑 B4、不写 expand、不重建索引；pi 只开 book_grep 与 read，证据全部由服务端给。
"""

import bisect
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time


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


import book_expand
import book_readme
import defpass
import lib_common as L
import lib_fts_cjk as cjk
import model_call
import qsearch
import runlog
from thinking import DEFAULT as DEFAULT_THINKING
from thinking import normalize as normalize_thinking

ROOT = L.ROOT

INDEX_DB = os.path.join(ROOT, "build", "检索", "index.db")
LIBRARY_DB = os.path.join(ROOT, "build", "library.db")
LIBRARY = os.path.join(ROOT, "books")
CACHE_DIR = os.path.join(HERE, "缓存", "answers")
SKILL = os.path.join(L.skill_dir("book-corpus-qa"), "SKILL.md")
try:  # 项目配的模型与思考等级；没装包时退回写死的那一套
    from bookcorpus import pimodel

    _CFG = pimodel.load(ROOT)
    DEFAULT_THINKING = normalize_thinking(pimodel.thinking(_CFG["values"]), DEFAULT_THINKING)
except ImportError:
    pimodel, _CFG = None, None
MODEL = (
    os.environ.get("ANSWER_MODEL")
    or (pimodel.ref(_CFG["values"]) if pimodel else "")
    or "opencode-go/deepseek-v4.1-flash"
)
PI_TIMEOUT = 240
PROMPT_VERSION = "answer-v14"
# 点名一本书时也走一次写手：点名书排在最前，其他书只补它没有的角度。
# 不再分成「主答」「补充」两次调用，版本统一到多书写手。
PROMPT_VERSION_MULTI = "answer-v20-multi"

JSON_REMIND = (
    '\n\n上一次的输出不是合法 JSON。这次只输出一个 JSON 对象，不要代码块，不要解释，字符串里的双引号写成 \\"。'
)
ANSWER_REMIND = (
    "\n\n上一次没有给出正文。这次只输出一个 JSON 对象，"
    "answer 里开头先给判断，下面用 ## 小标题分段；不要为了凑结构另开分歧或缺口段。"
)

QSEARCH_LIMIT = 100
DEFPASS_TOP = 4
MAX_CHAPTERS = 30
MAX_PASSAGES = 24
MAX_PER_CHAPTER = 2
MAX_WINDOWS = 5
WINDOW_CHARS = 3500
TOTAL_CHARS = 18000
# 5 个满窗是 17500 字，落在 18000 内。实验见「窗口参数-决策记录.md」：
# 单事实题 N=3 到 N=5 已饱和；长论证和凡人题靠 W=3500；N=7 只在多证据边界
# 多一条证据，默认不付这个成本。改窗数要同时升 PROMPT_VERSION 和更新文档。
PROMPT_MAX = 26000
# 上一轮的要点和章节表，不再整段搬上一轮回答。
HISTORY_CHARS_SINGLE = 3000
MAX_ANSWER_CHARS = 5000
MAX_CITATIONS = 10
QUOTE_PROBE = 20

MAX_SOURCES = 8
MULTI_QSEARCH_LIMIT = 60
MULTI_WINDOWS_PER_BOOK = 2
MULTI_CHAPTERS = 4
MULTI_QUOTA_MIN = 2200
MULTI_FOCUS_SHARE = 0.5
MULTI_MIN_SCORE = 2.0
PROMPT_MAX_MULTI = 30000
HISTORY_CHARS_MULTI = 3000
MAX_CITATIONS_MULTI = 10

# 思考等级由页面选择（默认 high）。低档 4 窗提示词大约从 40 秒档降到 20 秒档，
# 引文回查照样过；档位进缓存键，换档不会命中上一档的答案。
EXT = os.path.join(HERE, "readonly_tools.ts")


def pi_cmd(thinking):
    if pimodel:
        head, flags = pimodel.pi_head(), pimodel.model_flags(_CFG["values"], thinking)
    else:
        head, flags = ["pi"], ["--model", MODEL, "--thinking", thinking]
    return [
        *head,
        "-p",
        "--no-session",
        "--no-context-files",
        "--skill",
        SKILL,
        "-e",
        EXT,
        "--tools",
        "book_grep,read",
        *flags,
    ]


def pi_json_cmd(thinking):
    """与 pi_cmd 同一套参数，在 -p 前插 --mode json，拿结构化事件流。"""
    cmd = pi_cmd(thinking)
    try:
        at = cmd.index("-p")
    except ValueError:
        return cmd
    return [*cmd[:at], "--mode", "json", *cmd[at:]]


PROMPT = """你是书库问答的回答阶段。依据这一本书的原文回答，用自然、通顺的中文。

问题：{question}
书：{book}
本书 md 目录：{md}
{history}
你可以用两个只读工具：
- book_grep(pattern, path?, max?)：在本书 md 目录里搜正则，返回 file:line。
- read：读命中的章节文件。

先看候选章与候选窗口。候选章是按检索分排的前 {n} 章，窗口是其中最相关的几段。窗口不够，或要确认「是谁」「结局」「身份」，用 book_grep 搜人物、事件、同义说法，再 read 命中的章。窗口里只有一句主张、看不到展开时，用 book_grep 搜这个主张的关键词，读到展开段再写。不要写文件。
注意：原文里的「应该」「十有八九」「猜测」是推测，不是结论。涉及「是谁」「结局」，必须找到身份或结果被确认的段落（令牌、名号、旁白、后文），并往后多读一到两章；推测不能写进答案。

答案按问题本身的结构写，不按章节顺序写。用 Markdown 小标题分段，标题行以「## 」开头。

先判断这一问要什么：一张名单、一段过程、一个原因，还是一句话落在哪。要名单就列全，要过程就按时间走，要原因就分主次，要定位就直接给段落。答案的形状跟着问题走，不套固定模板。

1. 开头 1 到 4 句给判断，放在第一个小标题之前，不加标题。问「是谁」「结局」的，开头就点名，不要留到最后一段。书里的说法和问题里的说法不一样，或者这本书只答了问题的一半，也先在开头说清。
2. 中间 1 到 6 节，可以有小标题，也可以没有。有小标题时，标题可以是判断句，也可以是名词短语（「两封邮件」「谁先动手」），不要用章名、不要用「首先/其次」、不要编号。每节开头接住上一节留下的问题，再往下讲；节与节之间要有推进，不要并列三块。
3. 顺序可以按时间、按层次、按表面到内里，选最省读者力气的一种。不要把同一件事拆成「背景、经过、影响」三节重复说。
4. 该出现的人、对话、场景、数字要出现。人物第一次出现时给一句身份；数字要带基准（哪一年、哪个季度、占什么的多少）。不要为了简洁把三件事压成一句。
5. 每个判断至少有一处原文事实支撑。一处原文可以支撑几个判断，不要把一句话拆成两句来凑引文。citations 最多 10 条。
6. 书里前后不一致时，在相关那一节里直接写清哪一处和哪一处对不上：谁说的、说什么、哪一处证据更硬。不要另开「分歧」段。只有一种说法、无法确认时，用一句「书里只有这一种说法」带过。
7. 书没有讲到的地方，如果影响回答，在结尾一句带过；不影响就不写。不要另开「缺口」段。
8. 不超过 5000 字；事实少就写短。能用一段说清就写一段，不要为了结构凑节；事实多就展开，不要为了压字数把三件事挤成一句。不重复，不写「综上所述」。
9. 不写路径、行号、「原文写明」「根据原文」。不要以「《书名》：」开头写任何一行。quote 逐字来自原文，path 用本书 md 目录里的文件路径。

书里确实没有回答时，先用 book_grep 搜同义词与别名，确认搜不到再写「书里没有直接回答」并置 not_found，不要拿相邻话题凑一段。
输出一行 JSON，不要代码块，不要解释。answer 里用 \\n 换行，节标题行以 ## 开头；recap 写三条，每条不超过 40 字，留给下一问定位用：
{{"answer":"...","recap":["...","...","..."],"citations":[{{"path":"books/<uid>/md/...md","line":1166,"quote":"逐字引文"}}],"confidence":"high|medium|low","not_found":false}}

候选章（前 {n} 章）：
{chapters}

候选窗口：
{evidence}
"""

MULTI_PROMPT = """你是书库问答的回答阶段。这次要同时用几本书的原文回答同一个问题，用自然、通顺的中文。

问题：{question}

{coverage}{focus}要用的书（{n} 本，每条后面是这本书的 md 目录）：
{books}
{history}
你可以用两个只读工具：
- book_grep(pattern, path?, max?)：在这些书的 md 目录里搜正则，返回 file:line。
- read：读命中的章节文件。

先看各书的候选章与候选窗口。候选章是各书按检索分排的前 4 章，窗口是其中最相关的几段。窗口不够，或要确认「是谁」「结局」「身份」，用 book_grep 搜人物、事件、同义说法，再 read 命中的章。不要写文件。
有窗口的书至少确认一次它的机制章：窗口里只有一句主张、看不到展开时，用 book_grep 在该书 md 目录里搜这个主张的关键词，读到展开段再写。没有窗口的书不要硬找。
注意：原文里的「应该」「十有八九」「猜测」是推测，不是结论。涉及「是谁」「结局」，必须找到身份或结果被确认的段落，并往后多读一到两章；推测不能写进答案。

答案是一个整体，不是几本书的摘要拼起来。先判断这一问要什么，再决定用哪几本书、按什么顺序讲。用 Markdown 小标题分段，标题行以「## 」开头。

1. 开头 1 到 4 句给判断，放在第一个小标题之前，不加标题。先说清哪几本书真的答了这个问题，哪几本只是擦边；点名书有答案就先说它的答案，没有就一句话说清，再从有答案的书往下写。判断库里的措辞时只用「库内覆盖」里查过的词，没查过的说法不要断言库里没有。不要写「只有两三本」「一本讲……的著作」这种模糊指代。
2. 中间 1 到 6 节，可以有小标题，也可以没有。有小标题时，标题可以是判断句，也可以是名词短语，不要用书名、不要用「首先/其次」、不要编号。每节开头接住上一节留下的问题，再往下讲；节与节之间要有推进，不要一本书一段，也不要并列三块。
3. 几本书讲同一件事时，合并成一段讲，不要逐本复述。数字、定义、口径不一致时，在相关那一节里并列写清：一处说多少、另一处说多少、差在哪。需要点名时直接写《书名》，不要写「一本讲……的著作」。
4. 点名书优先：它的窗口最多，先用它回答；其他书只补它没有的角度。点名书没有回答时，不要硬写，开头一句说清，再从其他书答。没有相关原文的书最多一句带过，不要单独成段。
5. 该出现的人、对话、场景、数字要出现。人物第一次出现时给一句身份；数字要带基准。不要为了简洁把三件事压成一句。
6. 每个判断至少有一处原文事实支撑。一处原文可以支撑几个判断，不要把一句话拆成两句来凑引文。citations 最多 10 条，尽量落到两本以上；某本确实无关，就在结尾点一句它没有相关内容。
7. 几本书在前提上不一致时，在相关那一节里写清哪一步对不上：先做什么、谁是主体、外部条件算不算。不要另开「分歧」段。只有一种说法时，用一句「书里只有这一种说法」带过。
8. 没有讲到的地方，如果影响回答，在结尾一句带过；不影响就不写。不要另开「缺口」段。
9. 不超过 5000 字；事实少就写短。能不分段就不分段，事实多就展开。不要写成文献综述，不要写「甲把它界定为……；乙……；丙……」。不写路径、行号、「原文写明」「根据原文」。
10. 不要以「《书名》：」开头写任何一行，不要用「几本书分别指向 A、B、C」这种列举预告开头。

依据放在 citations，quote 必须逐字来自原文，path 用那本书 md 目录里的文件路径。只有所有书都没有相关原文时才写「书里没有直接回答」并置 not_found。
输出一行 JSON，不要代码块，不要解释。answer 里用 \\n 换行，节标题行以 ## 开头；recap 写三条，每条不超过 40 字，留给下一问定位用：
{{"answer":"...","recap":["...","...","..."],"citations":[{{"path":"books/<uid>/md/...md","line":1166,"quote":"逐字引文"}}],"confidence":"high|medium|low","not_found":false}}

各书候选章：
{chapters}

各书候选窗口：
{evidence}

{nowindow}
"""


# ---- 小工具 ---------------------------------------------------------


def _rel(path):
    """统一成 books/<uid>/... 形式的相对路径（正斜杠）。"""
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
    if not rel.startswith("books/") or not rel.endswith(".md"):
        return False
    real = os.path.realpath(os.path.join(ROOT, rel))
    lib = os.path.realpath(LIBRARY)
    try:
        inside = os.path.commonpath([lib, real]) == lib
    except ValueError:
        inside = False
    return inside and os.path.isfile(real)


def book_from_path(rel):
    """books/B0123/md/03_x.md → B0123"""
    if rel.startswith("books/") and "/md/" in rel:
        return rel.split("/")[1]
    return ""


def _norm(text):
    return re.sub(r"\s+", "", text or "")


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 线程号进临时名：两个标签页同时算同一问时，不至于互写同一个 tmp
    tmp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def _ok(**kwargs):
    out = {
        "ok": True,
        "cached": False,
        "ms": 0.0,
        "model": MODEL,
        "thinking": DEFAULT_THINKING,
        "answer": "",
        "lead": "",
        "sections": [],
        "dissent": "",
        "gap": "",
        "citations": [],
        "passages": [],
        "confidence": "low",
        "not_found": False,
        "error": "",
        "mode": "single",
        "sources": [],
        "recap": [],
    }
    out.update(kwargs)
    return out


def _ms(t0):
    return round((time.time() - t0) * 1000, 1)


def _passages(windows):
    return [
        {"path": w["path"], "start": w["start"], "end": w["end"], "chars": w["chars"], "title": w["title"]}
        for w in windows
    ]


def _err(message, **kwargs):
    out = _ok(error=message)
    out["ok"] = False
    out.update(kwargs)
    return out


def cache_key(question, book, path, line, terms, groups=None, thinking=DEFAULT_THINKING, history="", version=None):
    payload = {
        "q": question,
        "book": book,
        "path": path,
        "line": int(line),
        "terms": sorted({str(t) for t in terms if str(t).strip()}),
        "groups": groups or [],
        "v": version or PROMPT_VERSION,
        "model": MODEL,
        "thinking": thinking,
    }
    if history:
        payload["h"] = hashlib.sha1(history.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


def multi_cache_key(
    question,
    sources,
    terms,
    groups=None,
    thinking=DEFAULT_THINKING,
    history="",
    version=None,
    focus_title="",
    focus_book="",
):
    """多书回答的键：书单顺序也算输入，换掉一本书就不再命中；点名书也算输入。"""
    payload = {
        "q": question,
        "sources": [[s["book"], s["path"], int(s["line"])] for s in sources],
        "terms": sorted({str(t) for t in terms if str(t).strip()}),
        "groups": groups or [],
        "v": version or PROMPT_VERSION_MULTI,
        "focus": focus_title,
        "focus_book": focus_book,
        "model": MODEL,
        "thinking": thinking,
    }
    if history:
        payload["h"] = hashlib.sha1(history.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


def _normalize_sources(sources, report=None):
    """把请求里的 sources 收成去重、去库外、按 path 保序的 Source 列表。"""
    items = sources or []
    stats = {
        "total": len(items),
        "not_dict": 0,
        "bad_path": 0,
        "outside": 0,
        "duplicate": 0,
        "no_book": 0,
        "over_limit": 0,
        "dropped": [],
        "kept": 0,
    }
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            stats["not_dict"] += 1
            continue
        rel = _rel(item.get("path"))
        if not rel:
            stats["bad_path"] += 1
            continue
        if not _inside_library(rel):
            stats["outside"] += 1
            continue
        if rel in seen:
            stats["duplicate"] += 1
            continue
        book = book_from_path(rel)
        if not book:
            stats["no_book"] += 1
            continue
        try:
            line = max(1, int(item.get("line") or 1))
        except (TypeError, ValueError):
            line = 1
        first = defpass.first_content_line(os.path.join(ROOT, rel))
        if line < first:
            line = first
        if len(out) >= MAX_SOURCES:
            stats["over_limit"] += 1
            stats["dropped"].append({"book": book, "path": rel, "line": line})
            continue
        title = item.get("title")
        seen.add(rel)
        out.append({"book": book, "title": title.strip() if isinstance(title, str) else "", "path": rel, "line": line})
    stats["kept"] = len(out)
    if report is not None:
        report.update(stats)
    return out


def _read_cached(cache_path, t0, thinking=DEFAULT_THINKING, rec=None, key=""):
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    key = key or os.path.basename(cache_path)[:-5]
    if not os.path.exists(cache_path):
        rec.event("cache.miss", key=key, path=cache_path, reason="missing")
        return None
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cached = json.load(fh)
    except (OSError, ValueError):
        rec.event("cache.miss", key=key, path=cache_path, reason="unreadable")
        return None
    if not isinstance(cached, dict) or not cached.get("ok"):
        rec.event("cache.miss", key=key, path=cache_path, reason="invalid")
        return None
    cached = dict(cached)
    cached.setdefault("mode", "single")
    cached.setdefault("sources", [])
    original_ms = cached.get("ms")
    cached["thinking"] = thinking
    cached["cached"] = True
    cached["ms"] = _ms(t0)
    try:
        age = round(time.time() - os.path.getmtime(cache_path), 1)
    except OSError:
        age = None
    rec.event(
        "cache.hit",
        key=key,
        path=cache_path,
        age=age,
        cached_ms=original_ms,
        read_ms=cached["ms"],
        thinking=thinking,
        origin_run=runlog.find_by_cache_key(key),
    )
    rec.set_index(cache_hit=True, cache_key=key, cached_ms=original_ms)
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
    return "\n".join(lines[a - 1 : b])


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
    chapter_rows = [
        {
            "path": h["path"],
            "line": int(h.get("line") or 1),
            "score": float(h.get("score") or 0),
            "coverage": h.get("coverage"),
            "title": _title_for(titles, h["path"]),
        }
        for h in chapters
    ]

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
        def_hits.append({"path": h["path"], "line": ln, "score": h.get("score"), "coverage": cov, "group_score": gs})
        if len(def_hits) >= DEFPASS_TOP:
            break

    picked, used = [current], {(path, int(line))}
    for item in def_hits + [
        {"path": h["path"], "line": int(h.get("line") or 1), "score": h.get("score"), "coverage": h.get("coverage")}
        for h in chapters
    ]:
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
    text = "\n".join(lines[a - 1 : b])
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


def build_windows(chosen, titles, status, total_chars=TOTAL_CHARS, window_chars=WINDOW_CHARS, max_windows=MAX_WINDOWS):
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
        packet = book_expand.make_packet(
            book, path, title, lines, p["line"], 2, window_chars, status.get(book, "missing")
        )
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
        start, end, text = _window_text(
            cache[item["path"]], prefix[item["path"]], item["start"], item["end"], item["mid"], cap
        )
        if not text.strip():
            continue
        book, title = meta[item["path"]]
        windows.append(
            {
                "path": item["path"],
                "book": book,
                "title": title,
                "start": start,
                "end": end,
                "chars": len(text),
                "text": text,
            }
        )
        used += len(text)
    return windows


def build_prompt(question, book, windows, chapters, history="", rec=None):
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    inputs = {
        "builder": "single",
        "question": question,
        "book": book,
        "windows": [{k: w[k] for k in ("path", "book", "title", "start", "end", "chars", "text")} for w in windows],
        "chapters": chapters,
        "history": history,
    }
    rec.event("prompt.inputs", **rec.large(json.dumps(inputs, ensure_ascii=False), "inputs"))
    blocks = ["%s:%d-%d\n%s" % (w["path"], w["start"], w["end"], w["text"]) for w in windows]
    kept = ["%s:%d-%d" % (w["path"], w["start"], w["end"]) for w in windows]
    rows = []
    for i, c in enumerate(chapters, 1):
        cov = "-" if c.get("coverage") is None else "%.2f" % float(c["coverage"])
        rows.append("%d. %s:%d %s score=%.2f cov=%s" % (i, c["path"], c["line"], _quoted(c["title"]), c["score"], cov))
    history = (history.strip() + "\n") if history.strip() else ""
    limit = PROMPT_MAX + (HISTORY_CHARS_SINGLE if history else 0)

    def render(blocks):
        return PROMPT.format(
            question=question,
            book=book,
            md="books/%s/md" % book,
            history=history,
            n=len(rows),
            chapters="\n".join(rows),
            evidence="\n\n".join(blocks),
        )

    prompt = render(blocks)
    dropped = []
    while len(prompt) > limit and len(blocks) > 1:
        gone = blocks.pop()
        ref = gone.split("\n", 1)[0]
        dropped.append(ref)
        rec.event("prompt.drop", builder="single", block=ref, reason="budget", limit=limit, remaining=len(blocks))
        prompt = render(blocks)
    rec.event(
        "prompt.built",
        builder="single",
        chars=len(prompt),
        limit=limit,
        windows=len(windows),
        kept=len(blocks),
        dropped=len(dropped),
        history_chars=len(history),
        chapters=len(rows),
        truncated=bool(dropped),
        **rec.large(prompt, "prompt"),
    )
    rec.event("prompt.windows", builder="single", kept=kept[: len(blocks)], dropped=dropped)
    inputs_info = rec.blob(json.dumps(inputs, ensure_ascii=False))
    rec.event(
        "prompt",
        builder="single",
        version=PROMPT_VERSION,
        chars=len(prompt),
        sha256=runlog.sha256(prompt),
        inputs_blob=inputs_info,
        kept=len(blocks),
        dropped=len(dropped),
    )
    rec.set_index(prompt_chars=len(prompt), prompt_windows=len(blocks), prompt_dropped=len(dropped))
    return prompt


def _book_label(source):
    return source.get("title") or source["book"].rsplit("/", 1)[-1]


def _quoted(label):
    """书名本身已经带《》时不再套一层。"""
    text = str(label or "")
    return text if text.startswith("《") else "《%s》" % text


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
            count = con.execute("SELECT count(*) FROM para_fts WHERE para_fts MATCH ?", (expr,)).fetchone()[0]
            nbooks = con.execute(
                "SELECT count(DISTINCT p.book) FROM para_fts f JOIN para p ON p.id=f.rowid WHERE para_fts MATCH ?",
                (expr,),
            ).fetchone()[0]
            rows = con.execute(
                "SELECT p.book, count(*) c FROM para_fts f JOIN para p ON p.id=f.rowid "
                "WHERE para_fts MATCH ? GROUP BY p.book ORDER BY c DESC LIMIT 2",
                (expr,),
            ).fetchall()
            stats.append({"term": term, "count": count, "nbooks": nbooks, "books": [(b, c) for b, c in rows]})
    except sqlite3.Error:
        return ""
    finally:
        con.close()
    if not stats:
        return ""
    picked = [s for s in stats if s["count"] <= 200][:3]
    if not picked:
        picked = [min(stats, key=lambda s: s["count"])]

    # 书名：build/library.db 的 book 表按 uid 取 title，查不到用 uid
    titles = {}
    try:
        bcon = sqlite3.connect("file:%s?mode=ro" % LIBRARY_DB, uri=True)
        try:
            for s in picked:
                for b, _c in s["books"]:
                    if b not in titles:
                        row = bcon.execute("SELECT title FROM book WHERE path = ?", (b,)).fetchone()
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
            book_part = _quoted(label(books[0][0]))
        elif len(books) >= 2:
            book_part = "%s%d，%s%d" % (
                _quoted(label(books[0][0])),
                books[0][1],
                _quoted(label(books[1][0])),
                books[1][1],
            )
        else:
            book_part = ""
        lines.append(
            "- 「%s」%d 段 / %d 本%s" % (s["term"], s["count"], s["nbooks"], ("：" + book_part) if book_part else "")
        )
    return "\n".join(lines)


def multi_windows(plans, focus_book=None):
    """点名书最多拿一半预算，其他书平分剩下的；每本最多两窗。

    非点名书没有命中分、或最高分低于 MULTI_MIN_SCORE，就不给它窗口，由提示词点名它
    没有相关原文。点名书不受这条限制，窗口照给。
    """
    n = len(plans)
    if not n:
        return []
    books = [p["source"]["book"] for p in plans]
    if focus_book not in books:
        focus_book = None
    if focus_book:
        focus_quota = int(TOTAL_CHARS * MULTI_FOCUS_SHARE)
        rest = TOTAL_CHARS - focus_quota
        other_quota = rest // max(1, n - 1)
    else:
        focus_quota = 0
        other_quota = TOTAL_CHARS // n
    per_book = []
    for plan in plans:
        book = plan["source"]["book"]
        quota = focus_quota if book == focus_book else other_quota
        quota = max(MULTI_QUOTA_MIN, quota)
        scored = [c.get("score") for c in plan["chosen"] if c.get("score") is not None]
        best = max(scored) if scored else None
        if book != focus_book and (best is None or best < MULTI_MIN_SCORE):
            per_book.append([])
            continue
        per_book.append(
            build_windows(
                plan["chosen"],
                {book: plan["titles"]},
                {book: plan["status"]},
                total_chars=quota,
                window_chars=max(200, quota // MULTI_WINDOWS_PER_BOOK),
                max_windows=MULTI_WINDOWS_PER_BOOK,
            )
        )
    if not any(per_book):
        # 全都没有命中分时至少各给一窗，别把整次回答变成空。
        for plan in plans:
            book = plan["source"]["book"]
            quota = max(400, focus_quota if book == focus_book else other_quota)
            per_book.append(
                build_windows(
                    plan["chosen"],
                    {book: plan["titles"]},
                    {book: plan["status"]},
                    total_chars=quota,
                    window_chars=max(200, quota),
                    max_windows=1,
                )
            )
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


def build_multi_prompt(question, plans, windows, coverage="", history="", focus_title="", rec=None):
    """一次提示词装下所有书：库级覆盖 + 书名行 + 每书 4 章地图 + 按书分组的窗口。"""
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    inputs = {
        "builder": "multi",
        "question": question,
        "plans": [
            {
                "book": p["source"]["book"],
                "source": p["source"],
                "chosen": p["chosen"],
                "chapters": p["chapters"],
            }
            for p in plans
        ],
        "windows": [{k: w[k] for k in ("book", "path", "start", "end", "chars", "text")} for w in windows],
        "coverage": coverage,
        "history": history,
        "focus_title": focus_title,
    }
    rec.event("prompt.inputs", **rec.large(json.dumps(inputs, ensure_ascii=False), "inputs"))
    rank = {p["source"]["book"]: i for i, p in enumerate(plans)}
    labels = {p["source"]["book"]: _book_label(p["source"]) for p in plans}
    coverage = coverage.strip()
    if coverage:
        coverage += "\n\n"
    focus = ""
    if focus_title and focus_title in labels.values():
        focus = "用户点名的是%s，优先用它回答；其他书只补它没有的角度。\n\n" % _quoted(focus_title)
    elif focus_title:
        focus = "用户点名的是%s，但这本书没有可用原文：开头用一句话说明，再从其他书回答。\n\n" % _quoted(focus_title)
    history = (history.strip() + "\n") if history.strip() else ""
    limit = PROMPT_MAX_MULTI + (HISTORY_CHARS_MULTI if history else 0)
    keep = list(windows)
    dropped = []

    def finish(prompt, truncated):
        rec.event(
            "prompt.built",
            builder="multi",
            chars=len(prompt),
            limit=limit,
            windows=len(keep),
            original_windows=len(windows),
            books=len(plans),
            history_chars=len(history),
            coverage_chars=len(coverage),
            focus=bool(focus),
            truncated=truncated,
            **rec.large(prompt, "prompt"),
        )
        rec.event(
            "prompt.windows",
            builder="multi",
            kept=[{"book": w["book"], "path": w["path"], "start": w["start"], "end": w["end"]} for w in keep],
            dropped=dropped,
        )
        inputs_info = rec.blob(json.dumps(inputs, ensure_ascii=False))
        rec.event(
            "prompt",
            builder="multi",
            version=PROMPT_VERSION_MULTI,
            chars=len(prompt),
            sha256=runlog.sha256(prompt),
            inputs_blob=inputs_info,
            kept=len(keep),
            dropped=len(dropped),
        )
        rec.set_index(prompt_chars=len(prompt), prompt_windows=len(keep), prompt_dropped=len(dropped))
        return prompt

    while True:
        book_rows, chapter_blocks = [], []
        for plan in plans:
            book = plan["source"]["book"]
            md = "books/%s/md" % book
            book_rows.append("- %s book=%s md=%s" % (_quoted(labels[book]), book, md))
            rows = ["%s md=%s" % (_quoted(labels[book]), md)]
            for i, c in enumerate(plan["chapters"], 1):
                cov = "-" if c.get("coverage") is None else "%.2f" % float(c["coverage"])
                rows.append(
                    "%d. %s:%d %s score=%.2f cov=%s" % (i, c["path"], c["line"], _quoted(c["title"]), c["score"], cov)
                )
            chapter_blocks.append("\n".join(rows))
        blocks = [
            "%s %s:%d-%d\n%s" % (_quoted(labels.get(w["book"], "")), w["path"], w["start"], w["end"], w["text"])
            for w in keep
        ]
        empty = [labels[p["source"]["book"]] for p in plans if not any(w["book"] == p["source"]["book"] for w in keep)]
        nowindow = ""
        if empty:
            nowindow = "无相关原文窗口的书：%s（这些书在回答里要写明没有相关原文）" % "、".join(
                _quoted(x) for x in empty
            )
        prompt = MULTI_PROMPT.format(
            question=question,
            coverage=coverage,
            focus=focus,
            n=len(plans),
            history=history,
            books="\n".join(book_rows),
            chapters="\n\n".join(chapter_blocks),
            evidence="\n\n".join(blocks),
            nowindow=nowindow,
        )
        if len(prompt) <= limit:
            return finish(prompt, bool(dropped))
        before = list(keep)
        if not _drop_last_window(keep, rank):
            return finish(prompt, True)
        removed = [w for w in before if w not in keep]
        dropped.extend({"book": w["book"], "path": w["path"], "start": w["start"], "end": w["end"]} for w in removed)
        rec.event(
            "prompt.multi_drop",
            removed=[{"book": w["book"], "path": w["path"], "start": w["start"], "end": w["end"]} for w in removed],
            before=len(before),
            after=len(keep),
            limit=limit,
        )


# ---- 调模型 ---------------------------------------------------------


def call_pi(prompt, book, books=None, thinking=DEFAULT_THINKING, rec=None, kind="answer"):
    """返回 (raw_text, error)。失败时 raw_text 为 None。"""
    roots = ["books/%s/md" % b for b in (books or [book])]
    env = dict(os.environ)
    env["BOOK_MD"] = roots[0]
    env["BOOK_MDS"] = json.dumps(roots, ensure_ascii=False)
    thinking = normalize_thinking(thinking)
    result = model_call.invoke(
        prompt,
        pi_json_cmd(thinking),
        pi_cmd(thinking),
        kind=kind,
        rec=rec,
        timeout=PI_TIMEOUT,
        cwd=ROOT,
        env=env,
        parser="answer",
    )
    if result.timed_out:
        return None, "回答超时（%d 秒）" % PI_TIMEOUT
    if result.exit_code not in (0, None):
        tail = " ".join((result.stderr or "").split())[:240]
        return None, "模型调用失败（rc=%d）%s" % (result.exit_code, ("：" + tail) if tail else "")
    if not (result.text or "").strip():
        return None, "模型没有输出"
    return result.text, ""


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
                        obj = json.loads(text[start : i + 1])
                    except ValueError:
                        obj = None
                    if isinstance(obj, dict) and any(
                        k in obj for k in ("answer", "lead", "sections", "citations", "confidence", "not_found")
                    ):
                        return obj
                    break
        start = text.find("{", start + 1)
    return None


def _request_reply(prompt, book, books=None, thinking=DEFAULT_THINKING, accept=None, rec=None):
    """调模型并解析，最多两次。返回 (reply, raw, error)，reply 只在成功时非 None。

    解析不出 JSON 换 JSON_REMIND 再问，解析出来但结构不合格换 ANSWER_REMIND。
    raw 为 None 是调不起 pi（超时、传输失败），重问只会再等一遍，直接返回。
    """
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    raw, error, reply = None, "", None
    ask = prompt
    for _attempt in range(2):
        rec.event(
            "answer.attempt",
            attempt=_attempt + 1,
            chars=len(ask),
            sha256=runlog.sha256(ask),
            **rec.large(ask, "prompt"),
        )
        raw, error = call_pi(
            ask, book, books, thinking=thinking, rec=rec, kind="answer" if _attempt == 0 else "answer_retry"
        )
        if raw is None:
            rec.event("answer.attempt_end", attempt=_attempt + 1, ok=False, error=error)
            return None, None, error
        rec.event(
            "answer.raw",
            attempt=_attempt + 1,
            chars=len(raw),
            sha256=runlog.sha256(raw),
            **rec.large(raw, "raw"),
        )
        reply = parse_reply(raw)
        if reply is not None and (accept is None or accept(reply)):
            rec.event("answer.attempt_end", attempt=_attempt + 1, ok=True, parsed=True, accepted=True)
            return reply, raw, ""
        error = "模型没有返回 JSON" if reply is None else "模型没有给出答案结构"
        rec.event(
            "answer.parse",
            attempt=_attempt + 1,
            ok=False,
            parsed=reply is not None,
            error=error,
            raw_chars=len(raw),
            raw_sha256=runlog.sha256(raw),
        )
        # server.log 里要能看出这次答案多等了一轮，以及坏在哪
        print(
            "回答重问（%s）：%s" % ("解析失败" if reply is None else "没有正文", " ".join(raw.split())[:120]),
            file=sys.stderr,
            flush=True,
        )
        reminder = JSON_REMIND if reply is None else ANSWER_REMIND
        rec.event(
            "answer.reminder", attempt=_attempt + 1, reminder="json" if reply is None else "answer", chars=len(reminder)
        )
        ask = prompt + reminder
    rec.event("answer.attempt_end", attempt=2, ok=False, error=error)
    return None, raw, error


def _split_answer(text):
    """把带「## 小标题」的 answer 拆成 lead 与 sections。

    第一个小标题之前是 lead。模型若还写「## 分歧」「## 缺口」，把正文并回上一节，
    不单独留两段；没有上一节就并进 lead。空块丢掉，最多 6 节。
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
    sections = []
    for heading, lines in blocks:
        body = "\n".join(lines).strip()
        if not body:
            continue
        if len(heading) <= 6 and re.match(r"^(分歧|不同|差异|缺口|不足|欠缺|未展开)", heading):
            if sections:
                sections[-1]["body"] = (sections[-1]["body"] + "\n\n" + body).strip()
            else:
                lead = (lead + "\n\n" + body).strip()
            continue
        if len(sections) < 6:
            sections.append({"heading": heading, "body": body})
    return lead, sections, "", ""


def _clip_answer(text, limit=MAX_ANSWER_CHARS):
    """硬上限兜底：优先在句末截断，截不动再按字符截。"""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    for sep in ("。", "！", "？", "\n"):
        i = head.rfind(sep)
        if i >= (limit - 1) * 0.6:
            return head[: i + 1].rstrip() + "…"
    return head.rstrip() + "…"


def _recap(reply, lead, sections):
    """给下一问留三条定位要点；模型没给就从领句和标题凑。"""
    items = []
    raw = reply.get("recap")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                items.append(" ".join(item.split())[:60])
    if not items:
        if lead:
            first = re.split(r"(?<=[。！？])", lead.strip(), maxsplit=1)[0]
            if first:
                items.append(first[:60])
        items.extend(s["heading"][:60] for s in sections)
    out = []
    for item in items:
        if item in out:
            continue
        out.append(item)
        if len(out) == 3:
            break
    return out


def _shape_answer(reply, rec=None):
    """单书与多书共用：优先把 answer 里的「## 小标题」拆成结构，旧字段仍然接受。"""
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
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
                if not body:
                    continue
                sections.append({"heading": heading, "body": body})
                if len(sections) >= 6:
                    break

    def render(lead, sections):
        parts = [lead]
        for s in sections:
            parts.append((s["heading"] + "\n" + s["body"]) if s["heading"] else s["body"])
        return "\n\n".join(p for p in parts if p)

    raw_answer = render(lead, sections)
    answer = _clip_answer(raw_answer)
    if len(answer) != len(raw_answer):
        # 渲染的是 lead + sections，截断后要重新拆，否则前端仍按未截断的块渲染。
        clipped_lead, clipped_sections, _, _ = _split_answer(answer)
        if clipped_lead or clipped_sections:
            lead, sections = clipped_lead, clipped_sections
        else:
            lead, sections = answer, []
    if not answer and text:
        answer = _clip_answer(text)
    rec.event(
        "answer.shape",
        raw_chars=len(raw_answer),
        final_chars=len(answer),
        truncated=len(answer) != len(raw_answer),
        sections=len(sections),
        lead_chars=len(lead),
        dissent_chars=len(dissent),
        gap_chars=len(gap),
        text_chars=len(text),
    )
    if len(answer) != len(raw_answer):
        rec.event("answer.truncate", limit=MAX_ANSWER_CHARS, raw_chars=len(raw_answer), final_chars=len(answer))
    return lead, sections, dissent, gap, answer


# ---- 回查引文 -------------------------------------------------------


class _ChapterIndex:
    """按去空白文本回查：先逐行，再整章（跨行引文），行号以文件为准。"""

    def __init__(self, rel):
        self.rel = rel
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
            self.lines = fh.read().splitlines()
        self.norm_lines = [_norm(raw_line) for raw_line in self.lines]
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


def verify_citations(cites, windows, hit_path="", hit_line=0, sources=None, rec=None):
    """只留回查通过的引文；行号用文件实际行号，不信模型报的。

    单书：回查后再按「离命中行近的排前面」排序并截到 MAX_CITATIONS 条，固定的第一段就是命中段，
    邻近的引文最能支撑它。多书：path 必须落在本次书单内，按书轮取，最多 MAX_CITATIONS 条。
    """
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    if sources:
        return _verify_multi(cites, windows, sources, rec=rec)
    evidence = [w["path"] for w in windows]
    basenames = {}
    for p in evidence:
        basenames.setdefault(os.path.basename(p), []).append(p)
    indexes = {}
    out, seen, dropped = [], set(), 0
    total = len(cites or [])

    def fail(reason, **fields):
        nonlocal dropped
        dropped += 1
        rec.event("answer.citation", ok=False, reason=reason, **fields)

    for item in cites or []:
        if not isinstance(item, dict):
            fail("not_dict")
            continue
        quote = item.get("quote")
        if not isinstance(quote, str):
            fail("quote_not_str", path=item.get("path", ""), claimed_line=item.get("line"))
            continue
        needle = _norm(quote)[:QUOTE_PROBE]
        if len(needle) < 4:
            fail(
                "quote_short", quote=runlog.short(quote, 200), path=item.get("path", ""), claimed_line=item.get("line")
            )
            continue
        rel = _rel(item.get("path"))
        if not _inside_library(rel):
            same = basenames.get(os.path.basename(rel or ""), [])
            rel = same[0] if len(same) == 1 else ""
        if not rel or not _inside_library(rel):
            fail(
                "path_invalid", quote=runlog.short(quote, 200), path=item.get("path", ""), claimed_line=item.get("line")
            )
            continue
        line = _locate_in(rel, needle, indexes)
        if not line:
            fail("not_found", quote=runlog.short(quote, 200), path=rel, claimed_line=item.get("line"))
            continue
        key = (rel, line)
        if key in seen:
            rec.event(
                "answer.citation", ok=False, reason="duplicate", path=rel, line=line, claimed_line=item.get("line")
            )
            continue
        seen.add(key)
        title = next((w["title"] for w in windows if w["path"] == rel), "")
        rec.event(
            "answer.citation",
            ok=True,
            path=rel,
            line=line,
            claimed_line=item.get("line"),
            corrected=item.get("line") != line,
            quote=runlog.short(quote.strip(), 200),
            chapter=title,
        )
        out.append({"path": rel, "line": line, "quote": quote.strip(), "chapter": title, "verified": True})
    out.sort(key=lambda c: (c["path"] != hit_path, abs(c["line"] - hit_line)))
    kept = out[:MAX_CITATIONS]
    rec.event(
        "answer.citations",
        mode="single",
        total=total,
        kept=len(kept),
        dropped=dropped,
        books=len({book_from_path(c["path"]) for c in kept if book_from_path(c["path"])}),
        hit_path=hit_path,
        hit_line=hit_line,
    )
    return kept, dropped


def _verify_multi(cites, windows, sources, rec=None):
    """多书回查：不许跨书同名文件兑底，书只能来自本次书单。"""
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    scope = {s["book"]: s for s in sources}
    chapter_of = {w["path"]: w["title"] for w in windows}
    indexes, seen, dropped = {}, set(), 0
    by_book = {}
    out = []
    total = len(cites or [])

    def fail(reason, **fields):
        nonlocal dropped
        dropped += 1
        rec.event("answer.citation", ok=False, reason=reason, **fields)

    def summary():
        rec.event(
            "answer.citations",
            mode="multi",
            total=total,
            kept=len(out),
            dropped=dropped,
            books=len(by_book),
            sources=len(sources),
        )

    for item in cites or []:
        if not isinstance(item, dict):
            fail("not_dict")
            continue
        quote = item.get("quote")
        if not isinstance(quote, str):
            fail("quote_not_str", path=item.get("path", ""), claimed_line=item.get("line"))
            continue
        needle = _norm(quote)[:QUOTE_PROBE]
        if len(needle) < 4:
            fail(
                "quote_short", quote=runlog.short(quote, 200), path=item.get("path", ""), claimed_line=item.get("line")
            )
            continue
        rel = _rel(item.get("path"))
        if not _inside_library(rel):
            fail(
                "path_invalid", quote=runlog.short(quote, 200), path=item.get("path", ""), claimed_line=item.get("line")
            )
            continue
        book = book_from_path(rel)
        if book not in scope:
            fail(
                "book_not_in_scope", quote=runlog.short(quote, 200), path=rel, book=book, claimed_line=item.get("line")
            )
            continue
        line = _locate_in(rel, needle, indexes)
        if not line:
            fail("not_found", quote=runlog.short(quote, 200), path=rel, book=book, claimed_line=item.get("line"))
            continue
        key = (rel, line)
        if key in seen:
            rec.event(
                "answer.citation",
                ok=False,
                reason="duplicate",
                path=rel,
                line=line,
                book=book,
                claimed_line=item.get("line"),
            )
            continue
        seen.add(key)
        rec.event(
            "answer.citation",
            ok=True,
            path=rel,
            line=line,
            claimed_line=item.get("line"),
            corrected=item.get("line") != line,
            quote=runlog.short(quote.strip(), 200),
            book=book,
            chapter=chapter_of.get(rel, ""),
        )
        by_book.setdefault(book, []).append(
            {
                "path": rel,
                "line": line,
                "quote": quote.strip(),
                "chapter": chapter_of.get(rel, ""),
                "verified": True,
                "book": book,
                "book_title": _book_label(scope[book]),
            }
        )
    for book, items in by_book.items():
        seed = int(scope[book].get("line") or 1)
        items.sort(key=lambda c: abs(c["line"] - seed))
    for round_i in range(MAX_CITATIONS_MULTI):
        added = False
        for src in sources:
            items = by_book.get(src["book"]) or []
            if round_i < len(items):
                out.append(items[round_i])
                added = True
                if len(out) >= MAX_CITATIONS_MULTI:
                    summary()
                    return out, dropped
        if not added:
            break
    summary()
    return out, dropped


# ---- 主流程 ---------------------------------------------------------


def answer_question(
    question,
    book,
    path,
    line=1,
    terms=None,
    groups=None,
    use_cache=True,
    sources=None,
    thinking=DEFAULT_THINKING,
    history="",
    focus=None,
    rec=None,
):
    """返回 Answer dict（结构见 ANSWER-BRIEF.md 的「数据形状」）。

    use_cache=False 即前端「重新检索」：跳过答案缓存并重新调模型，本次结果照样回写。
    history 是上一轮的上下文片段，非空时进提示词也进缓存键。
    focus 是这一问点名的书（status=exact 才算点名）：一本书走单书写手，多本就把它排在最前、
    其余书按检索顺序补角度；不再拆成主答/补充两次调用。
    """
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    t0 = time.time()
    question = (question or "").strip()
    if not question:
        out = _err("question 为空", ms=_ms(t0))
        rec.event("answer.error", stage="request", error=out.get("error"))
        rec.set_index(answer_ok=False, answer_error=out.get("error"))
        return out
    thinking = normalize_thinking(thinking)
    history = (history or "").strip()
    terms = [str(t).strip() for t in (terms or []) if str(t).strip()][:12]
    groups = [g for g in (groups or []) if isinstance(g, dict) and g.get("variants")]
    rec.event(
        "answer.request",
        question=question,
        book=book,
        path=path,
        line=line,
        terms=terms,
        groups=len(groups),
        use_cache=bool(use_cache),
        thinking=thinking,
        history_chars=len(history),
        focus=focus,
        sources=len(sources or []),
    )
    scope_report = {}
    scope = _normalize_sources(sources, report=scope_report)
    rec.event("answer.sources", **scope_report)
    rec.set_index(
        sources=scope_report.get("kept", 0), sources_dropped=scope_report.get("total", 0) - scope_report.get("kept", 0)
    )
    named = _normalize_focus(focus)
    if named:
        rec.event("answer.focus", **named)
    if named and named["status"] == "exact" and named["book"]:
        hit = _focus_source(named, scope, book, path, line)
        note = _focus_note(named, hit)
        rest = [s for s in scope if s["book"] != named["book"]]
        rec.event("answer.route", mode="focus", book=named["book"], hit=bool(hit), rest=len(rest))
        if hit is None:
            # 点名的书没有原文时，开头一句话说明，再从其他书回答；一本都没有才报错。
            if rest:
                out = _answer_multi(
                    question,
                    rest,
                    terms,
                    groups,
                    use_cache,
                    t0,
                    thinking,
                    history,
                    focus_title=note["title"],
                    rec=rec,
                )
                out["focus"] = note
                rec.event("answer.focus_result", **note)
                return out
            out = _err(
                "点名的书没有可用原文（locked 或没有读到 md 路径），也没有其他书可答",
                ms=_ms(t0),
                thinking=thinking,
                focus=note,
            )
            rec.event("answer.error", stage="focus", error=out.get("error"), **note)
            rec.set_index(answer_ok=False, answer_error=out.get("error"))
            return out
        if not rest:
            out = _answer_single(
                question,
                hit["book"],
                hit["path"],
                hit["line"],
                terms,
                groups,
                use_cache,
                t0,
                thinking,
                history,
                rec=rec,
            )
        else:
            out = _answer_multi(
                question,
                [hit, *rest],
                terms,
                groups,
                use_cache,
                t0,
                thinking,
                history,
                focus_title=note["title"],
                focus_book=hit["book"],
                rec=rec,
            )
        out["focus"] = note
        rec.event("answer.focus_result", **note)
        return out
    if len(scope) >= 2:
        rec.event("answer.route", mode="multi", sources=len(scope))
        return _answer_multi(question, scope, terms, groups, use_cache, t0, thinking, history, rec=rec)
    rec.event("answer.route", mode="single", sources=len(scope))
    return _answer_single(question, book, path, line, terms, groups, use_cache, t0, thinking, history, rec=rec)


def _answer_single(
    question,
    book,
    path,
    line,
    terms,
    groups,
    use_cache,
    t0,
    thinking=DEFAULT_THINKING,
    history="",
    version=None,
    rec=None,
):
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    rel = _rel(path)
    if not _inside_library(rel):
        out = _err("path 不在 books/ 下或不是 .md", ms=_ms(t0), thinking=thinking)
        rec.event("answer.error", mode="single", stage="path", error=out.get("error"), path=path)
        rec.set_index(answer_ok=False, answer_error=out.get("error"))
        return out
    try:
        line = max(1, int(line or 1))
    except (TypeError, ValueError):
        line = 1
    book = (book or "").strip() or book_from_path(rel)
    first = defpass.first_content_line(os.path.join(ROOT, rel))
    if line < first:
        line = first

    key = cache_key(question, book, rel, line, terms, groups, thinking, history, version)
    cache_path = os.path.join(CACHE_DIR, key + ".json")
    rec.event(
        "answer.cache_key",
        mode="single",
        key=key,
        path=cache_path,
        version=version or PROMPT_VERSION,
        book=book,
        line=line,
        history_chars=len(history),
        thinking=thinking,
        use_cache=bool(use_cache),
    )
    if use_cache:
        cached = _read_cached(cache_path, t0, thinking, rec=rec, key=key)
        if cached:
            stored = rec.large(json.dumps(cached, ensure_ascii=False), "result")
            rec.event(
                "answer.result",
                mode=cached.get("mode", "single"),
                ok=cached.get("ok"),
                cached=True,
                error=cached.get("error", ""),
                confidence=cached.get("confidence"),
                citations=len(cached.get("citations") or []),
                passages=len(cached.get("passages") or []),
                chars=len(cached.get("answer") or ""),
                ms=cached.get("ms"),
                **stored,
            )
            rec.set_index(
                answer_ok=bool(cached.get("ok")),
                answer_error=cached.get("error", ""),
                cache_hit=True,
                cache_key=key,
                confidence=cached.get("confidence"),
                mode=cached.get("mode", "single"),
            )
            return cached

    titles, status = _chapter_titles(book)
    con = sqlite3.connect("file:%s?mode=ro" % INDEX_DB, uri=True)
    try:
        chosen, chapters = pick_passages(con, book, rel, line, terms, groups)
    finally:
        con.close()
    for item in chosen:
        item["book"] = book
    rec.event(
        "answer.pick",
        mode="single",
        book=book,
        path=rel,
        line=line,
        chosen=len(chosen),
        chapters=len(chapters),
        picked=[{"path": item.get("path"), "line": item.get("line"), "score": item.get("score")} for item in chosen],
        chapter_rows=[
            {"path": c.get("path"), "line": c.get("line"), "score": c.get("score"), "coverage": c.get("coverage")}
            for c in chapters
        ],
    )
    windows = build_windows(chosen, {book: titles}, {book: status})
    rec.event(
        "answer.windows",
        mode="single",
        count=len(windows),
        chars=sum(w["chars"] for w in windows),
        windows=[
            {"book": w["book"], "path": w["path"], "start": w["start"], "end": w["end"], "chars": w["chars"]}
            for w in windows
        ],
    )
    rec.set_index(windows=len(windows))
    if not windows:
        out = _err("没有取到原文窗口", ms=_ms(t0), thinking=thinking)
        rec.event("answer.error", mode="single", stage="windows", error=out.get("error"))
        rec.set_index(answer_ok=False, answer_error=out.get("error"))
        return out

    prompt = build_prompt(question, book, windows, chapters, history, rec=rec)
    reply, raw, error = _request_reply(prompt, book, thinking=thinking, rec=rec)
    if raw is None:
        out = _err(error, passages=_passages(windows), ms=_ms(t0), thinking=thinking)
        rec.event("answer.error", mode="single", stage="model", error=out.get("error"))
        rec.set_index(answer_ok=False, answer_error=out.get("error"))
        return out
    if reply is None:
        head = " ".join((raw or "").split())[:200]
        out = _err("%s：%s" % (error, head), passages=_passages(windows), ms=_ms(t0), thinking=thinking)
        rec.event("answer.error", mode="single", stage="parse", error=out.get("error"))
        rec.set_index(answer_ok=False, answer_error=out.get("error"))
        return out

    lead, sections, dissent, gap, answer = _shape_answer(reply, rec=rec)
    not_found = bool(reply.get("not_found")) or not answer
    citations, dropped = verify_citations(reply.get("citations"), windows, rel, line, rec=rec)
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

    out = _ok(
        answer=answer,
        citations=citations,
        passages=_passages(windows),
        confidence=confidence,
        not_found=not_found,
        error=error,
        ms=_ms(t0),
        lead=lead,
        sections=sections,
        dissent=dissent,
        gap=gap,
        recap=_recap(reply, lead, sections),
        thinking=thinking,
    )
    stored = rec.large(json.dumps(out, ensure_ascii=False), "result")
    rec.event(
        "answer.result",
        mode="single",
        ok=out.get("ok"),
        error=error,
        confidence=confidence,
        not_found=not_found,
        citations=len(citations),
        dropped=dropped,
        passages=len(out.get("passages") or []),
        chars=len(answer),
        ms=out.get("ms"),
        **stored,
    )
    rec.set_index(
        answer_ok=bool(out.get("ok")),
        answer_error=error,
        confidence=confidence,
        not_found=not_found,
        citations=len(citations),
        passages=len(out.get("passages") or []),
        mode="single",
        cache_key=key,
    )
    try:
        _write_json(cache_path, out)
    except OSError as exc:
        rec.event("cache.write_error", key=key, path=cache_path, error=exc.__class__.__name__)
    else:
        try:
            size = os.path.getsize(cache_path)
        except OSError:
            size = None
        rec.event("cache.write", key=key, path=cache_path, bytes=size)
        rec.set_index(cache_written=True)
    return out


def _answer_multi(
    question,
    sources,
    terms,
    groups,
    use_cache,
    t0,
    thinking=DEFAULT_THINKING,
    history="",
    version=None,
    focus_title="",
    focus_book=None,
    rec=None,
):
    """多书：每本取两窗，一次提示词写完整篇，引文按书归并。"""
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    key = multi_cache_key(question, sources, terms, groups, thinking, history, version, focus_title, focus_book or "")
    cache_path = os.path.join(CACHE_DIR, key + ".json")
    rec.event(
        "answer.cache_key",
        mode="multi",
        key=key,
        path=cache_path,
        version=version or PROMPT_VERSION_MULTI,
        books=[s["book"] for s in sources],
        history_chars=len(history),
        thinking=thinking,
        use_cache=bool(use_cache),
        focus_title=focus_title,
        focus_book=focus_book or "",
    )
    if use_cache:
        cached = _read_cached(cache_path, t0, thinking, rec=rec, key=key)
        if cached:
            stored = rec.large(json.dumps(cached, ensure_ascii=False), "result")
            rec.event(
                "answer.result",
                mode=cached.get("mode", "multi"),
                ok=cached.get("ok"),
                cached=True,
                error=cached.get("error", ""),
                confidence=cached.get("confidence"),
                citations=len(cached.get("citations") or []),
                passages=len(cached.get("passages") or []),
                sources=len(cached.get("sources") or []),
                chars=len(cached.get("answer") or ""),
                ms=cached.get("ms"),
                **stored,
            )
            rec.set_index(
                answer_ok=bool(cached.get("ok")),
                answer_error=cached.get("error", ""),
                cache_hit=True,
                cache_key=key,
                confidence=cached.get("confidence"),
                mode=cached.get("mode", "multi"),
            )
            return cached

    plans = []
    con = sqlite3.connect("file:%s?mode=ro" % INDEX_DB, uri=True)
    try:
        for src in sources:
            titles, status = _chapter_titles(src["book"])
            picked, chapters = pick_passages(
                con, src["book"], src["path"], src["line"], terms, groups, MULTI_QSEARCH_LIMIT
            )
            # 命中行本身就是首个正文行（line==1）也是合法命中，照样进证据窗；
            # 以前 > 1 会把用户点中的那段丢掉
            chosen = [picked[0]] if picked else []
            for item in picked[1:]:
                if len(chosen) >= MULTI_WINDOWS_PER_BOOK:
                    break
                chosen.append(item)
            plans.append(
                {
                    "source": src,
                    "titles": titles,
                    "status": status,
                    "chosen": [dict(item, book=src["book"]) for item in chosen],
                    "chapters": chapters[:MULTI_CHAPTERS],
                }
            )
    finally:
        con.close()
    rec.event(
        "answer.plans",
        mode="multi",
        count=len(plans),
        plans=[
            {
                "book": p["source"]["book"],
                "path": p["source"]["path"],
                "line": p["source"]["line"],
                "chosen": len(p["chosen"]),
                "chapters": len(p["chapters"]),
                "status": p["status"],
                "chosen_paths": [
                    {"path": c.get("path"), "line": c.get("line"), "score": c.get("score")} for c in p["chosen"]
                ],
            }
            for p in plans
        ],
    )

    windows = multi_windows(plans, focus_book=focus_book)
    rec.event(
        "answer.windows",
        mode="multi",
        count=len(windows),
        chars=sum(w["chars"] for w in windows),
        windows=[
            {"book": w["book"], "path": w["path"], "start": w["start"], "end": w["end"], "chars": w["chars"]}
            for w in windows
        ],
    )
    rec.set_index(windows=len(windows))
    if not windows:
        out = _err("没有取到原文窗口", passages=[], ms=_ms(t0), thinking=thinking)
        rec.event("answer.error", mode="multi", stage="windows", error=out.get("error"))
        rec.set_index(answer_ok=False, answer_error=out.get("error"))
        return out
    coverage = _coverage(terms)
    prompt = build_multi_prompt(question, plans, windows, coverage, history, focus_title, rec=rec)
    # 模型给了 JSON 但没有任何正文时重问，别当成「库里没有」。
    reply, raw, error = _request_reply(
        prompt,
        sources[0]["book"],
        [s["book"] for s in sources],
        thinking,
        accept=lambda reply: bool(reply.get("not_found")) or bool(_shape_answer(reply)[4]),
        rec=rec,
    )
    if raw is None:
        out = _err(error, passages=_passages(windows), ms=_ms(t0), thinking=thinking)
        rec.event("answer.error", mode="multi", stage="model", error=out.get("error"))
        rec.set_index(answer_ok=False, answer_error=out.get("error"))
        return out
    if reply is None:
        head = " ".join((raw or "").split())[:200]
        out = _err(
            "%s：%s" % (error or "模型没有返回 JSON", head), passages=_passages(windows), ms=_ms(t0), thinking=thinking
        )
        rec.event("answer.error", mode="multi", stage="parse", error=out.get("error"))
        rec.set_index(answer_ok=False, answer_error=out.get("error"))
        return out

    lead, sections, dissent, gap, answer = _shape_answer(reply, rec=rec)
    not_found = bool(reply.get("not_found")) or not answer
    citations, dropped = verify_citations(reply.get("citations"), windows, sources=sources, rec=rec)
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
    out = _ok(
        answer=answer,
        lead=lead,
        sections=sections,
        dissent=dissent,
        gap=gap,
        citations=citations,
        passages=_passages(windows),
        confidence=confidence,
        not_found=not_found,
        error=error,
        ms=_ms(t0),
        recap=_recap(reply, lead, sections),
        thinking=thinking,
    )
    out["mode"] = "multi"
    out["sources"] = [
        {
            "book": s["book"],
            "title": _book_label(s),
            "path": s["path"],
            "line": s["line"],
            "citations": counts.get(s["book"], 0),
        }
        for s in sources
    ]
    stored = rec.large(json.dumps(out, ensure_ascii=False), "result")
    rec.event(
        "answer.result",
        mode="multi",
        ok=out.get("ok"),
        error=error,
        confidence=confidence,
        not_found=not_found,
        citations=len(citations),
        dropped=dropped,
        covered=len(covered),
        sources=len(sources),
        passages=len(out.get("passages") or []),
        chars=len(answer),
        ms=out.get("ms"),
        **stored,
    )
    rec.set_index(
        answer_ok=bool(out.get("ok")),
        answer_error=error,
        confidence=confidence,
        not_found=not_found,
        citations=len(citations),
        covered=len(covered),
        sources=len(sources),
        passages=len(out.get("passages") or []),
        mode="multi",
        cache_key=key,
    )
    try:
        _write_json(cache_path, out)
    except OSError as exc:
        rec.event("cache.write_error", key=key, path=cache_path, error=exc.__class__.__name__)
    else:
        try:
            size = os.path.getsize(cache_path)
        except OSError:
            size = None
        rec.event("cache.write", key=key, path=cache_path, bytes=size)
        rec.set_index(cache_written=True)
    return out


# ---- 点名书 --------------------------------------------------------


def _normalize_focus(focus):
    """请求里的 focus 只认 status/book/title/best_path/best_line，其余丢掉。"""
    if not isinstance(focus, dict):
        return None

    def text(key, limit=200):
        value = focus.get(key)
        return value.strip()[:limit] if isinstance(value, str) else ""

    try:
        best_line = max(1, int(focus.get("best_line") or 1))
    except (TypeError, ValueError):
        best_line = 1
    return {
        "status": text("status", 20),
        "book": text("book", 40),
        "title": text("title"),
        "best_path": text("best_path", 500),
        "best_line": best_line,
    }


def _focus_source(focus, scope, book="", path="", line=1):
    """点名书的那一条来源：先看本次范围，再看 best_path / 命中 path。"""
    hit = next((s for s in scope if s["book"] == focus["book"]), None)
    if hit:
        return hit
    tries = []
    if focus["best_path"]:
        tries.append({"path": focus["best_path"], "line": focus["best_line"], "title": focus["title"]})
    if path and str(book or "").strip() == focus["book"]:
        tries.append({"path": path, "line": line, "title": focus["title"]})
    for item in tries:
        found = _normalize_sources([item])
        # 路径落到的书必须就是点名的那本，不然宁可报错也不换书答
        if found and found[0]["book"] == focus["book"]:
            return found[0]
    return None


def _focus_note(focus, src=None):
    return {
        "book": focus["book"],
        "title": focus["title"] or (src or {}).get("title") or focus["book"],
        "status": "exact",
    }


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
    out = answer_question(
        args.question, args.book, args.path, args.line, terms, use_cache=not args.no_cache, thinking=args.thinking
    )
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
