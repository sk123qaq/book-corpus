#!/usr/bin/env python3
"""组装一条检索 trace：切词 → 词法循环 → 打分选章 → 书卡 → 原文。

只读 build/检索/index.db、build/library.db 与 books/**.md。
切词用一次 pi 调用并缓存到 缓存/terms/<问题+思考等级 sha1>.json；失败退回问句去噪切词降级。
fresh=True 跳过切词缓存，前端「重新检索」走这条路。
不跑 B4：book_cards.run 的 expand 固定为 {}。
"""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict


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
import context as demo_context
import lib_fts_cjk as cjk
import model_call
import rerank_client
import runlog
import vec_search
from thinking import DEFAULT as DEFAULT_THINKING
from thinking import normalize as normalize_thinking

_PATHS = demo_context.build_paths()
ROOT = str(_PATHS.root)

INDEX_DB = str(_PATHS.index_db)
LIB_DB = str(_PATHS.lib_db)
LIBRARY = str(_PATHS.books)
CACHE_DIR = str(_PATHS.terms_dir)

try:  # 项目配的模型与思考等级；没装包时退回写死的那一套
    from bookcorpus import pimodel

    _CFG = pimodel.load(ROOT)
    MODEL = pimodel.ref(_CFG["values"]) or "opencode-go/deepseek-v4.1-flash"
    _PI_HEAD = list(pimodel.pi_head())
    PI = [
        *_PI_HEAD,
        "-p",
        "--no-tools",
        "--no-session",
        "--no-context-files",
        "--no-skills",
        "--no-extensions",
    ]
    PI_JSON = [
        *_PI_HEAD,
        "--mode",
        "json",
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
    _PI_HEAD = ["pi"]
    PI = [*_PI_HEAD, "-p", "--no-tools", "--no-session", "--no-context-files", "--no-skills", "--no-extensions"]
    PI_JSON = [
        *_PI_HEAD,
        "--mode",
        "json",
        "-p",
        "--no-tools",
        "--no-session",
        "--no-context-files",
        "--no-skills",
        "--no-extensions",
    ]

    def pi_flags(level):
        return ["--thinking", level, "--model", MODEL]


def configure(paths):
    """按实例路径重新绑定只读库与词缓存目录。"""
    global ROOT, INDEX_DB, LIB_DB, LIBRARY, CACHE_DIR, _CFG, MODEL, DEFAULT_THINKING
    ROOT = str(paths.root)
    INDEX_DB = str(paths.index_db)
    LIB_DB = str(paths.lib_db)
    LIBRARY = str(paths.books)
    CACHE_DIR = str(paths.terms_dir)
    if pimodel is not None:
        _CFG = pimodel.load(ROOT)
        MODEL = pimodel.ref(_CFG["values"]) or MODEL
        DEFAULT_THINKING = normalize_thinking(pimodel.thinking(_CFG["values"]), DEFAULT_THINKING)


PI_TIMEOUT = 90
MAX_TERMS = 6
TERM_VERSION = "terms-v5"
STAGE_CHARS = 900
STAGE_BEFORE = 700

GENERIC_TERM_STOPWORDS = frozenset(
    {
        "介绍",
        "介绍一下",
        "介绍下",
        "详细",
        "详细介绍",
        "情况",
        "问题",
        "原因",
        "哪些",
        "什么",
        "怎么",
        "怎么样",
        "如何",
        "为什么",
        "为何",
        "是否",
        "有没有",
        "区别",
        "关系",
        "影响",
        "作用",
        "过程",
        "经过",
        "特点",
        "表现",
        "意义",
        "背景",
        "结果",
        "评价",
        "看法",
        "事情",
        "方面",
        "内容",
        "故事",
        "道理",
    }
)
PREFLIGHT_SUFFIXES = ("研究", "原因", "过程", "经过", "影响", "问题", "情况", "关系", "特点", "表现", "历史")

_META_CACHE = {"at": 0.0, "value": None}


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


# ---- 切词 -----------------------------------------------------------


def _prompt(question):
    return (
        "你是书库全文检索的切词器。把问题拆成 3 到 6 个中文“检索原子词”（terms），供中文 bigram BM25 检索使用；同时给出 3 到 5 组同义说法（groups）供后续重排。\n"
        "规则：\n"
        '- 只回 JSON，形如 {"terms":["词1","词2"],"groups":[{"name":"组名","variants":["说法1","说法2"]}]}，不要解释、不要代码块。\n'
        "- 每个 term 优先 2 个汉字；3 到 4 个汉字只用于固定术语或专名（如 王安石、开元通宝、差序格局、精神胜利法）；5 个汉字以上只允许书名、人名或历史事件原名。\n"
        "- 一个 term 只表达一个概念，严禁把“朝代/人物 + 主题”拼成长词或短语（如「宋朝金融史」「宋代货币」「交子纸币」「某人挨打」「挨打原因」）。\n"
        "- 先放锚点词（朝代/人物/书名/地点），再放主题词（制度/领域），再放具体物件或机制词（如 交子、飞钱、柜坊）；同义词要参与首轮检索必须直接放进 terms，不要只放在 groups。\n"
        "- 不要输出单字词（除非不可替代的专名），也不要输出 介绍、影响、发展、问题、关系、情况、原因、过程、哪些、怎么、为什么 等空泛词或疑问词。\n"
        "- groups 用于后续章节与段落重排：覆盖人物、事件、制度、同义近义表达；每组 2 到 6 个说法。\n"
        "示例：\n"
        '- 问「介绍下宋朝的金融史」：不要输出 ["宋朝金融史","宋代金融","交子纸币"]，应输出 {"terms":["宋朝","宋代","金融","货币","交子","纸币"],"groups":[{"name":"朝代","variants":["宋朝","宋代","北宋","南宋"]},{"name":"货币制度","variants":["交子","会子","纸币","铜钱","金融"]}]}\n'
        '- 问「孔乙己为什么总挨打」：不要输出 ["孔乙己挨打","挨打原因"]，应输出 {"terms":["孔乙己","丁举人","咸亨酒店","偷书","长衫"],"groups":[{"name":"人物","variants":["孔乙己","丁举人","掌柜"]},{"name":"遭遇","variants":["挨打","折腿","偷书","嘲笑"]}]}\n'
        "问题：%s" % question
    )


def _clean_token(text, limit):
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"^[\s\"'“”‘’《》〈〉「」『』\[\]{}()（）]+|[\s\"'“”‘’《》〈〉「」『』\[\]{}()（）]+$", "", t)
    t = re.sub(r"\s+", "", t)
    return t[:limit]


