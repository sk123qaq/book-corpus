#!/usr/bin/env python3
"""P1.5 文档扩展：给每章生成「主题前缀」，写进独立索引，不碰正文。

  build_expand.py --pilot 20 --batch 5 --chars 800 --effort low --max-out 2500
  build_expand.py --all --workers 20
  build_expand.py --books B0169,B0123 --force

产物：目录/检索/expand/<uid>.md 与 index.db 的 expand 表 + expand_fts。
只读 库/，只写 目录/检索/。
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib_common import PATHS, ROOT

sys.path.insert(0, HERE)
import lib_fts_cjk as cjk

DB = PATHS.index_db
OUTDIR = os.path.join(PATHS.index_dir, "expand")
LIBDB = PATHS.library_db
LOG = os.path.join(PATHS.index_dir, "build.log")
AUTH = os.path.expanduser("~/.pi/agent/auth.json")
MIMO_KEY = os.path.expanduser("~/.config/mimo/api_key")
MIMO_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"
API = "https://api.commandcode.ai/provider/v1/responses"
MODEL = "deepseek/deepseek-v4.1-flash"
MIMO_MODEL = "mimo-v2.6-flash"
OPENCODE_URL = "https://opencode.ai/inference/go/v1/chat/completions"
OPENCODE_MODEL = "deepseek-v4.1-flash"
OPENCODE_UA = "opencode/1.18.16"
OPENCODE_SESSION = "ses_" + uuid.uuid4().hex[:24]
PROVIDERS = {"commandcode": (API, MODEL), "mimo": (MIMO_URL, MIMO_MODEL), "opencode": (OPENCODE_URL, OPENCODE_MODEL)}
MIMO_REJECT = "high risk"

SYS = "你是图书索引员。只输出结果，不加解释。"
BATCH_PROMPT = """下面有 {n} 个章节。为每个章节输出一行，格式严格为：
<编号>|摘要（不超过60字，点出本章真正讨论的抽象主题：如自由、权力、服从、孤独、正义、秩序；不要复述情节，不要以「本章」开头）

{blocks}

注意：一行一个章节，编号必须与上面一致，不要输出别的任何内容。"""

QUERY_PROMPT = """读者没读过下面这章，只会用自己脑子里的词在搜索框里找它。请写 5 条检索式，每条不超过 20 字。
硬性要求：
1. 每条至少含 2 个正文里根本没出现的词（同义词、上位概念、学术用语、大白话都行）；
2. 不许照抄正文的专名和术语；
3. 写读者真的会打的字，不写「为什么……？」这种考卷问句。

两个正例：
正文：「银行把贷款打包成理财产品卖给客户，评级机构给这些产品打了 AAA，风险被层层转手，最后谁也不知道底层资产是什么。」
检索式：1. 次贷危机是怎么酿成的 2. 金融衍生品为什么把风险放大 3. 评级机构为什么不可信

正文：「族谱只记男丁，祠堂的田租供子弟读书，外姓人不得入祠。」
检索式：1. 宗族为什么重男轻女 2. 祠堂的田产归谁 3. 乡村宗法制度怎么运作

章节标题：{title}
正文：{text}"""

MIMO_BATCH_PROMPT = """下面有 {n} 个章节。为每个章节输出一行，格式严格为：
<章节文件名>|摘要（不超过60字，点出本章真正讨论的抽象主题：如自由、权力、服从、孤独、正义、秩序；不要复述情节，不要以「本章」开头）

{blocks}

注意：一行一个章节，行首必须是上面给出的章节文件名原样照抄，不要用你自己数的序号，不要输出别的任何内容。"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS expand(
  path TEXT NOT NULL, kind TEXT NOT NULL, tok TEXT NOT NULL, text TEXT NOT NULL,
  model TEXT, prompt_hash TEXT, tokens_in INTEGER, tokens_out INTEGER,
  built_at TEXT, PRIMARY KEY(path, kind));
CREATE VIRTUAL TABLE IF NOT EXISTS expand_fts USING fts5(
  tok, content='expand', content_rowid='rowid', tokenize="unicode61 remove_diacritics 0");
