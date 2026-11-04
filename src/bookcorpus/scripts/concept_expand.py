#!/usr/bin/env python3
"""概念表展开：键 → 同义词组 + 排除正则。defpass 用它；qsearch 自己解析一遍概念表（见 qsearch.load_concept）。

concept_expand.py --key 自由
concept_expand.py --audit          # 每概念语料命中文件数、零命中的别名
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib_common as L

RG = L.RG


def load():
    out = {}
    for r in L.concept_rows():
        out[r["key"]] = {"aliases": L.split_aliases(r["aliases"]), "exclude": L.split_exclude(r.get("exclude", ""))}
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
    cmd.append(L.BOOKS)
    env = dict(os.environ)
    rg_dir = os.path.dirname(RG)
    if rg_dir:
        env["PATH"] = rg_dir + ":" + env.get("PATH", "")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return len([line for line in r.stdout.splitlines() if line])


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