def _clean_term(text):
    return _clean_token(text, 24)


def _clean_variant(text):
    return _clean_token(text, 32)


def _load_json_dict(raw):
    """解析模型输出的 JSON 对象；外层包裹噪声或全角引号时逐段扫描抢救。"""
    if not raw:
        return None
    normalized = raw.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    decoder = json.JSONDecoder()
    for text in (raw, normalized):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, dict):
                    return data
            except ValueError:
                pass
        best = None
        idx = 0
        while True:
            pos = text.find("{", idx)
            if pos < 0:
                break
            try:
                obj, nxt = decoder.raw_decode(text, pos)
            except ValueError:
                idx = pos + 1
                continue
            if isinstance(obj, dict) and ("terms" in obj or best is None):
                best = obj
            idx = max(nxt, pos + 1)
        if best is not None:
            return best
    return None


def _parse_extract(stdout):
    data = _load_json_dict(stdout or "")
    if not data:
        return [], []
    terms = []
    for item in data.get("terms") or []:
        if not isinstance(item, str):
            continue
        for part in re.split(r"[\s,，、;；]+", item):
            part = _clean_term(part)
            if part and part not in GENERIC_TERM_STOPWORDS and part not in terms:
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


def _extract_from_result(res):
    """优先从正文解析切词 JSON；正文为空或缺词时从 reasoning block 抢救最后一段 JSON。"""
    terms, groups = ([], [])
    if not res.error:
        terms, groups = _parse_extract(res.text)
        if len(terms) >= 2:
            return terms, groups, False
    # res.text 为空时 model_call 会把 error 记成「模型没有输出」；若 thinking 里有合法 JSON，仍可抢救
    if not res.error or res.error == "模型没有输出":
        reasoning_text = "\n".join(
            item.get("thinking", "") for item in (getattr(res, "reasoning", None) or []) if isinstance(item, dict)
        ).strip()
        if reasoning_text:
            r_terms, r_groups = _parse_extract(reasoning_text)
            if len(r_terms) >= 2 or (not terms and r_terms):
                return r_terms, r_groups, True
    return terms, groups, False


