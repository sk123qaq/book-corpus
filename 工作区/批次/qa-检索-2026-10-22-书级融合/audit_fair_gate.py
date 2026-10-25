#!/usr/bin/env python3
"""审计 B5/B6 门槛口径：卡片 top6 vs 自动只算 top1 是否公平。
只读，不写库；输出 live/B6/audit-fair-gate.tsv。"""
import csv, os, sqlite3, sys
HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT,"工作区","脚本")); sys.path.insert(0,HERE)
import book_cards as BC, book_query as BQ, book_score as BS

OUT=os.path.join(HERE,"live","B6","audit-fair-gate.tsv")

def chap_hit(book, own, hits, terms, n=3):
    if not book or not own: return 0
    rows=BQ.select_chapters(hits, book, terms, BC.book_readme.readme_toc(book)[0], n)
    return int(any(r["chapter_path"] in own for r in rows))

def plan_hit(plan, own_by_book):
    for b in plan.chapters:
        own=own_by_book.get(b["book"],set())
        if b["selected"] and any(r["chapter_path"] in own for r in b["selected"]):
            return 1
    return 0

def main():
    con=sqlite3.connect("file:%s?mode=ro"%BS.DB, uri=True)
    meta=BS.load_meta(); exp=BS.load_expand(con); rows=BC.gold_rows()
    out=[]
    for row in rows:
        p=BQ.Planner(con,meta,exp,scheme="best_support",limit=5)
        plan=p.plan(row["terms"],chapters=True)
        cs,hits,nouns,rank=BC.cards_for(p,row["terms"],row["question"],6)
        # auto top1 and top5 (noun first round)
        auto_top1 = rank[0].book if rank else ""
        auto_top1_hit = chap_hit(auto_top1,row["per_book"].get(auto_top1,set()),hits,nouns)
        auto_any=0
        for b in rank[:5]:
            auto_any = max(auto_any, chap_hit(b.book,row["per_book"].get(b.book,set()),hits,nouns))
        # loop top1 and top5 (plan.chapters)
        loop_top1 = plan.chapters[0]["book"] if plan.chapters else ""
        loop_top1_hit = 0
        if plan.chapters:
            b=plan.chapters[0]; own=row["per_book"].get(b["book"],set())
            loop_top1_hit=int(any(r["chapter_path"] in own for r in b["selected"]))
        loop_any=plan_hit(plan,row["per_book"])
        # cards top1/top3/top6
        card_top1=card_top3=card_any=0
        if cs.trigger:
            for t in row["books"]:
                if cs.cards and t==cs.cards[0].book:
                    card_top1=max(card_top1,chap_hit(t,row["per_book"].get(t,set()),hits,nouns))
                if t in set(cs.default_selection):
                    card_top3=max(card_top3,chap_hit(t,row["per_book"].get(t,set()),hits,nouns))
                if t in {c.book for c in cs.cards}:
                    card_any=max(card_any,chap_hit(t,row["per_book"].get(t,set()),hits,nouns))
        # loop top1/top3/top5
        def loop_k_hit(k):
            hit=0
            for b in plan.chapters[:k]:
                own=row["per_book"].get(b["book"],set())
                if any(r["chapter_path"] in own for r in b["selected"]): hit=1
            return hit
        loop_top3=loop_k_hit(3); loop_top5=loop_k_hit(5)
        out.append({"qid":row["qid"],"stratum":row["stratum"],"trigger":int(cs.trigger),
                    "auto_top1":auto_top1_hit,"auto_any":auto_any,
                    "loop_top1":loop_top1_hit,"loop_top3":loop_top3,"loop_top5":loop_top5,
                    "card_top1":card_top1,"card_top3":card_top3,"card_any":card_any})
    con.close()
    os.makedirs(os.path.dirname(OUT),exist_ok=True)
    with open(OUT+".tmp","w",encoding="utf-8",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=list(out[0]),delimiter="\t",lineterminator="\n"); w.writeheader(); w.writerows(out)
    os.replace(OUT+".tmp",OUT)
    def mean(xs): return sum(xs)/len(xs) if xs else 0
    trig=[r for r in out if r["trigger"]]
    print("n",len(out),"trigger",len(trig))
    for name,rs in (("all",out),("triggered",trig)):
        print(name, {k:round(mean([r[k] for r in rs]),3) for k in ("auto_top1","auto_any","loop_top1","loop_top3","loop_top5","card_top1","card_top3","card_any")})
    print("written",OUT)

if __name__=="__main__": main()
