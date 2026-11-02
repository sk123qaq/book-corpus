#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check a folder of chapter Markdown files produced by ebook_to_md.py.

    python3 verify_output.py OUTDIR

Reports file/character/image counts and any real problem: broken relative links,
missing or orphaned images, identical chapter files, missing front matter.
Exit code 1 when problems were found, 0 otherwise.

A link target only counts when its extension names a file the pipeline writes
(Markdown chapters and images). Plain text that happens to look like a link, such as
the formula `[G(R) - G(R)](1 - u)` or a bracketed citation, is not a path and is
not checked.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import sys

IMG_RE = re.compile(r"!\[[^\]]*\]\((?:<([^>]+)>|([^)]+))\)")
LINK_RE = re.compile(r"(?<![!\\])\[[^\]]*\]\((?:<([^>]+)>|([^)]+))\)")
WS_RE = re.compile(r"\s")
SKIP = "http://", "https://", "mailto:", "#", "data:"
# Extensions the converters write: chapters plus images (measured over 1,638 md folders).
FILE_EXT = (".md", ".png", ".jpg", ".jpeg", ".jpx", ".gif", ".svg")


def is_file_ref(target: str) -> bool:
    """A target names a file only when it carries one of the extensions we write.

    `[G(R) - G(R)](1 - u)` and bracketed citations match the link pattern too.
    Checking those as paths fails every book that has formulas in it.
    """
    return os.path.splitext(target)[1].lower() in FILE_EXT


def targets(text: str, pattern) -> list:
    """链接目标，兼容 `(<带括号的路径>)` 与普通 `(path)` 两种写法。"""
    return [a or b for a, b in pattern.findall(text)]


def strip_ws(text: str) -> str:
    return WS_RE.sub("", text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify chapter Markdown output.")
    ap.add_argument("outdir", help="folder written by ebook_to_md.py")
    ap.add_argument(
        "--min-chars",
        type=int,
        default=100,
        help="flag files with less text than this (default: 100, front matter pages are small)",
    )
    args = ap.parse_args(argv)

    root = os.path.abspath(args.outdir)
    if not os.path.isdir(root):
        sys.exit(f"not a directory: {root}")

    files = sorted(f for f in glob.glob(os.path.join(root, "*.md"))
                   if os.path.basename(f) != "README.md")
    problems: list[str] = []
    warnings: list[str] = []
    sizes: list[tuple[int, str]] = []
    seen: dict[str, str] = {}
    referenced_images: set[str] = set()
    total = 0

    for path in files:
        rel = os.path.basename(path)
        text = open(path, encoding="utf-8").read()
        body = text.split("---", 2)[-1] if text.startswith("---") else text
        chars = len(strip_ws(body))
        total += chars
        sizes.append((chars, rel))

        if not text.lstrip("\ufeff").startswith("---"):
            warnings.append(f"{rel}: no YAML front matter")
        if not re.search(r"^#\s", body, re.M):
            problems.append(f"{rel}: no H1 chapter title")
        if chars < args.min_chars:
            warnings.append(f"{rel}: only {chars} chars (front matter page?)")

        digest = hashlib.sha1(body.strip().encode()).hexdigest()
        if digest in seen:
            problems.append(f"{rel}: identical content to {seen[digest]}")
        seen[digest] = rel

        for target in targets(text, IMG_RE):
            if target.startswith(SKIP):
                continue
            referenced_images.add(target)
            if not os.path.exists(os.path.join(root, target)):
                problems.append(f"{rel}: missing image {target}")
        for target in targets(text, LINK_RE):
            if target.startswith(SKIP) or not is_file_ref(target):
                continue
            if not os.path.exists(os.path.join(root, target)):
                problems.append(f"{rel}: broken link -> {target}")

    on_disk: set[str] = set()
    imgdir = os.path.join(root, "images")
    if os.path.isdir(imgdir):
        on_disk = {f"images/{n}" for n in os.listdir(imgdir)}
    for orphan in sorted(on_disk - referenced_images):
        problems.append(f"orphan image on disk: {orphan}")

    print(f"folder      : {root}")
    print(f"markdown    : {len(files)} chapters")
    print(f"characters  : {total:,}")
    print(f"images used : {len(referenced_images)} (on disk: {len(on_disk)})")
    if sizes:
        print("smallest / largest:")
        for chars, rel in sorted(sizes)[:2]:
            print(f"   small {chars:>8,}  {rel}")
        for chars, rel in sorted(sizes)[-2:]:
            print(f"   large {chars:>8,}  {rel}")
    print(f"problems    : {len(problems)}")
    for p in problems[:40]:
        print("   -", p)
    if warnings:
        print(f"notes       : {len(warnings)} (usually fine for cover/CIP/dedication pages)")
        for w in warnings[:10]:
            print("   -", w)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
