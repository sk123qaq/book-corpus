#!/usr/bin/env python3
"""B2 新普通层：20 道 6-499 章盲写题的独立读数。只读。"""
import csv, hashlib, os, sqlite3, sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT,"工作区","脚本")); sys.path.insert(0,HERE)
import book_eval, book_score
GOLD=os.path.join(HERE,"book_gold-heldout.tsv")
FREEZE=os.path.join(HERE,"book_gold-heldout.freeze.txt")
OUT=os.path.join(HERE,"live","B2-heldout")

def main():
    os.makedirs(OUT,exist_ok=True)
    raw=open(GOLD,"rb").read(); sha=hashlib.sha256(raw).hexdigest()
    fr=open(FREEZE,encoding="utf-8").read()
    assert sha in fr, "freeze sha 不匹配"
    rows=book_eval.read_gold(GOLD)
    assert len(rows)==20 and {r["stratum"] for r in rows}=={"ordinary"}
    meta=book_score.load_meta()
    con=sqlite3.connect("file:%s?mode=ro"%book_score.DB,uri=True)
    indexed={r[0] for r in con.execute("SELECT DISTINCT path FROM para")}
    for r in rows:
        assert os.path.exists(os.path.join(ROOT,r["path"])), r["path"]
        assert r["path"] in indexed, r["path"]
        m=meta.get(r["target_book"])
        assert m and 6<=m.chapters<=499, (r["target_book"], m.chapters if m else None)
    per,hitstats=book_eval.run(rows,con,meta,book_score.load_expand(con))
    con.close()
    schemes=[]
    for scheme in book_eval.SCHEME_ORDER:
        rs=[x for x in per if x["scheme"]==scheme]
        mean=lambda k: sum(x[k] for x in rs)/len(rs)
        schemes.append({"scheme":scheme,"n":len(rs),"hit10":round(mean("hit10"),4),
                        "hit20":round(mean("hit20"),4),"mrr":round(mean("mrr"),4),
                        "ev10":round(mean("ev10"),4),
                        "in_book_top3":round(mean("in_book_top3"),4)})
    # 这批全是 6-499 章；single_hit 不适用，保留 0 行说明
    with open(os.path.join(OUT,"summary.tsv"),"w",encoding="utf-8",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=list(schemes[0]),delimiter="\t",lineterminator="\n")
        w.writeheader(); w.writerows(schemes)
    with open(os.path.join(OUT,"eval.tsv"),"w",encoding="utf-8",newline="") as fh:
        cols=["qid","scheme","book_rank","hit10","hit20","mrr","ev_rank","ev10",
              "in_book_top3","best_ok","hits"]
        w=csv.DictWriter(fh,fieldnames=cols,delimiter="\t",lineterminator="\n",
                         extrasaction="ignore")
        w.writeheader(); w.writerows(per)
    print("heldout n=%d sha=%s"%(len(rows),sha[:12]))
    print("%-20s %6s %6s %6s %6s"%("scheme","hit10","hit20","mrr","ev10"))
    for s in schemes:
        print("%-20s %.3f  %.3f  %.3f  %.3f"%(s["scheme"],s["hit10"],s["hit20"],s["mrr"],s["ev10"]))
    print("single_hit: 本层无单章书（6-499 章），不适用；原 60 题的 single_hit 另见审计。")
    print("written",OUT)

if __name__=="__main__": main()
