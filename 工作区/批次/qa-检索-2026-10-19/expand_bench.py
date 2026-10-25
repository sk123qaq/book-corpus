#!/usr/bin/env python3
"""P1.5 文档扩展的 token/质量对比台。

只打 commandcode 的 deepseek/deepseek-v4.1-flash，effort=low|high。
六种产出形态 × 两档思考，同一批章节、同一截断长度，比 token 与「注入率」。

用法: python3 expand_bench.py [--levels low,high] [--concurrency 5] [--out results.jsonl]
产物: results.jsonl（一行一次调用，只追加）
"""
import argparse, json, os, re, sys, time, threading
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

API = "https://api.commandcode.ai/provider/v1/responses"
MODEL = "deepseek/deepseek-v4.1-flash"
AUTH = os.path.expanduser("~/.pi/agent/auth.json")
ROOT = os.path.expanduser("~/文档/books")

BOOKS = {
    "liuqing029": ("刘擎西方现代思想讲义", "08 | 韦伯IV “现代的铁笼”是怎么锈就的",
                   "库/人文社科/思想与哲学/刘擎西方现代思想讲义/md/029_08_韦伯IV “现代的铁笼”是怎么锈就的.md",
                   ["自由", "自主", "支配"]),
    "liuqing072": ("刘擎西方现代思想讲义", "21 | 阿伦特II 怎么才能不变成坏人",
                   "库/人文社科/思想与哲学/刘擎西方现代思想讲义/md/072_思考的含义：独立判断.md",
                   ["自主", "独立", "判断"]),
    "wuhe05": ("乌合之众", "第二章 群体的情感与道德",
               "库/人文社科/心理与认知/乌合之众/md/05_第二章群体的情感与道德.md",
               ["自主", "群体", "冲动"]),
    "farm21": ("动物农场", "第十章（结尾）",
               "库/文学/严肃文学小说/动物农场/md/21_◆◆◆ ◆◆◆.md",
               ["权力", "平等", "革命"]),
    "n1984_08": ("1984", "第8节",
                 "库/文学/严肃文学小说/1984/md/08_第8节.md",
                 ["自由", "监视", "私人"]),
    "xiangtu10": ("乡土中国", "礼治秩序",
                  "库/人文社科/人类学与考古/乡土中国/md/10_礼治秩序.md",
                  ["礼", "规范", "自由"]),
    "churchill107": ("丘吉尔第二次世界大战回忆录", "第十六章 爱尔兰自由邦的兴起",
                     "库/文学/散文随笔/丘吉尔世界大战丛书（第一次世界大战回忆录、第二次世界大战回忆录 共计17册） (温斯顿•丘吉尔 (Winston Chu/md/107_第十六章爱尔兰自由邦的兴起.md",
                     ["爱尔兰", "政治", "自治"]),
    "camus03": ("加缪全集", "局外人（1942年）",
                "库/文学/严肃文学小说/加缪全集/md/03_局外人（1942年）.md",
                ["荒诞", "自由", "他人"]),
    "ming03": ("明朝那些事儿 第壹部", "第三章 踏上征途",
               "库/人文社科/历史/当年明月/明朝那些事儿/第壹部朱元璋：从和尚到皇帝/md/03_第三章踏上征途.md",
               ["历史", "权谋", "战争"]),
}

SYS = "你是图书索引员。只输出结果，不加解释。"

PROMPTS = {
 "S0_naive": "书名：《{book}》\n章标题：{title}\n正文节选：\n{text}\n\n请用一句话概括本章讲什么。",
 "S1_prefix": "书名：《{book}》\n章标题：{title}\n正文节选：\n{text}\n\n写一句不超过80字的『本章讲什么』用于检索。必须点出它真正讨论的抽象主题（如自由、权力、正义、孤独、服从），不要复述情节，不要以『本章』开头。",
 "S2_query": "书名：《{book}》\n章标题：{title}\n正文节选：\n{text}\n\n写4条读者可能会问、而本章能回答的问题。每条不超过25字，一行一条，不要编号。",
 "S3_both": "书名：《{book}》\n章标题：{title}\n正文节选：\n{text}\n\n输出5行：第1行以『摘要:』开头，不超过60字，点出其抽象主题；后4行每行以『问:』开头，不超过25字。",
 "S4_free": "书名：《{book}》\n章标题：{title}\n正文节选：\n{text}\n\n请用150字左右总结本章内容。",
}

BATCH_PROMPT = """下面有 {n} 个章节。为每个章节输出一行，格式严格为：
<编号>|摘要（不超过60字，点出抽象主题）|问题1;问题2;问题3（每条不超过20字）

{blocks}"""

CJK = re.compile(r"[\u4e00-\u9fff]+")

