"""P1.5 复测：三种「强制词汇外扩」的提示，看伪查询是不是被提示词废掉的。

用法：python3 pq_reprompt.py            # 生成 + 落 gen_pq.json + 打印 token/新词率
      python3 pq_reprompt.py --rank     # 生成产物送进 rank_test2 的同一套 27 问
"""
import json, os, re, sys, time, urllib.request

ns = {"__file__": os.path.abspath("expand_bench.py")}
exec(open("expand_bench.py").read(), ns)
BOOKS, ROOT, AUTH = ns["BOOKS"], ns["ROOT"], ns["AUTH"]
API = "https://api.commandcode.ai/provider/v1/responses"
MODEL = "deepseek/deepseek-v4.1-flash"

TOPICS = ["自主与自由", "权力与支配", "道德与责任", "群体与非理性", "历史与政治",
          "孤独与疏离", "生死与意义", "家庭与亲情", "法律与审判", "劳动与贫困"]

PROMPTS = {
    "A_搜索框": """读者没读过下面这章，只会用自己脑子里的词在搜索框里找它。请写 5 条检索式，每条不超过 20 字。
硬性要求：
1. 每条至少含 2 个正文里根本没出现的词（同义词、上位概念、学术用语、大白话都行）；
2. 不许照抄正文的专名和术语；
3. 写读者真的会打的字，不写「为什么……？」这种考卷问句。

章节标题：{title}
正文：{text}""",

    "B_hyde": """用普通读者的话写一段 150 字左右的短文，说明「这一章在讲人的什么处境」。
不要用正文里的术语和专名，可以提到它讲的事，但必须换成你自己的说法。

章节标题：{title}
正文：{text}""",

    "C_主题约束": """下面是一章的正文。现有主题候选：{topics}。
请为这章写 5 条检索式，合起来覆盖它真正涉及的那几个主题。要求：
1. 每条都直接用主题候选里的词，用来替换正文自己的说法；
2. 每条不超过 20 字，不写考卷问句；
3. 不许出现正文里的专名。

章节标题：{title}
正文：{text}""",
}


KEY = json.load(open(AUTH))["commandcode"]["key"]


def post(prompt, max_out=2500):
    body = {"model": MODEL,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "你是图书索引员。只输出结果，不加解释。\n\n" + prompt}]}],
            "max_output_tokens": max_out, "reasoning": {"effort": "low"}, "store": False}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + KEY, "Content-Type": "application/json",
        "x-command-code-version": "1.14.0", "x-cli-environment": "production",
        "x-project-slug": "books", "User-Agent": "cli"})
    t0 = time.time()
    d = json.load(urllib.request.urlopen(req, timeout=300))
    txt = "".join(c.get("text", "") for o in d.get("output", []) if o.get("type") == "message"
                  for c in o.get("content", []) if c.get("type") == "output_text")
    return txt.strip(), d["usage"], time.time() - t0


def body(rel, limit=1200):
    t = open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore").read()
    if t.startswith("---"):
        i = t.find("\n---", 3)
        if i > 0:
            t = t[i + 4:]
    return re.sub(r"\s+", " ", t).strip()[:limit]


def bg(s):
    return {s[i:i + 2] for i in range(len(s) - 1) if not re.search(r"\s", s[i:i + 2])}


def generate():
    out, stats = {}, {}
    for name, tpl in PROMPTS.items():
        tok_in = tok_out = 0
        for cid, (book, title, rel, _) in BOOKS.items():
            text = body(rel)
            p = tpl.format(title=title, text=text, topics="、".join(TOPICS))
            txt, u, dt = post(p)
            out.setdefault(cid, {})[name] = txt
            stats.setdefault(name, []).append((cid, txt, u, dt, text))
            tok_in += u["input_tokens"]
            tok_out += u["output_tokens"]
        print(f"{name:<10} 9 章: in={tok_in} out={tok_out} 每章={round((tok_in + tok_out) / 9)}tok "
              f"{(stats[name][0][3]) if stats[name] else 0:.1f}s/章")
    for name in PROMPTS:
        rows = stats[name]
        rat = [(cid, len(bg(txt) - bg(text)) / max(1, len(bg(txt)))) for cid, txt, u, dt, text in rows]
        print(f"{name:<10} 新 bigram 占比 均值={sum(v for _, v in rat) / len(rat):.0%}  "
              + " ".join(f"{c}:{v:.0%}" for c, v in rat))
    json.dump(out, open("gen_pq.json", "w"), ensure_ascii=False, indent=1)
    for cid, d in list(out.items())[:2]:
        print(f"\n[{cid}]")
        for k, v in d.items():
            print(f"  {k}: {v[:260]}".replace("\n", " ⏎ "))
    return out


def rank(extra_map):
    ns2 = {"__file__": os.path.abspath("rank_test2.py")}
    exec(open("rank_test2.py").read().split("def main()")[0], ns2)
    docs = [(c, ns2["clean"](r)) for c, r in ns2["TARGETS"].items()] + \
           [("d%03d" % i, ns2["clean"](r)) for i, r in enumerate(ns2["decoys"](100))]
    for name, extra in extra_map.items():
        db = "_pq.db"
        ns2["build"](db, docs, extra)
        flat, per = [], {}
        for cid, qs in ns2["Q"].items():
            per[cid] = []
            for q in qs:
                got = ns2["run"](db, q)
                r = got.index(cid) + 1 if cid in got else 99
                per[cid].append(r)
                flat.append(r)
        os.remove(db)
        print(f"{name:<26} top1={sum(1 for v in flat if v == 1):>2}/27  top3={sum(1 for v in flat if v <= 3):>2}/27  "
              f"@5={sum(1 for v in flat if v <= 5):>2}/27  均排名={sum(flat) / len(flat):.1f}")
        print("   ", {k: round(sum(v) / 3, 1) for k, v in per.items()})


if __name__ == "__main__":
    gen = generate() if not (len(sys.argv) > 1 and sys.argv[1] == "--rank") else json.load(open("gen_pq.json"))
    rank({"裸原文": None,
          "+A_搜索框": {c: d["A_搜索框"] for c, d in gen.items()},
          "+B_hyde": {c: d["B_hyde"] for c, d in gen.items()},
          "+C_主题约束": {c: d["C_主题约束"] for c, d in gen.items()},
          "+前缀批5@800(旧赢家)": json.load(open("batch_prefix.json")),
          "+S2_旧伪查询": None})
