#!/usr/bin/env python3
"""审计 book_query -n 3 时 next_books 缺 book_score 的崩溃路径。只读。"""
import os, sqlite3, sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT,"工作区","脚本")); sys.path.insert(0,HERE)
import book_cards as BC, book_query as BQ, book_score as BS
con=sqlite3.connect("file:%s?mode=ro"%BS.DB,uri=True); meta=BS.load_meta(); exp=BS.load_expand(con)
bad=[]
for row in BC.gold_rows():
    p=BQ.Planner(con,meta,exp,limit=3); plan=p.plan(row["terms"],chapters=True)
    cs,_,_,_=BC.cards_for(p,row["terms"],row["question"],6)
    if cs.trigger:
        keep=set(cs.default_selection)
        have={b["book"] for b in plan.chapters if b["book"] in keep}
        missing=[c for c in cs.cards if c.book in keep and c.book not in have]
        if missing: bad.append((row["qid"],row["terms"],row["question"],[c.book for c in missing]))
print("queries with missing cards",len(bad))
for x in bad[:10]: print(x)
if bad:
    print("REPRO:", "python3 工作区/脚本/book_query.py", " ".join(bad[0][1]), "--question", repr(bad[0][2]), "-n 3")
con.close()
