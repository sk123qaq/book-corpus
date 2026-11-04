"""book-corpus 命令行。子命令都是对 scripts/ 下脚本的分发，行为与直接跑脚本一致。"""

import argparse
import ast
import json
import os
import pkgutil
import runpy
import sqlite3
import subprocess
import sys

from . import __version__, pdfenv, workspace

# 不在这里 import lib_common：它 import 时就按 cwd 定根，先 import 会让
# run_script 设的 BOOKS_ROOT 失效。只算路径，脚本自己 import。
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")

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

HELP = """book-corpus <命令> [参数...]

工作区
  init <目录> [--force] [--no-pdf-venv]
                          建一个空工作区；顺手建 工作区/.venv-pdf 并装 PDF 依赖
  sample                  装内置样书《阿Q正传》，空库马上能检索
  doctor [--json]         体检工作区
  venv [--check]          建/补 工作区/.venv-pdf；--check 只导入一遍依赖
  model [set|setup-pi|check]
                          看/改这个工作区用哪个 pi 模型、思考等级、端点和 key
  list                    列出脚本与别名

迁移
  migrate [--apply]       把 v1 布局（库/ 目录/ 视图/）搬成 v2（books/ collections/ tables/ build/）
                          --dest <目录> 落到新树，--mode move|link|copy，--out <目录> 写对照表
  remap --map <csv> [--apply]
                          按对照表改写索引里的路径；--old-db <旧 index.db> 把旧 expand 灌进新索引

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


def _init_build(root):
    """新库也要有 library.db 与 index.db：没书时 search/query 返回 0 条，不是报错。"""
    made = []
    if not os.path.exists(os.path.join(root, "build", "library.db")):
        made.append(("书目库", _run_here(root, "library", quiet=True)))
    if not os.path.exists(os.path.join(root, "build", "检索", "index.db")):
        made.append(("检索索引", _run_here(root, "index", "--rebuild", quiet=True)))
    for label, ok in made:
        if not ok:
            print("%s 没建好，跑 book-corpus %s 重试" % (label, "library" if label == "书目库" else "index --rebuild"))
        else:
            print("%s：空库已建好" % label)
    return all(ok for _label, ok in made)


def cmd_init(target, force, pdf_venv):
    path = workspace.init(target, __version__, force, pdf_venv)
    print("工作区建好：%s" % path)
    ok, detail = pdfenv.versions(path)
    if ok:
        print("PDF 环境：%s" % detail)
    else:
        print("PDF 环境没建好：%s" % detail)
        print("  重试：book-corpus venv --root %s" % path)
    print("概念表带通用概念（tables/ledger.db 的 概念 表，改表用操作台或直接改库），按自己的语料增删；--concept 只认表里的键。")
    _init_build(path)
    print("下一步：放一本书进 %s/books/_收件箱，或跑 book-corpus sample 用内置样书" % path)
    return 0


def _index_books(root):
    path = os.path.join(root, "build", "检索", "index.db")
    if not os.path.exists(path):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        row = con.execute("select value from meta where key='books'").fetchone()
    finally:
        con.close()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


MODEL_SET_FLAGS = ("provider", "model", "thinking", "base_url", "api_key_env", "api")


def _model_set(root, rest):
    from . import pimodel

    changes, i = {}, 0
    while i < len(rest):
        arg = rest[i]
        if arg.startswith("--") and i + 1 < len(rest):
            field = arg[2:].replace("-", "_")
            if field not in MODEL_SET_FLAGS:
                raise SystemExit("model set 不认 %s；能改：%s" % (arg, " ".join("--" + f.replace("_", "-") for f in MODEL_SET_FLAGS)))
            changes[field] = rest[i + 1]
            i += 2
            continue
        if arg == "--clear":
            changes.update({f: "" for f in MODEL_SET_FLAGS})
            i += 1
            continue
        raise SystemExit("model set 看不懂 %s；用法：book-corpus model set --model <id> [--thinking high]" % arg)
    if not changes:
        raise SystemExit("model set 要至少改一项：book-corpus model set --model <id>")
    if changes.get("thinking") and changes["thinking"] not in pimodel.LEVELS:
        raise SystemExit("--thinking 只认 %s" % "、".join(pimodel.LEVELS))
    table = pimodel.save(root, changes)
    cfg = pimodel.load(root)
    print("写进 books.toml [model]：%s" % json.dumps(table, ensure_ascii=False))
    print("实际调用：%s" % " ".join(pimodel.model_flags(cfg["values"]) or ["（用 pi 自己的默认）"]))
    if cfg["values"]["base_url"]:
        print("配了 base_url，要把它交给 pi：book-corpus model setup-pi")
    print("验一次：book-corpus model check")
    return 0


def _model_show(root):
    from . import pimodel

    cfg = pimodel.load(root)
    values, sources = cfg["values"], cfg["sources"]
    for field in pimodel.FIELDS:
        print("  %-12s %-38s %s" % (field, values[field] or "（空）", sources[field]))
    flags = pimodel.model_flags(values)
    print("  实际调用      pi %s" % (" ".join(flags) if flags else "（用 pi 自己的默认模型）"))
    model_ref = cfg["ref"]
    known = pimodel.known_models()
    if not known:
        print("  pi 模型表      读不到（pi 不在 PATH？），跳过核对")
    elif model_ref and any(model_ref in line or model_ref.split("/", 1)[-1] in line for line in known):
        print("  pi 模型表      有 %s" % model_ref)
    elif model_ref:
        print("  pi 模型表      没找到 %s —— 先 /model 看看 pi 里有什么，或 book-corpus model set --model <id>" % model_ref)
    print("")
    print("在 pi 里选（最省事）：/login 选 provider 并给 key，/model 选模型，/thinking 选思考等级；")
    print("                     把选中的写进项目：book-corpus model set --provider <p> --model <id> --thinking <级>")
    print("自己搭的 OpenAI 兼容端点：book-corpus model set --provider <名> --model <id> \\")
    print("                         --base-url https://.../v1 --api-key-env <存 key 的环境变量>")
    print("                        book-corpus model setup-pi   把这套端点写进 ~/.pi/agent/models.json")
    print("验一次：book-corpus model check（真发一句，看回不回 ok）")
    return 0


def _model_check(root):
    from . import pimodel

    cfg = pimodel.load(root)
    result = pimodel.run(cfg["values"], "只回复两个字：ok", timeout=120)
    cmd = " ".join(result["argv"])
    key = cfg["values"]["api_key_env"]
    if key and os.environ.get(key):
        cmd = cmd.replace(os.environ[key], "****")
    print("跑：%s" % cmd)
    if result["ok"]:
        print("回：%s（%d ms）" % (result["text"][:200], result["ms"]))
        return 0
    print("失败：%s（%d ms）" % (result["error"], result["ms"]))
    print("先按提示在 pi 里把 provider/key/模型配好，再回来 check。")
    return 1


def _model_setup_pi(root):
    from . import pimodel

    cfg = pimodel.load(root)
    changed, detail = pimodel.setup_pi(cfg["values"])
    print(detail)
    if changed:
        print("pi 下次启动/打开 /model 时读到新端点；之后 book-corpus model check 验一次。")
    return 0


def cmd_model(root, rest):
    action = rest[0] if rest else "show"
    if action in ("show", "--json"):
        return _model_show(root)
    if action == "set":
        return _model_set(root, rest[1:])
    if action == "setup-pi":
        return _model_setup_pi(root)
    if action == "check":
        return _model_check(root)
    raise SystemExit("model 只认 show、set、setup-pi、check")


def _run_here(root, *argv, quiet=False):
    """子进程跑本包：run_script 同进程跑会复用已 import 的 lib_common，根是旧值。"""
    env = dict(os.environ, BOOKS_ROOT=root)
    proc = subprocess.run([sys.executable, "-m", "bookcorpus", *argv],
                          capture_output=True, text=True, env=env)
    if quiet and proc.returncode == 0:
        return True
    out = (proc.stdout or "").rstrip()
    if out:
        print(out)
    if proc.returncode != 0:
        err = (proc.stderr or "").rstrip()
        print(err or "命令失败：book-corpus %s" % " ".join(argv))
    return proc.returncode == 0


def cmd_sample(root):
    from . import sample

    if not os.path.isfile(os.path.join(root, "books.toml")):
        raise SystemExit("不是工作区：%s 里没有 books.toml。先 book-corpus init <目录>" % root)
    have = sample.load_books(root)
    if have:
        uid, doc = have[0]
        chapters, chars = sample._count(root, uid)
        print("样书已在库：%s《%s》%d 章 %d 字" % (uid, doc.get("title") or sample.TITLE,
                                               chapters, chars))
    else:
        uid, chapters, chars = sample.install(root)
        print("样书入库：%s《%s》%d 章 %d 字（%s）" % (uid, sample.TITLE, chapters, chars,
                                                   sample.SUBJECTS[0] + "/" + sample.SUBJECTS[1]))
    _run_here(root, "library")
    books = _index_books(root)
    if books is None or books <= 1:
        _run_here(root, "index", "--rebuild")
    else:
        print("索引里已有 %d 本书，没动它。要把样书加进检索：book-corpus index --rebuild" % books)
    print("试一条：book-corpus search 你那里配姓赵")
    print("问书：book-corpus query 阿Q为什么挨打")
    return 0


def cmd_venv(root, check):
    if check:
        ok, detail = pdfenv.versions(root)
    else:
        ok, detail = pdfenv.create(root)
        if ok:
            ok, detail = pdfenv.versions(root)
    print(("PDF 环境：%s" % detail) if ok else ("PDF 环境不可用：%s" % detail))
    return 0 if ok else 1


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


def cmd_remap(root, rest):
    from . import remap
    from .scripts import layout as LY
    db = os.path.join(LY.Layout(workspace.resolve(root)).index_dir, "index.db")
    old_db = None
    mapping = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--db" and i + 1 < len(rest):
            db = rest[i + 1]
            i += 2
            continue
        if arg == "--old-db" and i + 1 < len(rest):
            old_db = rest[i + 1]
            i += 2
            continue
        if arg == "--map" and i + 1 < len(rest):
            mapping = rest[i + 1]
            i += 2
            continue
        i += 1
    if not mapping:
        raise SystemExit("remap 要 --map <对照表.csv>")
    code, lines = remap.run(db, mapping, old_db, "--apply" in rest)
    for line in lines:
        print(line)
    return code


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
        print("%-24s %-10s %s" % (name, alias_of.get(name, ""), first[:70]))
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
    if cmd == "model":
        return cmd_model(workspace.resolve(explicit_root), rest)
    if cmd == "sample":
        return cmd_sample(workspace.resolve(explicit_root))
    if cmd == "venv":
        return cmd_venv(workspace.resolve(explicit_root), "--check" in rest)
    if cmd == "init":
        target = next((a for a in rest if not a.startswith("-")), None)
        if not target:
            raise SystemExit("init 要一个目录：book-corpus init <目录>")
        return cmd_init(target, "--force" in rest, "--no-pdf-venv" not in rest)
    if cmd == "doctor":
        return cmd_doctor(workspace.resolve(explicit_root), "--json" in rest)
    if cmd == "list":
        return cmd_list()
    if cmd == "migrate":
        return cmd_migrate(explicit_root, rest)
    if cmd == "remap":
        return cmd_remap(explicit_root, rest)
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
