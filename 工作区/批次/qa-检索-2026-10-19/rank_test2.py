#!/usr/bin/env python3
"""端到端：加了扩展文本后，改写问法能否把目标章排进前 3。100 干扰章 × 27 问。"""
import json, os, random, re, sqlite3
ROOT = os.path.expanduser("~/文档/books"); BENCH = os.path.dirname(os.path.abspath(__file__))

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
Q = {
 "liuqing029": ["为什么现代人觉得自己不自由", "人被当成机器零件是怎么回事", "效率为什么会压垮人的尊严"],
 "liuqing072": ["人为什么会盲从，怎么才能自己拿主意", "艾希曼为什么会丧失思考", "服从法律为什么可能作恶"],
 "wuhe05": ["人在群体里为什么会失去自控", "群体为什么容易冲动轻信", "群体道德为什么忽高忽低"],
 "farm21": ["革命成功以后权力为什么会变质", "猪和人为什么最后分不清", "平等口号为什么成了特权"],
 "n1984_08": ["一个人独处为什么变得危险", "为什么要监视党员的一举一动", "孤独为什么被当成罪"],
 "xiangtu10": ["礼治为什么不是自由", "乡土社会靠什么维持秩序", "打官司为什么在乡下丢脸"],
 "churchill107": ["爱尔兰的政治妥协是怎么谈成的", "英国为什么和爱尔兰叛军谈判", "自由邦是怎么建立的"],
 "camus03": ["人为什么会觉得活着没意义", "母亲死了为什么不哭", "社会为什么审判一个冷漠的人"],
 "ming03": ["元末农民为什么起义", "朱元璋为什么要造反", "灾荒为什么会引发叛乱"],
}
CJK = re.compile(r"[\u4e00-\u9fff]+"); ASCII = re.compile(r"[A-Za-z0-9]+")
def toks(s):
    out=[]
    for run in CJK.findall(s):
        out.extend(run[i:i+2] for i in range(len(run)-1))
        if len(run)==1: out.append(run)
    out.extend(w.lower() for w in ASCII.findall(s)); return out
def clean(rel, limit=3000):
    t=open(os.path.join(ROOT,rel),encoding="utf-8",errors="ignore").read()
    if t.startswith("---"):
        i=t.find("\n---",3)
        if i>0: t=t[i+4:]
    return re.sub(r"\s+"," ",t).strip()[:limit]
def decoys(n=100, seed=11):
    rnd=random.Random(seed); pool=[]; seen=set()
    for dp,_,fns in os.walk(os.path.join(ROOT,"库")):
        if os.path.basename(dp)!="md": continue
        for fn in fns:
            if not fn.endswith(".md"): continue
            p=os.path.join(dp,fn)
            if os.path.getsize(p)<4000: continue
            rel=os.path.relpath(p,ROOT)
            if rel in TARGETS.values(): continue
            k=os.path.dirname(dp)
            if k in seen: continue
            seen.add(k); pool.append(rel)
    rnd.shuffle(pool); return pool[:n]

def exps():
    e={}
    for fn,key in [("results-v2.jsonl",None),("results-s1-trunc.jsonl","trunc")]:
        for line in open(os.path.join(BENCH,fn)):
            r=json.loads(line)
            if "error" in r: continue
            if r["kind"]=="single":
                k = f"S1@{r['trunc']}" if r["st"]=="S1_prefix" else r["st"]+"-"+r["lv"]
                e.setdefault((k,r["cid"]), r["text"])
            if r["kind"]=="batch":
                for ln in r["text"].splitlines():
                    m=re.match(r"\s*(\d+)[|｜]", ln)
                    if not m: continue
                    ids=r["cid"].split("+"); i=int(m.group(1))-1
                    if 0<=i<len(ids):
                        e.setdefault(("B_batch-"+r["lv"], ids[i]), ln.split("|",1)[-1].replace("｜","|"))
    return e

VARIANTS=[("裸原文",None),("S1@400","S1@400"),("S1@800","S1@800"),("S1@1200","S1_prefix-low"),
          ("S1@2400","S1@2400"),("S2伪查询","S2_query-low"),("S3两者","S3_both-low"),
          ("S4自由摘要","S4_free-low"),("批量5章-low","B_batch-low"),("批量5章-high","B_batch-high")]

def build(db, docs, extra):
    if os.path.exists(db): os.remove(db)
    con=sqlite3.connect(db)
    con.execute("create virtual table fts using fts5(cid, body, tokenize='unicode61')")
    for cid,text in docs:
        add=extra.get(cid,"") if extra else ""
        con.execute("insert into fts(cid,body) values (?,?)",(cid," ".join(toks(text+("\n"+add if add else "")))))
    con.commit(); con.close()
def run(db,q):
    con=sqlite3.connect(db)
    rows=con.execute("select cid,bm25(fts) s from fts where fts match ? order by s limit 5",
                     (" OR ".join(toks(q)),)).fetchall()
    con.close(); return [r[0] for r in rows]

def main():
    docs=[(c,clean(r)) for c,r in TARGETS.items()]+[("d%03d"%i,clean(r)) for i,r in enumerate(decoys(100))]
    e=exps()
    print(f"{'语料':<14}{'top1':>6}{'top3':>6}{'@5':>5}{'均排名':>8}   未进前5")
    rowsout={}
    for name,key in VARIANTS:
        extra=None if key is None else {c:e.get((key,c),"") for c in TARGETS}
        if key and not any(extra.values()): print(f"{name:<14} 跳过（无产出）"); continue
        db=os.path.join(BENCH,"_rt.db"); build(db,docs,extra)
        rk={}
        for cid,qs in Q.items():
            r=[99]*len(qs)
            for i,q in enumerate(qs):
                got=run(db,q)
                if cid in got: r[i]=got.index(cid)+1
            rk[cid]=r
        os.remove(db)
        flat=[v for vs in rk.values() for v in vs]
        t1=sum(1 for v in flat if v==1); t3=sum(1 for v in flat if v<=3); t5=sum(1 for v in flat if v<=5)
        rowsout[name]=rk
        miss=[c for c,vs in rk.items() if min(vs)>5]
        print(f"{name:<14}{t1:>6}{t3:>6}{t5:>5}{sum(flat)/len(flat):>8.2f}   {','.join(miss) or '-'}")
    # 逐章最差查询
    print("\n逐章平均排名（3 个问法）")
    print(f"{'章':<13}"+"".join(f"{n:<14}" for n,_ in VARIANTS if n in rowsout))
    for cid in Q:
        print(f"{cid:<13}"+"".join(f"{sum(rowsout[n][cid])/3:<14.1f}" for n,_ in VARIANTS if n in rowsout))

if __name__=="__main__": main()
