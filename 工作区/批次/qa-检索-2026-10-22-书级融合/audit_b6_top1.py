#!/usr/bin/env python3
"""60 题默认 top1 vs B2 best_support top1。只读，写 live/B6/top1.tsv。"""
import csv, os, sqlite3, sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT,"工作区","脚本")); sys.path.insert(0,HERE)
import book_cards as BC, book_query as BQ, book_score as BS
OUT=os.path.join(HERE,"live","B6","top1.tsv")
def main():
    con=sqlite3.connect("file:%s?mode=ro"%BS.DB,uri=True); meta=BS.load_meta(); exp=BS.load_expand(con)
    rows=[]; diff=0
    for row in BC.gold_rows():
        p=BQ.Planner(con,meta,exp,scheme="best_support",limit=5)
        plan=p.plan(row["terms"],chapters=True)
        b2_hits,_=p.search(row["terms"]); b2=BS.rank(b2_hits,row["terms"],"best_support",[],meta,exp)
        b2_top=b2[0].book if b2 else ""
        loop_top=plan.chapters[0]["book"] if plan.chapters else ""
        cs,hits,_,_=BC.cards_for(p,row["terms"],row["question"],6)
        default_top=loop_top
        if cs.trigger:
            keep=set(cs.default_selection)
            chapters=[b for b in plan.chapters if b["book"] in keep]
            have={b["book"] for b in chapters}
            missing=[c for c in cs.cards if c.book in keep and c.book not in have]
            if missing:
                chapters += BC.next_books(missing, hits, row["terms"], 3)
            order={b:i for i,b in enumerate(cs.default_selection)}
            chapters.sort(key=lambda b: order.get(b["book"],99))
            default_top=chapters[0]["book"] if chapters else ""
        d=int(default_top!=b2_top); diff+=d
        rows.append({"qid":row["qid"],"stratum":row["stratum"],"trigger":int(cs.trigger),
                     "b2_top":b2_top,"loop_top":loop_top,"default_top":default_top,
                     "diff":d})
    con.close(); os.makedirs(os.path.dirname(OUT),exist_ok=True)
    with open(OUT+".tmp","w",encoding="utf-8",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=list(rows[0]),delimiter="\t",lineterminator="\n"); w.writeheader(); w.writerows(rows)
    os.replace(OUT+".tmp",OUT)
    print("default vs B2 differences",diff,"of",len(rows))
    for r in rows:
        if r["diff"]: print(r["qid"],"B2",r["b2_top"],"| default",r["default_top"],"| loop",r["loop_top"],"trigger",r["trigger"])
    print("written",OUT)
if __name__=="__main__": main()
