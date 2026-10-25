#!/usr/bin/env python3
"""审计 --book-score mix 在书卡默认开时是否仍是 mix 排序。只读。"""
import os, sqlite3, sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT,"工作区","脚本")); sys.path.insert(0,HERE)
import book_cards as BC, book_query as BQ, book_score as BS
def main():
    con=sqlite3.connect("file:%s?mode=ro"%BS.DB,uri=True); meta=BS.load_meta(); exp=BS.load_expand(con)
    diff=[]
    for row in BC.gold_rows():
        p=BQ.Planner(con,meta,exp,scheme="mix",limit=5); plan=p.plan(row["terms"],chapters=True)
        no=plan.chapters[0]["book"] if plan.chapters else ""
        cs,_,_,_=BC.cards_for(p,row["terms"],row["question"],6)
        withc=no
        if cs.trigger:
            keep=set(cs.default_selection)
            filtered=[b for b in plan.chapters if b["book"] in keep]
            withc=filtered[0]["book"] if filtered else ""
        if no!=withc: diff.append((row["qid"],cs.trigger,no,withc,cs.default_selection))
    print("differences",len(diff),"of",len(BC.gold_rows()))
    for x in diff[:10]: print(x)
    con.close()
if __name__=="__main__": main()