def _split_candidates(term):
    """为 4 字以上零命中复合词生成拆分候选（先双段，后去后缀词干）。"""
    cands = []
    n = len(term)
    for split_at in (2, 3, n - 2, n - 3):
        if not (2 <= split_at <= n - 2):
            continue
        left = term[:split_at].strip("的与和及在里中被把对向给从")
        right = term[split_at:].strip("的与和及在里中被把对向给从")
        if (
            len(left) >= 2
            and len(right) >= 2
            and left not in GENERIC_TERM_STOPWORDS
            and right not in GENERIC_TERM_STOPWORDS
        ):
            cands.append((left, right))
    for suffix in PREFLIGHT_SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= 2:
            stem = term[: -len(suffix)]
            if stem not in GENERIC_TERM_STOPWORDS:
                cands.append((stem,))
    return cands


def _harvest_group_variants(groups, has_hit, target, limit, max_len=3, match_term=""):
    """从 groups 里回收有命中的短原子变体，补齐零命中复合词被拆/丢后的缺口。"""
    for g in groups or []:
        for v in g.get("variants") or []:
            if not (isinstance(v, str) and 2 <= len(v) <= max_len):
                continue
            if v in GENERIC_TERM_STOPWORDS or v in target:
                continue
            if match_term and not any(ch in match_term for ch in v):
                continue
            if has_hit(v):
                target.append(v)
                if len(target) >= limit:
                    return


def _preflight_terms(terms, groups=None, question="", index_db=None):
    """用 para_fts 轻量检查 >=4 字非专名检索词；零命中复合词拆成有命中的原子短词。"""
    index_db = index_db or INDEX_DB
    terms = [t for t in (terms or []) if isinstance(t, str) and t]
    if not terms or not index_db or not os.path.exists(index_db):
        return terms[:MAX_TERMS], []
    quoted_titles = set(re.findall(r"《([^》]{2,})》", question or ""))
    try:
        con = _ro(index_db)
    except sqlite3.Error:
        return terms[:MAX_TERMS], []
    hit_cache = {}

    def has_hit(cand):
        if cand in hit_cache:
            return hit_cache[cand]
        expr = cjk.query_expr(cand)
        if not expr:
            hit_cache[cand] = False
            return False
        try:
            ok = con.execute("SELECT 1 FROM para_fts WHERE para_fts MATCH ? LIMIT 1", (expr,)).fetchone() is not None
        except sqlite3.Error:
            ok = False
        hit_cache[cand] = ok
        return ok

    def add_unique(target, item):
        if item and item not in GENERIC_TERM_STOPWORDS and item not in target:
            target.append(item)

    try:
        out = []
        dropped = []
        for term in terms:
            if term.isascii() or len(term) < 4 or term in quoted_titles or has_hit(term):
                add_unique(out, term)
                continue
            dropped.append(term)
            replaced = []
            for parts in _split_candidates(term):
                if all(has_hit(p) for p in parts):
                    replaced = list(parts)
                    break
            if not replaced:
                _harvest_group_variants(groups, has_hit, replaced, 2, max_len=3, match_term=term)
            if not replaced:
                for parts in _split_candidates(term):
                    hitting = [p for p in parts if has_hit(p)]
                    if hitting:
                        replaced = hitting
                        break
            for piece in replaced:
                add_unique(out, piece)
        if len(out) < 2:
            _harvest_group_variants(groups, has_hit, out, 2, max_len=4)
        if len(out) >= 2 or (out and all(t in dropped for t in terms)):
            return out[:MAX_TERMS], dropped
        return terms[:MAX_TERMS], []
    finally:
        con.close()


