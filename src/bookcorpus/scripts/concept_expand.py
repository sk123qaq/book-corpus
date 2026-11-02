#!/usr/bin/env python3
"""概念表展开：键 → 同义词组 + 排除正则。defpass 用它；qsearch 自己解析一遍概念表（见 qsearch.load_concept）。

  concept_expand.py --key 自由
  concept_expand.py --audit          # 每概念语料命中文件数、零命中的别名
"""
import argparse, csv, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L  # noqa: E402

TABLE = os.path.join(L.CAT, "概念表.tsv")
RG = L.RG


def load():
    out = {}
    with open(TABLE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            aliases = [a for a in r["aliases"].split(",") if a]
            exclude = [x for x in r["exclude"].split(",") if x and x != "无"]
            out[r["key"]] = {"aliases": aliases, "exclude": exclude}
    return out


def expand(key):
    rec = load().get(key)
    if rec is None:
        sys.exit("概念表里没有 " + key)
    return [key] + rec["aliases"], rec["exclude"]


def count_files(terms):
    # README.md 是每本的目录页，不是正文，命中不算
    cmd = [RG, "-l", "-F", "-g", "*.md", "-g", "!README.md"]
    for t in terms:
        cmd += ["-e", t]
    cmd.append(L.LIB)
    env = dict(os.environ)
    rg_dir = os.path.dirname(RG)
    if rg_dir:
        env["PATH"] = rg_dir + ":" + env.get("PATH", "")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return len([l for l in r.stdout.splitlines() if l])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key")
    ap.add_argument("--audit", action="store_true")
    a = ap.parse_args()
    table = load()
    if a.key:
        groups, exclude = expand(a.key)
        print("groups:", " | ".join(groups))
        print("exclude:", "|".join(exclude) or "-")
        print("files:", count_files(groups))
        return
    if a.audit:
        print("key\tfiles\tdead_aliases")
        for key, rec in table.items():
            terms = [key] + rec["aliases"]
            n = count_files(terms)
            dead = [al for al in rec["aliases"] if count_files([al]) == 0]
            print("%s\t%d\t%s" % (key, n, ",".join(dead) or "-"))
        return
    ap.error("要 --key 或 --audit")


if __name__ == "__main__":
    main()