"""


def batch_tpl(args):
    """按 provider 选提示词。mimo 会把自己数的序号当编号，改让它照抄章节文件名对齐。"""
    return MIMO_BATCH_PROMPT if args.provider == "mimo" else BATCH_PROMPT


def prompt_hash(args):
    tpl = batch_tpl(args) if args.kind == "prefix" else QUERY_PROMPT
    key = "%s|%s|%s|%s|%s|%s" % (tpl, args.kind, args.batch, args.chars, args.effort, PROVIDERS[args.provider][1])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def md_files(uid, book_rel):
    base = os.path.join(ROOT, "books", book_rel, "md")
    return [os.path.join(base, f) for f in sorted(os.listdir(base)) if f.endswith(".md") and f != "README.md"]


def body(path, chars):
    t = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
    if t.startswith("---"):
        i = t.find("\n---", 3)
        if i > 0:
            t = t[i + 4 :]
    t = re.sub(r"!\[\]\([^)]*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:chars]


def is_stub(path):
    """剥掉 frontmatter、图片、标题行后不足 30 字的章：图片页/标题页，没有可索引正文。

    这种章硬生成只会编造（实测动物农场纯图章生出整段假剧情梗概），跳过不调模型。
    别用 body() 判：它先合并换行，标题会和正文并成一行被误杀。"""
    t = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
    if t.startswith("---"):
        i = t.find("\n---", 3)
        if i > 0:
            t = t[i + 4 :]
    t = re.sub(r"!\[\]\([^)]*\)", " ", t)
    t = "\n".join(line for line in t.splitlines() if not line.lstrip().startswith("#") and line.strip())
    return len(t.strip()) < 30


def title_of(path):
    head = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")[:400]
    m = re.search(r"^title:\s*\"?(.+?)\"?\s*$", head, re.M)
    if m:
        return m.group(1)
    for line in head.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return os.path.basename(path)


class ContentRejectedError(Exception):
    pass


def _chat_call(url, model, key, prompt, max_out, timeout, ua=None, session=None):
    """OpenAI 兼容的 /chat/completions。mimo 与 opencode-go 同形，只差 base URL、模型与 key。"""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": SYS + "\n\n" + prompt}],
        "max_tokens": max_out,
        "stream": False,
    }
    if "xiaomimimo" in url:
        body["temperature"] = 0.3
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if ua:
        headers["User-Agent"] = ua
    if session:
        headers["x-opencode-session"] = session
    req = urllib.request.Request(  # noqa: S310 URL 来自模块内 https 常量
        url, data=json.dumps(body).encode(), headers=headers
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 端点来自模块内 https 常量，urlopen 不接外部输入
        d = json.load(r)
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    txt = msg.get("content") or ""
    u = d.get("usage") or {}
    if MIMO_REJECT in txt:
        raise ContentRejectedError(txt[:80])
    return txt, {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}


def _mimo_call(prompt, max_out, timeout):
    return _chat_call(
        MIMO_URL, MIMO_MODEL, pathlib.Path(MIMO_KEY).read_text(encoding="utf-8").strip(), prompt, max_out, timeout
    )


def _opencode_call(prompt, max_out, timeout):
    """opencode-go 要求每请求带 x-opencode-session，否则 400 MissingSessionID。
    一个进程一个会话 id：服务端靠它把同会话请求路由到同一上游。"""
    key = json.loads(pathlib.Path(AUTH).read_text(encoding="utf-8"))["opencode-go"]["key"]
    return _chat_call(OPENCODE_URL, OPENCODE_MODEL, key, prompt, max_out, timeout, OPENCODE_UA, OPENCODE_SESSION)


def call(prompt, args, timeout=300, tries=3):
    url, model = PROVIDERS[args.provider]
    if args.provider in ("mimo", "opencode"):
        fn = _opencode_call if args.provider == "opencode" else _mimo_call
        last = None
        for i in range(tries):
            try:
                return fn(prompt, args.max_out, timeout)
            except ContentRejectedError:
                raise
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = exc
                if i + 1 < tries:
                    time.sleep(5 * (i + 1) ** 2)
        raise RuntimeError("%s 连续失败 %d 次: %s" % (args.provider, tries, last))
    key = json.loads(pathlib.Path(AUTH).read_text(encoding="utf-8"))["commandcode"]["key"]
    body_obj = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": SYS + "\n\n" + prompt}]}],
        "max_output_tokens": args.max_out,
        "reasoning": {"effort": args.effort},
        "store": False,
    }
    req = urllib.request.Request(  # noqa: S310 URL 来自模块内 https 常量
        url,
        data=json.dumps(body_obj).encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "x-command-code-version": "1.14.0",
            "x-cli-environment": "production",
            "x-project-slug": "books",
            "User-Agent": "cli",
        },
    )
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 端点来自模块内 https 常量，urlopen 不接外部输入
                d = json.load(r)
            txt = "".join(
                c.get("text", "")
                for o in d.get("output", [])
                if o.get("type") == "message"
                for c in o.get("content", [])
                if c.get("type") == "output_text"
            )
            return txt, d.get("usage", {})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(5 * (i + 1) ** 2)
    raise RuntimeError("接口连续失败 %d 次: %s" % (tries, last))


def parse(reply, paths, by_name=False):
    """按编号取（deepseek 守约）或按章节文件名取（mimo 会把文件名里的序号当成编号）。

    编号越界时不再静默丢弃，改成按出现顺序补到还没被占用的章上。
    """
    got, used = {}, set()
    pending = []
    for line in reply.splitlines():
        m = re.match(r"\s*([^|｜]+?)\s*[|｜]\s*(.+)", line)
        if not m:
            continue
        key, txt = m.group(1).strip(), m.group(2).strip().strip("|").strip()
        if not txt:
            continue
        idx = None
        if by_name:
            for i, p in enumerate(paths):
                if os.path.basename(p) == key and i not in used:
                    idx = i
                    break
        if idx is None and key.isdigit():
            i = int(key) - 1
            if 0 <= i < len(paths) and i not in used:
                idx = i
        if idx is None:
            pending.append(txt)
            continue
        used.add(idx)
        got[paths[idx]] = txt
    free = [p for i, p in enumerate(paths) if i not in used]
    for p, txt in zip(free, pending, strict=False):
        got[p] = txt
    return got


def build_prompt(chunk, args, texts=None):
    if args.kind == "queries":
        txt = (texts or {}).get(chunk[0]) or body(chunk[0], args.chars)
        return QUERY_PROMPT.format(title=title_of(chunk[0]), text=txt)
    blocks = []
    for i, p in enumerate(chunk, 1):
        txt = (texts or {}).get(p) or body(p, args.chars)
        key = os.path.basename(p) if args.provider == "mimo" else "%d." % i
        blocks.append(
            "%s 书名：《%s》\n章标题：%s\n正文节选：%s"
            % (key, os.path.basename(os.path.dirname(os.path.dirname(p))), title_of(p), txt)
        )
    return batch_tpl(args).format(n=len(chunk), blocks="\n\n".join(blocks))


def try_once(chunk, args, texts=None):
    reply, usage = call(build_prompt(chunk, args, texts), args)
    got = (
        _parse_queries(reply, chunk) if args.kind == "queries" else parse(reply, chunk, by_name=args.provider == "mimo")
    )
    return got, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def _parse_queries(reply, chunk):
    got = " ".join(line.strip() for line in reply.splitlines() if line.strip())
    if len(re.findall(r"(?:^|\s)\d+[\.、]", got)) < 3:
        return {}
    return {chunk[0]: got}


def expand_chunk(chunk, args, texts=None, depth=0, tally=None):
    """取一批章节的扩展文本。400 或漏行时自己拆小，不把整批丢掉。

    三層退让，每層都记进 tally 便于事后查：
    1. 整批失败 → 批对半拆，两半各自重试；
    2. 单章仍失败 → 正文对半，哪半能过就用哪半的输出；
    3. 回包漏行 → 只把缺的那几章单独重发，不重跑已成功的。
    """
    tally = tally if tally is not None else []
    if depth > 4:
        tally.append("拆分到第 5 层仍失败")
        return {}, 0, 0
    got, tin, tout = {}, 0, 0
    try:
        r, ti, to = try_once(chunk, args, texts)
        got.update(r)
        tin += ti
        tout += to
    except Exception as exc:
        tag = "内容过滤" if isinstance(exc, ContentRejectedError) else re.split(r"[:\n]", str(exc))[-1].strip()[:40]
        if len(chunk) > 1:
            mid = len(chunk) // 2
            tally.append("批拆半(%d章)" % len(chunk))
            for part in (chunk[:mid], chunk[mid:]):
                g, ti, to = expand_chunk(part, args, texts, depth + 1, tally)
                got.update(g)
                tin += ti
                tout += to
        else:
            p = chunk[0]
            full = (texts or {}).get(p) or body(p, args.chars)
            if len(full) > 80:
                mid = len(full) // 2
                tally.append("正文拆半(%d字)" % len(full))
                for half in (full[:mid], full[mid:]):
                    try:
                        r, ti, to = try_once([p], args, {p: half})
                    except Exception:
                        r = None
                    if r:
                        got.update(r)
                        tin += ti
                        tout += to
                        break
            if not got:
                tally.append("单章两半都不行: " + tag)

    missing = [p for p in chunk if p not in got]
    if missing and len(chunk) > 1:
        tally.append("漏行补发(%d章)" % len(missing))
        for p in missing:
            g, ti, to = expand_chunk([p], args, texts, depth + 1, tally)
            got.update(g)
            tin += ti
            tout += to
    return got, tin, tout


def expand_book(uid, book_rel, paths, args, phash, lock=None, fails=None, sink=None):
    """逐块取扩展文本。sink 每块回调一次，让调用方立刻落库。

    以前是整本跑完才提交，一本 11,619 章的合集要跑六小时才写第一行，
    中途死掉全部白做，外部也看不出它在跑还是卡了。
    """
    out, tin, tout = {}, 0, 0
    step = 1 if args.kind == "queries" else args.batch
    tally = []
    for start in range(0, len(paths), step):
        chunk = paths[start : start + step]
        got, ti, to = expand_chunk(chunk, args, None, 0, tally)
        out.update(got)
        tin += ti
        tout += to
        if got and sink:
            sink(uid, book_rel, got, ti, to)
        lost = [p for p in chunk if p not in got]
        if lost:
            print("丢失 %s %d 章: %s" % (uid, len(lost), os.path.basename(lost[0])), file=sys.stderr)
            if lock and fails is not None:
                with lock:
                    fails.extend(["最后仍缺章"] * len(lost))
        if start and start % (step * 40) == 0:
            print("  进度 %s %d/%d 章" % (uid, start, len(paths)), file=sys.stderr)
    if tally and lock and fails is not None:
        with lock:
            fails.extend(tally)
    return out, tin, tout


def write_md(uid, book_rel, got):
    """账本只追加。本次只补了几章，也要和旧的合起来写，不能覆盖掉之前写的章。"""
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, uid + ".md")
    old = {}
    if os.path.isfile(path):
        cur = None
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("## "):
                    cur = line[3:].strip()
                    old[cur] = []
                elif cur and line.strip():
                    old[cur].append(line.rstrip())
    base = os.path.join(ROOT, "books", book_rel, "md")
    for p, txt in got.items():
        old[os.path.relpath(p, base)] = [txt]
    lines = [
        "# 文档扩展账 %s" % uid,
        "",
        "书：`books/%s`" % book_rel,
        "这些是索引文本，不是原文。引用一律回章节 md 取行号。",
        "",
    ]
    for name in sorted(old):
        lines += ["## %s" % name, ""] + old[name] + [""]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--books", default="")
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--chars", type=int, default=800)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-out", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--provider",
        default="commandcode",
        choices=list(PROVIDERS),
        help="commandcode=deepseek-v4.1-flash（无过滤）；mimo=mimo-v2.6-flash（有内容过滤）",
    )
    ap.add_argument("--kind", default="prefix", choices=["prefix", "queries"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--regen-stale", action="store_true", help="把 prompt_hash 不同的旧行也重排；默认只补完全没行的章")
    ap.add_argument(
        "--only-file",
        default="",
        help="只扩展这个清单里的章节。每行一个，路径（books/…）放最后一列，可带 tab 前缀列；# 开头与空行忽略",
    )
    ap.add_argument(
        "--exclude-file",
        default="",
        help="排除清单：一行一个 uid（# 开头与空行忽略）。被排的书整本跳过，已生成的行不动",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--rebuild-md", action="store_true", help="不调模型，只按库里的 expand 表重写 build/检索/expand/*.md 账文件"
    )
    ap.add_argument(
        "--reindex-fts", action="store_true", help="不调模型，从 expand 表整体重建 expand_fts 索引（修索引漂移，幂等）"
    )
    ap.add_argument(
        "--prune-stub",
        action="store_true",
        help="不调模型，备份后删掉空正文章（图片页/标题页）的 expand 行，再重建 expand_fts",
    )
    a = ap.parse_args()

    if a.prune_stub:
        con = sqlite3.connect(DB)
        con.executescript(SCHEMA)
        lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
        dump = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "expand-stub-rows.jsonl")
        cols = ["path", "kind", "tok", "text", "model", "prompt_hash", "tokens_in", "tokens_out", "built_at"]
        n = 0
        with open(dump, "a", encoding="utf-8") as fh:
            for uid, rel in lib.execute("SELECT uid, path FROM book ORDER BY uid"):
                try:
                    paths = md_files(uid, rel)
                except FileNotFoundError:
                    continue
                for p in paths:
                    if not is_stub(p):
                        continue
                    rel_p = os.path.relpath(p, ROOT)
                    sql = "SELECT %s FROM expand WHERE path=?" % ",".join(cols)  # noqa: S608 列名来自常量 cols
                    for row in con.execute(sql, (rel_p,)):
                        fh.write(json.dumps(dict(zip(cols, row, strict=False)), ensure_ascii=False) + "\n")
                        n += 1
                    con.execute("DELETE FROM expand WHERE path=?", (rel_p,))
        con.execute("INSERT INTO expand_fts(expand_fts) VALUES('rebuild')")
        con.commit()
        print("备份并删除 %d 行（空正文章），expand_fts 已重建，备份在 %s" % (n, dump))
        return

    if a.reindex_fts:
        con = sqlite3.connect(DB)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO expand_fts(expand_fts) VALUES('rebuild')")
        con.commit()
        print("expand_fts 重建 %d 行" % con.execute("SELECT count(*) FROM expand").fetchone()[0])
        return

    con = sqlite3.connect(DB, check_same_thread=False)
    con.executescript(SCHEMA)
    phash = prompt_hash(a)
    # 进度按章记，不按提示词版本记：改参数不该把已经花过额度的章判成没做。
    # 要重生成旧版本的行，显式 --regen-stale；要全部重做，用 --force。
    done = {r[0] for r in con.execute("SELECT path FROM expand WHERE kind=?", (a.kind,))}
    if a.regen_stale:
        done = {r[0] for r in con.execute("SELECT path FROM expand WHERE kind=? AND prompt_hash=?", (a.kind, phash))}
    lib = sqlite3.connect("file:%s?mode=ro" % LIBDB, uri=True)
    books = lib.execute("SELECT uid, path FROM book ORDER BY uid").fetchall()
    if a.books:
        want = set(a.books.split(","))
        books = [(u, p) for u, p in books if u in want]
    elif a.pilot:
        books = books[: a.pilot]
    elif not (a.all or a.only_file or a.rebuild_md):
        ap.error("要 --pilot N / --books / --all / --only-file / --rebuild-md")
    if a.exclude_file:
        with open(a.exclude_file, encoding="utf-8") as fh:
            drop = {ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")}
        before = len(books)
        books = [(u, p) for u, p in books if u not in drop]
        print("排除清单 %d 条，跳过书 %d 本" % (len(drop), before - len(books)), file=sys.stderr)
    if a.rebuild_md:
        rows = con.execute("SELECT path, text FROM expand WHERE kind=?", (a.kind,)).fetchall()
        bybook = {}
        for p, t in rows:
            m = re.match(r"books/(.*)/md/(.*)$", p)
            if m:
                bybook.setdefault(m.group(1), {})[m.group(2)] = (p, t)
        uids = {rel: uid for uid, rel in books}
        n = 0
        for rel, chs in bybook.items():
            uid = uids.get(rel)
            if not uid:
                continue
            write_md(uid, rel, dict(chs.values()))
            n += len(chs)
        print("重写账文件 %d 本 %d 章" % (len(bybook), n))
        return
    only = set()
    if a.only_file:
        with open(a.only_file, encoding="utf-8") as fh:
            only = {ln.rsplit("\t", 1)[-1].strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")}
        only = {p for p in only if p.startswith("books/")}
    jobs = []
    for uid, rel in books:
        try:
            paths = list(md_files(uid, rel))
        except FileNotFoundError:
            continue
        todo = [p for p in paths if a.force or os.path.relpath(p, ROOT) not in done]
        if only:
            todo = [p for p in todo if os.path.relpath(p, ROOT) in only]
        todo = [p for p in todo if not is_stub(p)]
        if a.kind == "queries":
            jobs += [(uid, rel, [p]) for p in todo]
        elif todo:
            jobs.append((uid, rel, todo))
    if only:
        sched = {os.path.relpath(p, ROOT) for _, _, ts in jobs for p in ts}
        unsel = only - sched
        if unsel:
            print(
                "only-file %d 条未排上（已扩展过、路径不存在或不在书集里）: %s"
                % (len(unsel), "; ".join(sorted(unsel)[:3])),
                file=sys.stderr,
            )
    nch = sum(len(j[2]) for j in jobs)
    print("%s：任务 %d 个 / 待扩展章 %d 个 / %d 线程" % (a.kind, len(jobs), nch, a.workers), file=sys.stderr)
    if a.dry_run:
        return
    t0 = time.time()
    token_in = token_out = 0
    lock = threading.Lock()
    fails = []

    def sink(uid, rel, got, tin, tout):
        """一块落一次库。账文件（.md）整本跑完再写，缺了用 --rebuild-md 补。"""
        nonlocal token_in, token_out
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = [
            (
                os.path.relpath(p, ROOT),
                a.kind,
                cjk.index_text(t),
                t,
                PROVIDERS[a.provider][1],
                phash,
                tin // max(1, len(got)),
                tout // max(1, len(got)),
                now,
            )
            for p, t in got.items()
        ]
        keys = [(r[0], a.kind) for r in rows]
        with lock:
            token_in += tin
            token_out += tout
            for rel, kind in keys:
                # external content 表不能直接 DELETE，走 FTS5 的 'delete' 命令
                con.execute(
                    "INSERT INTO expand_fts(expand_fts, rowid, tok) "
                    "SELECT 'delete', rowid, tok FROM expand "
                    "WHERE path=? AND kind=?",
                    (rel, kind),
                )
            con.executemany("INSERT OR REPLACE INTO expand VALUES(?,?,?,?,?,?,?,?,?)", rows)
            con.executemany(
                "INSERT INTO expand_fts(rowid, tok) SELECT rowid, tok FROM expand WHERE path=? AND kind=?", keys
            )
            con.commit()

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {
            pool.submit(expand_book, uid, rel, todo, a, phash, lock, fails, sink): (uid, rel) for uid, rel, todo in jobs
        }
        for finished, fut in enumerate(as_completed(futs), 1):
            uid, rel = futs[fut]
            got, _tin, _tout = fut.result()
            with lock:
                write_md(uid, rel, got)
                now_n = con.execute("SELECT count(*) FROM expand WHERE kind=?", (a.kind,)).fetchone()[0]
                print(
                    "  %d/%d 本  %d 章  in %d out %d  %.0fs"
                    % (finished, len(jobs), now_n, token_in, token_out, time.time() - t0),
                    file=sys.stderr,
                )
    n = con.execute("SELECT count(*) FROM expand WHERE kind=?", (a.kind,)).fetchone()[0]
    print("expand 表 %d 章  in %d tok  out %d tok  %.0fs" % (n, token_in, token_out, time.time() - t0))
    if fails:
        tally = {}
        for f in fails:
            tally[f] = tally.get(f, 0) + 1
        print(
            "失败 %d 块，按原因: %s"
            % (len(fails), ", ".join("%s×%d" % kv for kv in sorted(tally.items(), key=lambda x: -x[1]))),
            file=sys.stderr,
        )
    log(
        "expand kind=%s provider=%s jobs=%d chapters=%d in=%d out=%d secs=%.0f phash=%s "
        "effort=%s batch=%d chars=%d"
        % (a.kind, a.provider, len(jobs), nch, token_in, token_out, time.time() - t0, phash, a.effort, a.batch, a.chars)
    )


if __name__ == "__main__":
    main()
