#!/usr/bin/env python3
"""定义/段落级检索：两阶段。

阶段1 rg -l 用词表把所有命中文件缩到很小（rg 扫 400MB 约 30ms）
阶段2 只在这些文件里做段落切分 + 概念分组覆盖度打分，输出 top-N

用法:
  defpass.py ROOT -g 黑暗森林,森林 -g 猎人 -g 开枪,消灭,清除 -g 费米
  defpass.py ROOT -g 猜疑链 -g 怎样想 -g 魔鬼 -n 3
每个 -g 是「一组同义/近义词」，一段里命中不同组的数量越多分越高（组覆盖 > 词频）。
"""

import argparse
import math
import os
import pathlib
import re
import subprocess
import sys


def rg_candidates(root, terms):
    # 全部组的词 OR 成一个正则粗筛，宁滥勿缺：组多时 rg 会慢，收窄交给阶段2打分
    pat = "|".join(re.escape(t) for t in terms)
    r = subprocess.run(["rg", "-l", "-e", pat, root], capture_output=True, text=True)
    # rg 用 1 表示没有命中，2 及以上是路径错一类的真错误，不能当成 0 命中
    if r.returncode not in (0, 1):
        sys.exit("rg 退出码 %d：%s" % (r.returncode, (r.stderr or "").strip()))
    return [line for line in r.stdout.splitlines() if line]


FRONT_MATTER_KEY = re.compile(r"^[A-Za-z_][\w-]*:\s*\S")


def front_matter_end(lines):
    """front matter 闭合行的下标（0-based）。不是 front matter 返回 None。

    只认文件开头、40 行内闭合、块里至少有一个 key: value 的块。脚注分隔线
    没有 key: value，不会被当成 front matter。
    """
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, min(40, len(lines))):
        if lines[i].strip() == "---":
            return i if any(FRONT_MATTER_KEY.match(x) for x in lines[1:i]) else None
    return None


def content_line(lines, line):
    """把落在 front matter 里的行号挪到块后第一个非空行。"""
    end = front_matter_end(lines)
    if end is None or line >= end + 2:
        return line
    for i in range(end + 1, len(lines)):
        if lines[i].strip():
            return i + 1
    return line


def first_content_line(path):
    """正文第一行的 1-based 行号。没有 front matter 或整篇只有 front matter 时返回 1。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return 1
    return content_line(lines, 1)


def paragraphs(path, keep_front_matter=False):
    """返回 (起始行号, 文本)。空行分段，过短的段向下合并。

    文件开头的 front matter 块不索引。块后同一段里的正文另起一段，从闭合行的
    下一行算起，不按前缀删，免得把正文一起删掉。整篇只有 front matter 时保留
    原段，章节不从索引里消失。keep_front_matter=True 时连块一起返回，给正文没有
    可索引 token 的章兜底。
    """
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore").split("\n")
    except OSError:
        return []
    base = 1
    end = front_matter_end(lines)
    if end is not None and not keep_front_matter and any(x.strip() for x in lines[end + 1 :]):
        base = end + 2
    out, buf, start = [], [], base
    for i, ln in enumerate(lines[base - 1 :], base):
        if ln.strip():
            if not buf:
                start = i
            buf.append(ln)
        else:
            if buf:
                out.append((start, "\n".join(buf)))
                buf = []
    if buf:
        out.append((start, "\n".join(buf)))
    # 合并 < 120 字的短段，避免对话行被切碎
    merged = []
    for s, t in out:
        if merged and len(merged[-1][1]) < 120:
            merged[-1] = (merged[-1][0], merged[-1][1] + "\n" + t)
        else:
            merged.append((s, t))
    return merged


def score(text, groups):
    hit = sum(1 for g in groups if any(w in text for w in g))
    freq = sum(text.count(w) for g in groups for w in g)
    return hit, hit * 100 + math.log1p(freq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("-g", "--group", action="append", required=False)
    ap.add_argument("-c", "--concept", help="概念表键，替代 -g，用同义词组展开")
    ap.add_argument("-n", "--limit", type=int, default=5)
    ap.add_argument("-w", "--width", type=int, default=260)
    a = ap.parse_args()

    exclude = None
    if a.concept:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from concept_expand import expand

        terms, ex = expand(a.concept)
        groups = [terms]
        exclude = re.compile("|".join(map(re.escape, ex))) if ex else None
    else:
        if not a.group:
            ap.error("要 -g 或 --concept")
        groups = [g.split(",") for g in a.group]
    min_hit = 1 if (a.concept or len(groups) == 1) else 2
    files = rg_candidates(a.root, [w for g in groups for w in g])
    results = []
    for f in files:
        for start, txt in paragraphs(f):
            if exclude:
                txt = exclude.sub("", txt)
            hit, sc = score(txt, groups)
            if hit >= min_hit:
                results.append((sc, hit, f, start, txt))
    results.sort(reverse=True, key=lambda x: (x[0], len(x[4])))
    print(f"候选文件 {len(files)} / 命中段 {len(results)}")
    for sc, hit, f, start, txt in results[: a.limit]:
        snippet = re.sub(r"\s+", " ", txt)[: a.width]
        print(f"\n[{hit}/{len(groups)} 组 · {sc:.1f}] {os.path.relpath(f, a.root)}:{start}")
        print(f"  {snippet}")


if __name__ == "__main__":
    main()
