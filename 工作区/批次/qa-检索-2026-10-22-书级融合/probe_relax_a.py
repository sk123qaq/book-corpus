#!/usr/bin/env python3
"""B2.5 停止判据 A 的放宽对照（2026-10-23）。

  probe_relax_a.py

四种口径在同一批 60 题 gold 与 40 题宽词层 heldout 上跑，零模型调用：
  v0   原口径：A = 2 段覆盖全部名词组；C = 3 段覆盖全部组
  v2a  只放宽 A（已采纳）：A = 并集覆盖全部组 + 2 段各覆盖 min(2,n) 组；C 不变
  v3a  A 用 support、C 仍按全组覆盖（中间态，未采纳）
  v3   A 用 support、C 用 support（未采纳；heldout 掉一题 hit@10）

做法是 monkeypatch book_query.hit_rounds，不改生产代码。读数写 stdout；
正式读数在 live/B2.5/summary.tsv，放宽前的留档在 live/B2.5/before-relax-A/。
"""

import csv, os, sqlite3, sys
ROOT="/home/shike/文档/books"
sys.path.insert(0,os.path.join(ROOT,"工作区","脚本"))
sys.path.insert(0,os.path.join(ROOT,"工作区","批次","qa-检索-2026-10-22-书级融合"))
import book_query as BQ, book_score
HERE=os.path.join(ROOT,"工作区","批次","qa-检索-2026-10-22-书级融合")
def rows(path,col):
    out=[]
    for r in csv.DictReader(open(path,encoding="utf-8-sig"),delimiter="\t"):
        b=r.get("target_book")
        books=b.split("|") if b else [ (r["path"].split("/md/")[0][2:] if r["path"].startswith("库/") else r["path"].split("/md/")[0]) ]
        out.append({"qid":r["qid"],"terms":[t.strip() for t in r[col].split(",") if t.strip()],"books":books})
    return out
def make(kind):
    def f(hits,ranking,groups,margin=BQ.MARGIN):
        if not ranking: return False,False,False,0
        t=ranking[0]; own=[r for r in hits if r["book"]==t.book]
        per=[len(book_score.matched_groups(r["text"],groups,[])) for r in own]
        union=len(set(g for r in own for g in groups if g in r["text"]))==len(groups)
        t2=ranking[1].score if len(ranking)>1 else 0.0
        B=((t.score-t2)/t.score if t.score else 0.0)>=margin
        n=len(groups); need=min(2,n)
        cnt=sum(1 for c in per if c>=need); full=sum(1 for c in per if c==n)
        if kind=="v2a": A=union and cnt>=2; C=full>=3
        elif kind=="v3a": A=union and t.support>=2; C=full>=3
        elif kind=="v3": A=union and t.support>=2; C=t.support>=3
        else: A=full>=2; C=full>=3
        return A,B,C,cnt
    return f
def run(rs,con,meta,expand,kind):
    BQ.hit_rounds=make(kind); out=[]
    for row in rs:
        p=BQ.Planner(con,meta,expand,limit=10); plan=p.plan(row["terms"],chapters=False)
        rank=0
        for i,b in enumerate(plan.ranking,1):
            if b.book in row["books"]: rank=i; break
        top1=plan.ranking[0].book if plan.ranking else ""
        out.append({"hit10":int(0<rank<=10),"mrr":1/rank if rank else 0,"rounds":len(plan.rounds),
                    "stop":plan.stop,"top1_ok":int(top1 in row["books"])})
    return out
def summ(rs,label):
    n=len(rs); st={}
    for r in rs: st[r["stop"]]=st.get(r["stop"],0)+1
    print("%-12s hit@10 %.3f MRR %.3f 轮 %.2f top1 %d/%d  %s"%(label,sum(r["hit10"] for r in rs)/n,
        sum(r["mrr"] for r in rs)/n,sum(r["rounds"] for r in rs)/n,sum(r["top1_ok"] for r in rs),n,
        ",".join("%s=%d"%(k,v) for k,v in sorted(st.items()))))
con=sqlite3.connect("file:%s?mode=ro"%book_score.DB,uri=True); meta=book_score.load_meta(); expand=book_score.load_expand(con)
G=rows(os.path.join(HERE,"book_gold.tsv"),"terms"); HH=rows(os.path.join(HERE,"..","qa-检索-2026-10-22-宽词层","heldout.tsv"),"literal")
for label,RS in (("gold",G),("heldout",HH)):
    print("==",label)
    for k in ("v0","v2a","v3a","v3"): summ(run(RS,con,meta,expand,k),k)
