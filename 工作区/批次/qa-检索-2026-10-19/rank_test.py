#!/usr/bin/env python3
"""端到端检索测试：加了扩展文本以后，用户的问法能不能把正确章节排进前 3。

语料 = 9 个目标章 + 30 个随机干扰章；查询用「书里没有那些词」的改写问法。
索引 = CJK 二元组（与 P1 的 FTS5 方案同一套切法）。
"""
import json, os, random, re, sqlite3, sys

ROOT = os.path.expanduser("~/文档/books")
BENCH = os.path.dirname(os.path.abspath(__file__))

TARGETS = {
 "liuqing029": "库/人文社科/思想与哲学/刘擎西方现代思想讲义/md/029_08_韦伯IV “现代的铁笼”是怎么锈就的.md",
 "liuqing072": "库/人文社科/思想与哲学/刘擎西方现代思想讲义/md/072_思考的含义：独立判断.md",
 "wuhe05": "库/人文社科/心理与认知/乌合之众/md/05_第二章群体的情感与道德.md",
 "farm21": "库/文学/严肃文学小说/动物农场/md/21_◆◆◆ ◆◆◆.md",
 "n1984_08": "库/文学/严肃文学小说/1984/md/08_第8节.md",
 "xiangtu10": "库/人文社科/人类学与考古/乡土中国/md/10_礼治秩序.md",
 "churchill107": "库/文学/散文随笔/丘吉尔世界大战丛书（第一次世界大战回忆录、第二次世界大战回忆录 共计17册） (温斯顿•丘吉尔 (Winston Chu/md/107_第十六章爱尔兰自由邦的兴起.md",
 "camus03": "库/文学/严肃文学小说/加缪全集/md/03_局外人（1942年）.md",
 "ming03": "库/人文社科/历史/当年明月/明朝那些事儿/第壹部朱元璋：从和尚到皇帝/md/03_第三章踏上征途.md",
}

# 用户会怎么问：刻意避免原文用词
QUERIES = {
 "liuqing029": "为什么现代人觉得自己不自由",
 "liuqing072": "人为什么会盲从，怎么才能自己拿主意",
 "wuhe05": "人在群体里为什么会失去自控",
 "farm21": "革命成功以后权力为什么会变质",
 "n1984_08": "一个人独处为什么变得危险",
 "xiangtu10": "礼治为什么不是自由",
 "churchill107": "爱尔兰的政治妥协是怎么谈成的",
 "camus03": "人为什么会觉得活着没意义",
 "ming03": "元末农民为什么起义",
}

CJK = re.compile(r"[\u4e00-\u9fff]+")
ASCII = re.compile(r"[A-Za-z0-9]+")

def toks(s):
    out = []
    for run in CJK.findall(s):
        out.extend(run[i:i+2] for i in range(len(run)-1))
        if len(run) == 1:
            out.append(run)
    out.extend(w.lower() for w in ASCII.findall(s))
    return out

def clean(rel, limit=3000):
    t = open(os.path.join(ROOT, rel), encoding="utf-8", errors="ignore").read()
    if t.startswith("---"):
        i = t.find("\n---", 3)
        if i > 0: t = t[i+4:]
    return re.sub(r"\s+", " ", t).strip()[:limit]

def sample_decoys(n=30, seed=7):
    rnd = random.Random(seed)
    hits, seen = [], set()
    for dp, _, fns in os.walk(os.path.join(ROOT, "库")):
        if os.path.basename(dp) != "md": continue
        for fn in fns:
            if not fn.endswith(".md"): continue
            p = os.path.join(dp, fn)
            if os.path.getsize(p) < 4000: continue
            rel = os.path.relpath(p, ROOT)
            if any(rel == t for t in TARGETS.values()): continue
            key = os.path.dirname(dp)
            if key in seen: continue
            seen.add(key); hits.append(rel)
    rnd.shuffle(hits)
    return hits[:n]

def expansions():
    """从 bench 结果里抽出每种策略的最佳产出（按章、按策略取 low 档；无 low 取 high）"""
    rows = [json.loads(l) for l in open(os.path.join(BENCH, "results-v2.jsonl")) if "error" not in l]
    exp = {}
    for r in rows:
        if r["kind"] == "single" and r["lv"] != "low": continue
        exp.setdefault((r["st"], r["cid"]), r["text"])
    for r in rows:  # 批量是按批一次的，拆开
        if r["kind"] == "batch" and r["lv"] == "low":
            for line in r["text"].splitlines():
                m = re.match(r"\s*(\d+)[|｜]", line)
                if not m: continue
                idx = int(m.group(1)) - 1
                ids = r["cid"].split("+")
                if 0 <= idx < len(ids):
                    exp[("B_batch5", ids[idx])] = line.split("|", 1)[-1].replace("｜", "|")
    return exp

def build(db, docs, extra):
    if os.path.exists(db): os.remove(db)
    con = sqlite3.connect(db)
    con.execute("create virtual table fts using fts5(cid, body, tokenize='unicode61')")
    for cid, text in docs:
        body = text + ("\n" + extra.get(cid, "") if extra.get(cid) else "")
        con.execute("insert into fts(cid, body) values (?,?)", (cid, " ".join(toks(body))))
    con.commit(); con.close()

def run(db, query):
    con = sqlite3.connect(db)
    q = " OR ".join(toks(query))
    rows = con.execute("select cid, bm25(fts) as s from fts where fts match ? order by s limit 5", (q,)).fetchall()
    con.close()
    return [r[0] for r in rows]

def main():
    docs = [(cid, clean(rel)) for cid, rel in TARGETS.items()]
    docs += [("decoy%02d" % i, clean(rel)) for i, rel in enumerate(sample_decoys(30))]
    exp = expansions()
    variants = [("裸原文", None), ("+S1前缀", "S1_prefix"), ("+S2伪查询", "S2_query"),
                ("+S3两者", "S3_both"), ("+S4自由摘要", "S4_free"), ("+批量5章", "B_batch5")]
    print(f"{'语料':<12}{'top1':>6}{'top3':>6}{'top5':>6}   未进前5的章")
    detail = {}
    for name, st in variants:
        extra = {} if st is None else {cid: exp.get((st, cid), "") for cid in TARGETS}
        db = os.path.join(BENCH, "rank_%s.db" % re.sub(r"\W+", "_", name))
        build(db, docs, extra)
        ranks, miss = {}, []
        for cid, q in QUERIES.items():
            got = run(db, q)
            ranks[cid] = got.index(cid) + 1 if cid in got else 99
            if ranks[cid] > 5: miss.append(cid)
        detail[name] = ranks
        n = len(QUERIES)
        print(f"{name:<12}{sum(1 for v in ranks.values() if v==1):>6}"
              f"{sum(1 for v in ranks.values() if v<=3):>6}"
              f"{sum(1 for v in ranks.values() if v<=5):>6}   {','.join(miss) or '-'}")
        os.remove(db)
    print("\n逐章排名（1 最好，99=未进前5）")
    ids = list(QUERIES)
    print(f"{'章':<13}" + "".join(f"{v:<12}" for v, _ in variants))
    for cid in ids:
        print(f"{cid:<13}" + "".join(f"{detail[v][cid]:<12}" for v, _ in variants))

if __name__ == "__main__":
    main()
