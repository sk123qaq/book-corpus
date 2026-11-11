#!/usr/bin/env python3
"""操作台状态三态验收：缺库 / 未体检 / 脚本失败 / 全好。

  python3 tests/acceptance/ops/test_status_state.py     # 末行 ALL PASS

用四个临时根分别起真服务（--scripts 指向真脚本或空目录），取 /api/status，
再用 tests/acceptance/ops/dom_status.js 跑 app.js 的 viewOverview/viewMaintain，
断言页面上的措辞：只有状态 ok 才能出现「干净 / 全部通过 / 已同步」。

不复用 check_ops.py 的 fixture，因为它带 OPS_STATUS_FIXTURE（stub 状态），
这里要的是真 ops_status.py 与真 server.py 的组合。
"""

import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OPS_DIR = os.path.join(REPO, "apps", "ops")
SERVER = os.path.join(OPS_DIR, "server.py")
APP_JS = os.path.join(OPS_DIR, "web", "app.js")
DOM_JS = os.path.join(HERE, "dom_status.js")
SCRIPTS = os.path.join(REPO, "src", "bookcorpus", "scripts")

sys.path.insert(0, HERE)
import test_status as T  # 复用它的 build_root（四本书、两个库、书目表、视图）

FAILS = []
FORBIDDEN = ("干净", "全部通过", "已同步")


def check(name, ok, detail=""):
    if ok:
        print("PASS  " + name)
    else:
        FAILS.append(name)
        print("FAIL  %s%s" % (name, (" - " + str(detail)) if detail else ""))


def node_bin():
    env = os.environ.get("OPS_NODE")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("node")
    if found:
        return found
    guess = os.path.expanduser("~/.local/share/pi-node/current/bin/node")
    return guess if os.path.isfile(guess) else ""


def wait_status(port, timeout=30):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:  # 服务刚起时连接被拒
            last = exc
            time.sleep(0.2)
    raise SystemExit("服务 30 秒内没就绪：%r" % (last,))