def read_text(rel, limit):
    p = os.path.join(ROOT, rel)
    t = open(p, encoding="utf-8", errors="ignore").read()
    if t.startswith("---"):
        i = t.find("\n---", 3)
        if i > 0:
            t = t[i + 4:]
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]

def call(prompt, level, max_out=400, timeout=120):
    key = json.load(open(AUTH))["commandcode"]["key"]
    body = {"model": MODEL,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": SYS + "\n\n" + prompt}]}],
            "max_output_tokens": max_out,
            "reasoning": {"effort": level},
            "store": False}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "x-command-code-version": "1.14.0", "x-cli-environment": "production",
                 "x-project-slug": "books", "User-Agent": "cli"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    txt = "".join(c.get("text", "") for o in d.get("output", []) if o.get("type") == "message"
                  for c in o.get("content", []) if c.get("type") == "output_text")
    return txt, d.get("usage", {}), time.time() - t0

def bigrams(s):
    return {s[i:i+2] for i in range(len(s)-1) if CJK.fullmatch(s[i]) and CJK.fullmatch(s[i+1])}

def score(text, src, targets):
    bg = bigrams(text)
    cov = (len(bg & bigrams(src)) / len(bg)) if bg else 0.0
    hits = [w for w in targets if w in text]
    return {"chars": len(text), "inject": 1 if hits else 0, "hits": hits, "bigram_cov": round(cov, 3)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="low,high")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results.jsonl"))
    ap.add_argument("--trunc", type=int, default=1200)
    ap.add_argument("--strategies", default="S0_naive,S1_prefix,S2_query,S3_both,S4_free")
    a = ap.parse_args()
    levels = a.levels.split(",")
    strategies = a.strategies.split(",")
    lock = threading.Lock()
    out = open(a.out, "a", encoding="utf-8")
    n = 0

    jobs = []
    for cid, (book, title, rel, targets) in BOOKS.items():
        src = read_text(rel, 5000)  # 用于 bigram 覆盖率的参照（比截断宽）
        text = src[:a.trunc]
        for st in strategies:
            for lv in levels:
                jobs.append({"kind": "single", "cid": cid, "book": book, "title": title,
                             "st": st, "lv": lv, "trunc": a.trunc,
                             "prompt": PROMPTS[st].format(book=book, title=title, text=text),
                             "src": src, "targets": targets})
    # 截断消融：三个章节 × 400/2000
    for cid in ("liuqing029", "farm21", "wuhe05"):
        book, title, rel, targets = BOOKS[cid]
        src = read_text(rel, 6000)
        for trunc in (400, 2000):
            for lv in levels:
                jobs.append({"kind": "trunc", "cid": cid, "book": book, "title": title,
                             "st": "S3_both", "lv": lv, "trunc": trunc,
                             "prompt": PROMPTS["S3_both"].format(book=book, title=title, text=src[:trunc]),
                             "src": src, "targets": targets})
    # 批量：5 章一次
    ids = list(BOOKS)[:5]
    for lv in levels:
        blocks = []
        for i, cid in enumerate(ids, 1):
            book, title, rel, _ = BOOKS[cid]
            blocks.append(f"{i}｜《{book}》{title}｜正文：{read_text(rel, 20000)[:800]}")
        jobs.append({"kind": "batch", "cid": "+".join(ids), "book": "batch", "title": "-",
                     "st": "B_batch5", "lv": lv, "trunc": 800,
                     "prompt": BATCH_PROMPT.format(n=len(ids), blocks="\n\n".join(blocks)),
                     "src": "", "targets": []})

    def run(j):
        nonlocal n
        for attempt in range(2):
            try:
                txt, usage, dt = call(j["prompt"], j["lv"], max_out=4000 if j["kind"] == "batch" else 2500)
                rec = {"kind": j["kind"], "cid": j["cid"], "st": j["st"], "lv": j["lv"],
                       "trunc": j["trunc"],
                       "in": usage.get("input_tokens"), "out": usage.get("output_tokens"),
                       "reason": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
                       "sec": round(dt, 2), "text": txt}
                if j["kind"] == "single" or j["kind"] == "trunc":
                    rec.update(score(txt, j["src"], j["targets"]))
                with lock:
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush(); n += 1
                return rec
            except Exception as e:
                if attempt == 1:
                    with lock:
                        out.write(json.dumps({"kind": j["kind"], "cid": j["cid"], "st": j["st"],
                                              "lv": j["lv"], "error": str(e)[:300]}, ensure_ascii=False) + "\n")
                    return None
                time.sleep(2)

    with ThreadPoolExecutor(a.concurrency) as ex:
        list(ex.map(run, jobs))
    out.close()
    print("done", n, "records ->", a.out)

if __name__ == "__main__":
    main()