FALLBACK_HEAD = re.compile(
    r"^(?:请问|请|帮我|帮忙|麻烦|我想知道|想知道|介绍一下|介绍下|详细介绍下|详细介绍|说说|讲讲|告诉我|解释一下|解释下|分析一下|分析|查一下|查查|看看|看下|列一下|列出|能否|可以)+"
)
FALLBACK_TAIL = re.compile(r"(?:吗|呢|吧|啊|呀|的)+$")
FALLBACK_SPLIT = re.compile(
    r"(?:是怎么|怎么样|怎么|如何|为什么|为何|哪些|哪几种|哪几个|哪一个|哪个|谁|什么|多少|多久|多大|是否|有没有)"
)
FALLBACK_PARTICLES = re.compile(r"[的与和及在里中被把对向给从、]")
FALLBACK_VERBS = re.compile(r"^(?:提到|提及|谈到|谈及|写过|讲过|说过|写了|讲了|说了)+")


def _strip_question(question):
    """去掉问句的礼貌语、疑问词与句末语气词，留下可检索的实词。"""
    text = unicodedata.normalize("NFKC", question or "")
    text = re.sub(r"\s+", "", text)
    text = text.strip("？?！!。.，,、；;：:")
    text = FALLBACK_HEAD.sub("", text)
    text = FALLBACK_TAIL.sub("", text)
    return text.strip("，,、；;：:？?！!。.")


def _fallback_prompt(question):
    """第一次切词退化时再问一次，只要短原子词，不要长复合短语。"""
    return (
        "把问题拆成 3 到 6 个用于书库全文检索的中文检索原子词（优先 2 个字；3 到 4 个字只给专名或固定术语，书名和事件原名除外）。\n"
        "严禁把朝代、人物和主题拼在一起（如「宋朝金融史」「某人挨打」「挨打原因」）。\n"
        "不要疑问词，不要空泛词（如介绍、情况、原因、影响、问题），不要整句，不要解释。只回一行 JSON：\n"
        '{"terms":["词1","词2","词3"],"groups":[{"name":"组名","variants":["说法1","说法2"]}]}\n'
        "问题：%s" % question
    )


def _fallback_terms(question):
    """去掉疑问脚手架与虚词，提取可进 bigram 检索的短原子词。"""
    raw = unicodedata.normalize("NFKC", question or "")
    titles = []
    for title in re.findall(r"《([^》]{2,24})》", raw):
        cleaned = _clean_term(title)
        if cleaned and cleaned not in titles:
            titles.append(cleaned)
    body = re.sub(r"《[^》]{2,24}》", "、", raw)
    text = _strip_question(body)
    if not text:
        return titles[:MAX_TERMS]
    chunks = [part for part in FALLBACK_SPLIT.split(text) if part]
    if len(chunks) == 1 and len(text) > 12:
        chunks = [part for part in re.split(r"[被把对向给从]", text) if part]
    out = list(titles)
    for part in chunks:
        part = part.strip("的了是在有和与及讲说写做、")
        pieces = FALLBACK_PARTICLES.split(part) if len(part) > 4 else [part]
        for piece in pieces:
            piece = FALLBACK_VERBS.sub("", piece)
            piece = _clean_term(piece.strip("的了是在有和与及讲说写做"))
            if len(piece) >= 4:
                for suffix in ("情况", "问题", "原因"):
                    if piece.endswith(suffix) and len(piece) - len(suffix) >= 2:
                        piece = piece[: -len(suffix)]
                        break
            if len(piece) >= 2 and piece not in GENERIC_TERM_STOPWORDS and piece not in out:
                out.append(piece)
    if not out:
        fallback = _clean_term(text)
        if fallback and fallback not in GENERIC_TERM_STOPWORDS:
            out = [fallback]
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