def start(root, scripts=SCRIPTS):
    env = dict(os.environ)
    env.pop("OPS_STATUS_FIXTURE", None)
    proc = subprocess.Popen(
        [sys.executable, SERVER, "--root", root, "--port", "0", "--scripts", scripts],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    line = proc.stdout.readline()
    match = re.search(r"127\.0\.0\.1:(\d+)", line or "")
    if not match:
        proc.kill()
        raise SystemExit("服务没打印端口：%r" % line)
    return proc, int(match.group(1))


def read_json_file(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json_file(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def stop(proc):
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except OSError:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def dom(body):
    """把 /api/status 的响应喂给 node 里的 app.js，拿三段 HTML。"""
    node = node_bin()
    if not node:
        raise SystemExit("找不到 node，DOM 断言跑不了；设 OPS_NODE 指向 node 可执行文件")
    payload = {
        "payload": body,
        "status": body.get("status") or {},
        "jobs": [],
        "pending": [],
    }
    proc = subprocess.run(
        [node, DOM_JS, APP_JS],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise SystemExit("dom_status.js 失败：%s" % proc.stderr[-1000:])
    return json.loads(proc.stdout)


def serve(root, scripts=SCRIPTS):
    proc, port = start(root, scripts)
    try:
        return wait_status(port)
    finally:
        stop(proc)


def assert_no_success_words(html):
    for word in FORBIDDEN:
        check("页面不出现「%s」" % word, word not in html, html[:200])


def assert_broken_words(html, words=("未知", "未运行", "不完整")):
    for word in words:
        check("页面出现「%s」" % word, word in html)


def scenario_missing_build(node_ok=True):
    print("\n== 场景一：缺 build（library.db / index.db 都不在）==")
    root = tempfile.mkdtemp(prefix="ops-state-nobuild-")
    try:
        body = serve(root)
        check("state=degraded", body.get("state") == "degraded", body.get("state"))
        check("complete=True（脚本跑成了）", body.get("complete") is True, body.get("complete"))
        check("stale=True（没体检）", body.get("stale") is True, body.get("stale"))
        check("ok 字段语义没改（无线程错误时仍为 True）", body.get("ok") is True, body.get("ok"))
        check("source=script", body.get("source") == "script", body.get("source"))
        missing = body.get("missing") or []
        check(
            "missing 列出四个缺库段落",
            set(missing) >= {"library", "catalog", "index", "views"},
            missing,
        )
        status = body.get("status") or {}
        check("计数是 0 但标了未知", status.get("books") == 0 and (status.get("library") or {}).get("ok") is False)
        check("index.ok=False", (status.get("index") or {}).get("ok") is False)
        check("audit.ran=False", (status.get("audit") or {}).get("ran") is False)
        problems = status.get("problems") or []
        check("problems 有缺库记录", any("library.db" in str(p) for p in problems), problems)
        if not node_ok:
            return
        parts = dom(body)
        html = "".join(parts.values())
        assert_no_success_words(html)
        assert_broken_words(html)
        check("维护页列出 problems 文本", "没有 build/library.db" in parts["maintain"], parts["maintain"][:300])
        check("计数未知显示 —", "—" in parts["stats"], parts["stats"])
        check("体检未运行进待办", "还没有做过体检" in parts["overview"], parts["overview"][:300])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scenario_no_audit():
    print("\n== 场景二：库都在，audit.at=None（还没跑过体检）==")
    root = tempfile.mkdtemp(prefix="ops-state-noaudit-")
    try:
        T.build_root(root)
        body = serve(root)
        status = body.get("status") or {}
        check("state=degraded", body.get("state") == "degraded", body.get("state"))
        check("complete=True", body.get("complete") is True, body.get("complete"))
        check("stale=True", body.get("stale") is True, body.get("stale"))
        check("missing 为空", body.get("missing") == [], body.get("missing"))
        check("books=4（读数是真的）", status.get("books") == 4, status.get("books"))
        check("audit.at=None", (status.get("audit") or {}).get("at") is None)
        check("audit.ran=False", (status.get("audit") or {}).get("ran") is False)
        parts = dom(body)
        html = "".join(parts.values())
        assert_no_success_words(html)
        assert_broken_words(html, ("未运行", "不完整"))
        check("维护条体检=未运行", "未运行" in parts["maintain"] and "还没有体检记录" in parts["maintain"])
        check("体检未运行进待办", "还没有做过体检" in parts["overview"], parts["overview"][:300])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scenario_script_missing():
    print("\n== 场景三：ops_status 跑不成（scripts 目录是空的）==")
    root = tempfile.mkdtemp(prefix="ops-state-noscript-")
    scripts = tempfile.mkdtemp(prefix="ops-state-emptyscripts-")
    try:
        body = serve(root, scripts)
        check("state=unknown", body.get("state") == "unknown", body.get("state"))
        check("complete=False", body.get("complete") is False, body.get("complete"))
        check("ok=False（兼容字段跟着降级）", body.get("ok") is False, body.get("ok"))
        check("source=missing", body.get("source") == "missing", body.get("source"))
        check("error 有原因", bool(body.get("error")), body.get("error"))
        parts = dom(body)
        html = "".join(parts.values())
        assert_no_success_words(html)
        check("页面说读不到", "读不到" in html)
        check("维护条体检=未运行", "未运行" in parts["maintain"])
        check("计数未知显示 —", "—" in parts["stats"], parts["stats"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(scripts, ignore_errors=True)


def make_clean_root(root):
    """把 build_root 调成能做干净状态：所有书都有 md、索引覆盖全部、收件箱空、体检已跑。"""
    T.build_root(root)
    inbox = os.path.join(root, "books", "_收件箱")
    shutil.rmtree(inbox, ignore_errors=True)
    book3 = os.path.join(root, "books", "B0003")
    T.write(os.path.join(book3, "md", "01.md"), "# 丙书 第 1 章\n正文。\n")
    T.write(os.path.join(book3, "md", "02.md"), "# 丙书 第 2 章\n正文。\n")
    T.write(os.path.join(book3, "md", "README.md"), "# 丙书\n\n- [第1章](01.md)\n")
    libdb = os.path.join(root, "build", "library.db")
    con = sqlite3.connect(libdb)
    con.execute("INSERT INTO derived(uid,kind,status,chapters,bytes) VALUES('B0003','md','done',2,100)")
    con.commit()
    con.close()
    shutil.rmtree(os.path.join(root, "工作区", "批次"), ignore_errors=True)
    idx = os.path.join(root, "build", "检索", "index.db")
    con = sqlite3.connect(idx)
    for key, value in (("books", "3"), ("chapters", "6"), ("paras", "6")):
        con.execute("UPDATE meta SET value=? WHERE key=?", (value, key))
    con.commit()
    con.close()
    T.write(
        os.path.join(root, "工作区", "运维", "last-audit.json"),
        json.dumps(
            {
                "at": datetime.datetime.now().astimezone().replace(microsecond=0).isoformat(),
                "root": root,
                "deep": True,
                "fail": [],
                "warn": [],
                "ok": ["结构"],
            },
            ensure_ascii=False,
        ),
    )


def scenario_all_ok():
    print("\n== 场景四：库全、索引不落后、体检已跑 ==")
    root = tempfile.mkdtemp(prefix="ops-state-ok-")
    try:
        make_clean_root(root)
        body = serve(root)
        status = body.get("status") or {}
        check("state=ok", body.get("state") == "ok", body.get("state"))
        check("complete=True", body.get("complete") is True, body.get("complete"))
        check("stale=False", body.get("stale") is False, body.get("stale"))
        check("missing 为空", body.get("missing") == [], body.get("missing"))
        check("problems 为空", status.get("problems") == [], status.get("problems"))
        check("audit.ran=True", (status.get("audit") or {}).get("ran") is True)
        check("audit.stale=False", (status.get("audit") or {}).get("stale") is False, status.get("audit"))
        parts = dom(body)
        html = "".join(parts.values())
        for word in FORBIDDEN:
            check("页面出现「%s」" % word, word in html, html[:400])
        check("主判定是干净的", "库是干净的" in parts["overview"], parts["overview"][:300])
        check("没有「未运行」", "未运行" not in html)
        check("没有「不完整」", "不完整" not in html)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scenario_partial_state():
    print("\n== 场景五：库与索引可读、体检已跑，但缺浏览入口与书目总表 ==")
    root = tempfile.mkdtemp(prefix="ops-state-partial-")
    try:
        make_clean_root(root)
        shutil.rmtree(os.path.join(root, "build", "views"), ignore_errors=True)
        os.remove(os.path.join(root, "build", "书目总表.csv"))
        body = serve(root)
        status = body.get("status") or {}
        audit = status.get("audit") or {}
        check("state=degraded", body.get("state") == "degraded", body.get("state"))
        check("complete=True（脚本跑成了）", body.get("complete") is True, body.get("complete"))
        check("stale=False（体检记录在）", body.get("stale") is False, body.get("stale"))
        missing = body.get("missing") or []
        check("missing 含 views 与 catalog", {"views", "catalog"} <= set(missing), missing)
        check(
            "library/index 仍可读",
            (status.get("library") or {}).get("ok") is True and (status.get("index") or {}).get("ok") is True,
            (status.get("library"), status.get("index")),
        )
        check("views.ok=False", (status.get("views") or {}).get("ok") is False, status.get("views"))
        check("catalog.ok=False", (status.get("catalog") or {}).get("ok") is False, status.get("catalog"))
        check(
            "audit.ran=True 且 fail/warn 都是 0",
            audit.get("ran") is True and audit.get("fail") == 0 and audit.get("warn") == 0,
            audit,
        )
        check("audit.stale=False", audit.get("stale") is False, audit)
        parts = dom(body)
        overview, maintain = parts["overview"], parts["maintain"]
        check("总览不说「库是干净的」", "库是干净的" not in overview, overview[:300])
        check("总览说「状态不完整」", "状态不完整" in overview, overview[:300])
        check("总览体检格不出现「全部通过」", "全部通过" not in overview, overview[:400])
        check("总览体检格=需注意 / 无失败项", "需注意" in overview and "无失败项" in overview, overview[:400])
        check("待办含「书库状态不完整」", "书库状态不完整" in overview, overview[:400])
        check(
            "待办 note 点名书目总表与浏览入口",
            "书目总表" in overview and "浏览入口" in overview,
            overview[:400],
        )
        check("维护条浏览入口=未知", "浏览入口缺失" in maintain, maintain[:400])
        check("维护条书目总表=未知", "没有书目总表" in maintain, maintain[:400])
        check("维护条体检不出现「全部通过」", "全部通过" not in maintain, maintain[:400])
        check("维护条体检=需注意 / 无失败项", "需注意" in maintain and "无失败项" in maintain, maintain[:400])
        for word in FORBIDDEN:
            check("页面不出现「%s」" % word, word not in "".join(parts.values()))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scenario_index_extra():
    print("\n== 场景七：索引比书库多（旧书没清）==")
    root = tempfile.mkdtemp(prefix="ops-state-extra-")
    try:
        make_clean_root(root)
        idx = os.path.join(root, "build", "检索", "index.db")
        con = sqlite3.connect(idx)
        con.execute("UPDATE meta SET value='99' WHERE key='books'")
        con.commit()
        con.close()
        cache_path = os.path.join(root, "工作区", "运维", "last-audit.json")
        cache = read_json_file(cache_path)
        cache["at"] = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
        write_json_file(cache_path, cache)

        body = serve(root)
        status = body.get("status") or {}
        index = status.get("index") or {}
        check("state=degraded", body.get("state") == "degraded", body.get("state"))
        check("原始 lag_books 为负", (index.get("lag_books") or 0) < 0, index)
        check("extra_books=96", index.get("extra_books") == 96, index)
        problems = status.get("problems") or []
        check("problems 点名索引多出书", any("索引比书库多" in str(p) for p in problems), problems)
        parts = dom(body)
        html = "".join(parts.values())
        check("总览说索引比书库多", "比书库多 96 本" in parts["overview"], parts["overview"][:400])
        check("维护条说索引比书库多", "比书库多 96 本" in parts["maintain"], parts["maintain"][:400])
        check("待办说索引比书库多", "检索索引比书库多 96 本" in parts["overview"], parts["overview"][:500])
        check("页面不出现负的落后", "落后 -" not in html)
        for word in FORBIDDEN:
            check("页面不出现「%s」" % word, word not in html)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scenario_stale_or_foreign_cache():
    print("\n== 场景六：体检缓存过期 / 属于别的库 ==")
    root = tempfile.mkdtemp(prefix="ops-state-stale-")
    try:
        make_clean_root(root)
        cache_path = os.path.join(root, "工作区", "运维", "last-audit.json")
        cache = read_json_file(cache_path)
        cache["at"] = "2000-01-01T00:00:00+08:00"
        write_json_file(cache_path, cache)

        body = serve(root)
        status = body.get("status") or {}
        audit = status.get("audit") or {}
        check("过期：state=degraded", body.get("state") == "degraded", body.get("state"))
        check("过期：stale=True", body.get("stale") is True, body.get("stale"))
        check("过期：audit.ran=True", audit.get("ran") is True, audit)
        check("过期：audit.stale=True", audit.get("stale") is True, audit)
        problems = status.get("problems") or []
        check("过期：problems 点名体检过期", any("过期" in str(p) for p in problems), problems)
        parts = dom(body)
        html = "".join(parts.values())
        assert_no_success_words(html)
        check("过期：维护条体检=已过期", "已过期" in parts["maintain"], parts["maintain"][:400])
        check("过期：总览待办=体检结果已过期", "体检结果已过期" in parts["overview"], parts["overview"][:400])

        cache["at"] = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
        cache["root"] = root + "-another-library"
        write_json_file(cache_path, cache)
        body = serve(root)
        status = body.get("status") or {}
        audit = status.get("audit") or {}
        check("异库：state=degraded", body.get("state") == "degraded", body.get("state"))
        check("异库：stale=True", body.get("stale") is True, body.get("stale"))
        check("异库：audit.ran=False", audit.get("ran") is False, audit)
        problems = status.get("problems") or []
        check("异库：problems 点名另一个书库", any("另一个书库" in str(p) for p in problems), problems)
        parts = dom(body)
        assert_no_success_words("".join(parts.values()))
        check("异库：维护条体检=未运行", "未运行" in parts["maintain"], parts["maintain"][:400])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    if not os.path.exists(SERVER):
        raise SystemExit("缺 server.py：%s" % SERVER)
    node = node_bin()
    check("找得到 node（DOM 断言需要）", bool(node), node or "PATH 与 ~/.local/share/pi-node 都没有")
    if not FAILS:
        scenario_missing_build()
        scenario_no_audit()
        scenario_script_missing()
        scenario_all_ok()
        scenario_partial_state()
        scenario_index_extra()
        scenario_stale_or_foreign_cache()
    print("")
    if FAILS:
        print("%d 项检查，失败 %d 项" % (len(FAILS), len(FAILS)))
        for name in FAILS:
            print("  失败：" + name)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
