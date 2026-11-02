"""book-corpus 命令行。子命令都是对 scripts/ 下脚本的分发，行为与直接跑脚本一致。"""

import argparse
import ast
import json
import os
import pkgutil
import runpy
import sys

from . import __version__, workspace
from .scripts import lib_common

SCRIPTS_DIR = os.path.dirname(os.path.abspath(lib_common.__file__))

ALIASES = {
    "ingest": "ingest",
    "library": "build_library",
    "catalog": "gen_book_catalog",
    "render": "build_index",
    "index": "build_para_index",
    "search": "qsearch",
    "query": "book_query",
    "eval": "eval_retrieval",
    "verify": "verify_phase",
    "status": "ops_status",
    "numbers": "doc_numbers",
    "stats": "corpus_stats",
}

# 这些脚本还按旧布局拼路径（库/ 目录/ 视图/）。在 v2 树上跑会静默指错地方，
# 所以先拒跑，转完一个从这套里删一个。
LEGACY = {
    "audit_state", "migrate_layout", "ops_pdf_triage", "paddleocr_pilot",
    "pdf_census", "retire_book",
}

HELP = """book-corpus <命令> [参数...]

工作区
  init <目录> [--force]   建一个空工作区
  doctor [--json]         体检工作区
  list                    列出脚本与别名

迁移
  migrate [--apply]       把 v1 布局（库/ 目录/ 视图/）搬成 v2（books/ collections/ tables/ build/）
                          --dest <目录> 落到新树，--mode move|link|copy，--out <目录> 写对照表

跑脚本
  run <脚本> [参数...]    scripts/ 下任一脚本，例如 run scan_library --help
  <别名> [参数...]        常用脚本的短名，见 list

全局 --root <目录> 可以出现在任意位置。不传时依次看 BOOKS_ROOT、当前目录上层的 books.toml。"""


def script_path(name):
    if name.endswith(".py"):
        name = name[:-3]
    path = os.path.join(SCRIPTS_DIR, name + ".py")
    if not os.path.exists(path):
        raise SystemExit("没有这个脚本：%s（book-corpus list 看全部）" % name)
    return path


def run_script(name, argv, root):
    path = script_path(name)
    base = name[:-3] if name.endswith(".py") else name
    if base in LEGACY:
        raise SystemExit("%s 还没转到 v2 布局，先别在 v2 树上跑。见阶段 3 的剩余清单。" % base)
    os.environ["BOOKS_ROOT"] = root
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    sys.argv = [path] + list(argv)
    runpy.run_path(path, run_name="__main__")


def split_root(argv):
    """把任意位置的 --root X / --root=X 摘出来，其余原样返回。"""
    root, rest = None, []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--root="):
            root = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    return root, rest


def cmd_init(target, force):
    path = workspace.init(target, __version__, force)
    print("工作区建好：%s" % path)
    print("概念表带 126 条通用概念，按自己的语料增删；--concept 只认表里的键。")
    print("下一步：把书放进 %s/books/_收件箱，然后 cd 进去跑 book-corpus ingest scan" % path)
    return 0


def cmd_doctor(root, as_json):
    rows = workspace.check(root, __version__)
    if as_json:
        print(json.dumps([{"level": lv, "label": lb, "detail": d} for lv, lb, d in rows],
                         ensure_ascii=False, indent=2))
    else:
        width = max(len(lb) for _lv, lb, _d in rows)
        for level, label, detail in rows:
            print("%-5s %-*s %s" % (level, width, label, detail))
    return 1 if any(lv == "fail" for lv, _lb, _d in rows) else 0


def cmd_migrate(root, rest):
    from . import migrate
    dest = None
    mode = None
    out = "."
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--dest" and i + 1 < len(rest):
            dest = rest[i + 1]
            i += 2
            continue
        if arg == "--mode" and i + 1 < len(rest):
            mode = rest[i + 1]
            i += 2
            continue
        if arg == "--out" and i + 1 < len(rest):
            out = rest[i + 1]
            i += 2
            continue
        i += 1
    apply_it = "--apply" in rest
    if mode not in (None, "move", "link", "copy"):
        raise SystemExit("--mode 只认 move、link、copy")
    root = workspace.resolve(root)
    chosen = mode or ("move" if (dest or root) == root else "link")
    code, lines = migrate.run(root, dest or root, chosen, apply_it, out, __version__)
    for line in lines:
        print(line)
    if code == 0 and not apply_it:
        print("")
        print("没落盘。加 --apply 才动。")
    return code


def cmd_list():
    names = sorted(m.name for m in pkgutil.iter_modules([SCRIPTS_DIR]) if m.name != "lib_common")
    alias_of = {v: k for k, v in ALIASES.items()}
    for name in names:
        path = os.path.join(SCRIPTS_DIR, name + ".py")
        try:
            doc = ast.get_docstring(ast.parse(open(path, encoding="utf-8").read())) or ""
        except SyntaxError:
            doc = ""
        first = doc.strip().splitlines()[0] if doc.strip() else ""
        mark = " 未转 v2" if name in LEGACY else ""
        print("%-24s %-10s %s%s" % (name, alias_of.get(name, ""), first[:70], mark))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    explicit_root, argv = split_root(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(HELP)
        return 0
    if argv[0] == "--version":
        print("book-corpus " + __version__)
        return 0

    cmd, rest = argv[0], argv[1:]
    if cmd == "init":
        target = next((a for a in rest if not a.startswith("-")), None)
        if not target:
            raise SystemExit("init 要一个目录：book-corpus init <目录>")
        return cmd_init(target, "--force" in rest)
    if cmd == "doctor":
        return cmd_doctor(workspace.resolve(explicit_root), "--json" in rest)
    if cmd == "list":
        return cmd_list()
    if cmd == "migrate":
        return cmd_migrate(explicit_root, rest)
    if cmd == "run":
        if not rest:
            raise SystemExit("run 要脚本名：book-corpus run <脚本> [参数...]")
        run_script(rest[0], rest[1:], workspace.resolve(explicit_root))
        return 0
    if cmd in ALIASES:
        run_script(ALIASES[cmd], rest, workspace.resolve(explicit_root))
        return 0
    raise SystemExit("未知命令 %s。book-corpus --help 看全部。" % cmd)


if __name__ == "__main__":
    sys.exit(main())