def extract_terms(question, thinking=DEFAULT_THINKING, use_cache=True, rec=None):
    """返回 {terms, groups, source, ms, note}。source: model|cache|degraded。

    thinking 进缓存键：换档就是另一份切词。use_cache=False 时跳过读缓存，
    但本次模型结果仍写回，下一次同档提问直接命中。
    """
    thinking = normalize_thinking(thinking)
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    path = _cache_path(question, thinking)
    key = os.path.basename(path)[:-5]
    if use_cache and os.path.exists(path):
        t0 = time.time()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            terms = [t for t in data.get("terms", []) if isinstance(t, str) and t]
            groups = data.get("groups") or [{"name": t, "variants": [t]} for t in terms]
            if terms and groups:
                out = {
                    "terms": terms[:MAX_TERMS],
                    "groups": groups,
                    "source": "cache",
                    "thinking": thinking,
                    "ms": round((time.time() - t0) * 1000, 1),
                    "model": MODEL,
                }
                rec.event(
                    "terms.cache",
                    hit=True,
                    key=key,
                    path=path,
                    source="cache",
                    terms=out["terms"],
                    groups=groups,
                    ms=out["ms"],
                )
                rec.set_index(terms_source="cache", terms=len(out["terms"]), groups=len(groups))
                return out
            rec.event("terms.cache", hit=False, key=key, path=path, reason="invalid")
        except (OSError, ValueError) as exc:
            rec.event("terms.cache", hit=False, key=key, path=path, error=exc.__class__.__name__)
    elif not use_cache:
        rec.event("terms.cache", hit=False, key=key, path=path, reason="fresh")
    else:
        rec.event("terms.cache", hit=False, key=key, path=path, reason="missing")

    t0 = time.time()
    note = ""
    terms, groups = [], []
    first_terms, first_groups = [], []
    retry_terms, retry_groups = [], []
    first_error = ""
    retry_error = ""
    retried = False
    try:
        prompt = _prompt(question)
        rec.event("terms.prompt", **rec.large(prompt, "prompt"), question=question, thinking=thinking)
        res = model_call.invoke(
            prompt,
            [*PI_JSON, *pi_flags(thinking)],
            [*PI, *pi_flags(thinking)],
            kind="terms",
            rec=rec,
            timeout=PI_TIMEOUT,
            cwd=ROOT,
            parser="terms",
        )
        first_error = res.error
        terms, groups, rescued = _extract_from_result(res)
        first_terms, first_groups = terms, groups
        if rescued and first_error == "模型没有输出":
            first_error = ""
            note = "已从思考过程抢救切词 JSON"
        if len(terms) < 2:
            retry_prompt = _fallback_prompt(question)
            rec.event(
                "terms.retry_prompt",
                **rec.large(retry_prompt, "prompt"),
                question=question,
                thinking=thinking,
            )
            retry = model_call.invoke(
                retry_prompt,
                [*PI_JSON, *pi_flags(thinking)],
                [*PI, *pi_flags(thinking)],
                kind="terms_retry",
                rec=rec,
                timeout=PI_TIMEOUT,
                cwd=ROOT,
                parser="terms",
            )
            retry_error = retry.error
            retry_terms, retry_groups, retry_rescued = _extract_from_result(retry)
            if retry_rescued and retry_error == "模型没有输出":
                retry_error = ""
            if len(retry_terms) >= 2:
                terms = retry_terms
                groups = retry_groups or [{"name": t, "variants": [t]} for t in retry_terms]
                note = "第一次切词不可用，已重试"
                retried = True
            rec.event(
                "terms.retry",
                tried=True,
                used=retried,
                first_terms=first_terms,
                first_groups=first_groups,
                first_error=first_error,
                retry_terms=retry_terms,
                retry_groups=retry_groups,
                retry_error=retry_error,
            )
        else:
            rec.event("terms.retry", tried=False, used=False, rescued=rescued)
        if terms and not groups:
            groups = [{"name": t, "variants": [t]} for t in terms]
        # 读侧认 terms 且 groups；模型输出后对 >=4 字非专名先做 FTS5 零命中拆词，再写缓存
        if terms and groups:
            raw_terms = list(terms)
            terms, preflight_dropped = _preflight_terms(terms, groups=groups, question=question)
            if preflight_dropped:
                rec.event(
                    "terms.preflight",
                    before=raw_terms,
                    after=terms,
                    dropped=preflight_dropped,
                )
                extra = "已拆零命中长词：" + "、".join(preflight_dropped)
                note = f"{note}；{extra}" if note else extra
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
            out = {
                "terms": terms,
                "groups": groups,
                "source": "model",
                "thinking": thinking,
                "ms": ms,
                "model": MODEL,
                "note": note,
            }
            rec.event(
                "terms.model",
                source="model",
                terms=terms,
                groups=groups,
                note=note,
                ms=ms,
                model=MODEL,
                thinking=thinking,
                first_error=first_error,
                retry_error=retry_error,
            )
            rec.event("terms.cache_write", key=key, path=path, source="model")
            rec.set_index(terms_source="model", terms=len(terms), groups=len(groups))
            return out
        note = note or ("模型没给出可用的词组" if not first_error else "模型返回不可用（%s）" % first_error)
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
        rec.event("terms.cache_write", key=key, path=path, source="degraded")
    rec.event(
        "terms.degraded",
        source="degraded",
        note=note,
        terms=terms,
        groups=groups,
        ms=ms,
        first_error=first_error,
        retry_error=retry_error,
    )
    rec.set_index(terms_source="degraded", terms=len(terms), groups=len(groups))
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


