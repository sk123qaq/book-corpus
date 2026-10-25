#!/usr/bin/env python3
"""伪查询生成质量审计：只读 index.db 的 expand(kind=queries) 与库内章节，量四类指标。

  python3 audit_queries_quality.py [--sample N]

指标：
1. 形态：每行条数、单条字数（提示词限 20 字）、空行/退化行；
2. 新词率：生成 bigram 里正文（全章）没有的占比（P1.5 报告的基线：零样本 53%、少样本+硬约束 89%）；
3. 提示词规则：考卷问句（为什么/怎么/什么 开头）占比（规则 3 说不写，正例自己又带，只记数）；
4. 重复：同条目跨章重复、行内重复。

按 model 分组输出，深seek（旧 101 行）与 mimo（本次）对照。
"""
import argparse, os, random, re, sqlite3, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "工作区", "脚本"))
import lib_fts_cjk as cjk

DB = os.path.join(ROOT, "目录", "检索", "index.db")


def body_of(path, chars=0):
    t = open(os.path.join(ROOT, path), encoding="utf-8", errors="ignore").read()
    if t.startswith("---"):
        i = t.find("\n---", 3)
        if i > 0:
            t = t[i + 4:]
    t = re.sub(r"!\[\]\([^)]*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:chars] if chars else t


def items(text):
    parts = re.split(r"\s*(?:^|(?<=\s))\d+[\.、]\s*", text)
    return [p.strip(" 。；") for p in parts if p.strip(" 。；")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    a = ap.parse_args()
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows = con.execute("SELECT path, text, model FROM expand WHERE kind='queries'").fetchall()
    stat = collections.defaultdict(lambda: collections.defaultdict(list))
    seen = collections.defaultdict(lambda: collections.defaultdict(list))
    for path, text, model in rows:
        its = items(text)
        b, bx = body_of(path), body_of(path, 800)
        gt = set(cjk.tokens(text))
        nov = len(gt - set(cjk.tokens(b))) / max(1, len(gt))
        novx = len(gt - set(cjk.tokens(bx))) / max(1, len(gt))
        s = stat[model or "?"]
        s["rows"].append(1)
        s["n_items"].append(len(its))
        s["novelty"].append(nov)
        s["novelty_excerpt"].append(novx)
        s["max_len"].append(max((len(x) for x in its), default=0))
        s["exam"].append(sum(1 for x in its if re.match(r"^(为什么|怎么|什么|如何|为何)", x)))
        s["short_row"].append(1 if len(text) < 30 else 0)
        s["in_row_dup"].append(len(its) - len(set(its)))
        for x in its:
            seen[model or "?"][x].append(path)
    for model in sorted(stat):
        s = stat[model]
        n = len(s["rows"])
        dups = sum(len(v) - 1 for v in seen[model].values() if len(v) > 1)
        print("== %s == 行数 %d" % (model, n))
        print("  条数/行  中位 %.0f  最少 %d  最多 %d" % (
            sorted(s["n_items"])[n // 2], min(s["n_items"]), max(s["n_items"])))
        print("  新词率(全章)   均值 %.3f  中位 %.3f  最低 %.3f" % (
            sum(s["novelty"]) / n, sorted(s["novelty"])[n // 2], min(s["novelty"])))
        print("  新词率(模型所见800字) 均值 %.3f  中位 %.3f" % (
            sum(s["novelty_excerpt"]) / n, sorted(s["novelty_excerpt"])[n // 2]))
        print("  单条最长 中位 %d  超20字条数 %d" % (
            sorted(s["max_len"])[n // 2], sum(1 for x in s["max_len"] if x > 20)))
        print("  考卷问句 占条目 %.0f%%" % (100 * sum(s["exam"]) / max(1, sum(s["n_items"]))))
        print("  空/短行 %d  行内重复条 %d  跨章重复条目 %d（多出的份数）" % (
            sum(s["short_row"]), sum(s["in_row_dup"]), dups))
        top = sorted(seen[model].items(), key=lambda kv: -len(kv[1]))[:3]
        for txt, paths in top:
            if len(paths) > 1:
                print("    重复例: %s ×%d" % (txt[:28], len(paths)))
    if a.sample:
        random.seed(11)
        for path, text, model in random.sample(rows, min(a.sample, len(rows))):
            print("--", model.split("/")[0], path.split("/md/")[-1][:40])
            print("  ", text[:150])


if __name__ == "__main__":
    main()