def read_chapter(path, line=1, chars=STAGE_CHARS, before=STAGE_BEFORE, rec=None):
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
    info = {
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
    if rec is not None:
        rec.event(
            "window.read",
            path=path,
            line=start,
            title=title,
            chars=len(body),
            total_lines=total_lines,
            ms=info["ms"],
            file=runlog.file_fingerprint(real),
        )
    return info


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
        cached = dict(_META_CACHE["value"])
        cached["embed"] = vec_search.health_info()
        cached["rerank"] = rerank_client.health_info(root=ROOT)
        return cached
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
    _META_CACHE.update(at=now, value=dict(out))
    out["embed"] = vec_search.health_info()
    out["rerank"] = rerank_client.health_info(root=ROOT)
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


def _clean_scope_books(scope):
    if not isinstance(scope, dict):
        return []
    raw = scope.get("books")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:16]:
        if isinstance(item, str):
            uid = item.strip()
            if uid and re.fullmatch(r"[A-Za-z0-9_-]+", uid) and uid not in out:
                out.append(uid)
    return out


def build(question, select=0, thinking=DEFAULT_THINKING, fresh=False, rec=None, scope=None):
    question = (question or "").strip()
    if not question:
        raise ValueError("question 为空")
    thinking = normalize_thinking(thinking)
    scope_books = _clean_scope_books(scope)
    rec = rec or runlog.Recorder("", "", {}, enabled=False)
    rec.event(
        "trace.request",
        question=question,
        select=select,
        thinking=thinking,
        fresh=fresh,
        scope={"books": scope_books} if scope_books else None,
        index=runlog.file_fingerprint(INDEX_DB, hash_content=False),
        library=runlog.file_fingerprint(LIB_DB, hash_content=False),
    )
    understand = extract_terms(question, thinking=thinking, use_cache=not fresh, rec=rec)
    terms = understand["terms"]
    rec.event(
        "trace.terms",
        source=understand.get("source", ""),
        terms=list(terms),
        groups=understand.get("groups", []),
        note=understand.get("note", ""),
        ms=understand.get("ms", 0),
        model=understand.get("model", ""),
    )

    con = _ro(INDEX_DB)
    try:
        books = book_cards.load_books(LIB_DB)
        book_meta = book_score.load_meta(LIB_DB)
        scope_set = set(scope_books)
        visible_books = {uid: rec for uid, rec in books.items() if uid in scope_set} if scope_set else books
        hints = book_cards.query_hints(question, visible_books)
        if len(scope_books) == 1 and scope_books[0] in books:
            uid = scope_books[0]
            rec_book = books[uid]
            focus = {
                "status": "exact",
                "match": "scope",
                "query_title": rec_book.get("title") or uid,
                "book": uid,
                "uid": uid,
                "title": rec_book.get("title") or uid,
                "author": rec_book.get("author") or "",
                "chapters": book_title._chapters(book_meta, uid),
                "candidates": [],
                "best_path": "",
                "best_line": 0,
                "explicit_scope": True,
            }
        else:
            focus = book_title.resolve_title(question, visible_books, book_meta, hint=hints.get("title", ""))
            if focus.get("book") and not focus.get("uid"):
                focus["uid"] = focus["book"]
        t_plan = time.time()
        plan = book_query.run(
            terms, con=con, limit=8, max_chapters=3, focus=focus, question=question, scope=scope_books
        )
        plan_ms = plan.ms
        rec.event(
            "search.plan",
            **rec.large(json.dumps(asdict(plan), ensure_ascii=False, default=str), "plan"),
            terms=list(plan.terms),
            stop=plan.stop,
            best_round=plan.best_round,
            quality=list(plan.quality),
            ms=plan_ms,
            fetches=plan.fetches,
            model_calls=plan.model_calls,
            timings=plan.timings,
        )
        cardset = book_cards.run(
            question,
            terms=plan.terms or terms,
            question=question,
            expand={},
            feedback=None,
            con=con,
            focus=focus,
            scope=scope_books,
        )
        cards_ms = cardset.ms
        rec.event(
            "search.cards",
            **rec.large(json.dumps(asdict(cardset), ensure_ascii=False, default=str), "cards"),
            trigger=bool(cardset.trigger),
            reasons=list(cardset.reasons),
            card_count=len(cardset.cards),
            next=len(cardset.next),
            uncertainty=cardset.uncertainty,
            ms=cards_ms,
            model_calls=cardset.model_calls,
        )

        card_dicts = []
        for _i, c in enumerate(cardset.cards, 1):
            row = {
                "uid": c.book,
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
            "fusion": plan.fusion,
            "vec_used": bool(plan.vec_used),
            "vec_ms": plan.vec_ms,
            "vec_hits": int(plan.vec_hits),
            "rerank_used": bool(getattr(plan, "rerank_used", False)),
            "rerank_ms": float(getattr(plan, "rerank_ms", 0.0)),
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
                    "uid": b.book,
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
                    "uid": c["book"],
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
        kept_ranking = _pick_rows(plan.ranking, 8, focus.get("book"))
        kept_chapters = _pick_rows(plan.chapters, 3, focus.get("book"))
        rec.event(
            "search.trim",
            ranking_total=len(plan.ranking),
            ranking_kept=[b.book for b in kept_ranking],
            ranking_dropped=[b.book for b in plan.ranking if b not in kept_ranking],
            chapters_total=len(plan.chapters),
            chapters_kept=[c["book"] for c in kept_chapters],
            chapters_dropped=[c["book"] for c in plan.chapters if c not in kept_chapters],
            focus=focus,
            select=select,
        )

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
        rec.event("trace.pick", pick=pick, select=select, focus=focus)
        t_res = time.time()
        result = None
        if pick and pick.pop("focus_empty", False):
            result = {
                "uid": pick["book"],
                "book": pick["book"],
                "book_title": pick["title"],
                "chapter": "",
                "path": "",
                "line": 0,
                "text": "",
                "focus_empty": True,
            }
        elif pick:
            result = read_chapter(pick["path"], pick["line"], STAGE_CHARS, rec=rec)
            result.update(
                {
                    "uid": pick["book"],
                    "book": pick["book"],
                    "book_title": pick["title"],
                    "chapter": pick["chapter"],
                    "card": pick["card"],
                }
            )
        rec.event(
            "trace.result",
            book=(result or {}).get("book", ""),
            path=(result or {}).get("path", ""),
            line=(result or {}).get("line", 0),
            chars=len((result or {}).get("text", "")),
            card=(result or {}).get("card", 0),
            focus_empty=bool((result or {}).get("focus_empty")),
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
        meta_info = meta()
        total_ms = round((time.time() - t_plan) * 1000 + understand["ms"], 1)
        rec.event("trace.stage_ms", **stage_ms)
        rec.event("trace.total", total_ms=total_ms, plan_ms=plan_ms, cards_ms=cards_ms)
        rec.event("trace.meta", **meta_info)
        return {
            "question": question,
            "thinking": thinking,
            "scope": {"books": scope_books} if scope_books else None,
            "extract": understand,
            "plan": plan_dict,
            "focus": focus,
            "cards": cards_dict,
            "result": result,
            "stage_ms": stage_ms,
            "meta": meta_info,
            "total_ms": total_ms,
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
