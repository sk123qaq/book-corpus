#!/usr/bin/env python3
"""classify_model、ingest 预填与 classify_books 合并的 stub 验收。

  python3 tests/acceptance/ops/test_classify.py

模型一律走 PI_BIN=tests/acceptance/fixtures/pi_stub.py，不联网。临时根在
tempfile.mkdtemp() 下，不碰真库、真收件箱、真人账。末行 ALL PASS。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SCRIPTS = os.path.join(ROOT, "src", "bookcorpus", "scripts")
STUB = os.path.join(ROOT, "tests", "acceptance", "fixtures", "pi_stub.py")
sys.path.insert(0, SCRIPTS)
import classify_model as CM
import lib_common as L


def run(script, args, env_extra=None, expect=0):
    env = dict(os.environ, PI_BIN=STUB, **(env_extra or {}))
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *args], capture_output=True, text=True, env=env, cwd=ROOT
    )
    assert r.returncode == expect, (r.returncode, r.stdout[-600:], r.stderr[-600:])
    return r


def one(level1="文学", level2="类型小说", confidence=0.93, reason="作者是推理作家"):
    return json.dumps(
        [{"level1": level1, "level2": level2, "confidence": confidence, "reason": reason}], ensure_ascii=False
    )


def make_library(tmp, name="lib"):
    root = os.path.join(tmp, name)
    os.makedirs(os.path.join(root, "books", "_收件箱"))
    os.makedirs(os.path.join(root, "tables"))
    return root


def test_parse():
    good = json.dumps(
        [{"level1": "文学", "level2": "类型小说", "confidence": 0.93, "reason": "推理系列"}], ensure_ascii=False
    )
    want = [{"level1": "文学", "level2": "类型小说", "confidence": 0.93, "reason": "推理系列"}]
    assert CM.parse(good, 1) == want, CM.parse(good, 1)
    wrapped = "好的：\n```json\n%s\n```\n以上。" % good
    assert CM.parse(wrapped, 1) == want, CM.parse(wrapped, 1)
    assert CM.parse("结果如下 " + good + " 完毕", 1) == want

    odd = CM.parse(
        json.dumps(
            [{"level1": "玄幻", "level2": "类型小说", "confidence": 1.7, "reason": "  依据\n 两行  "}],
            ensure_ascii=False,
        ),
        1,
    )[0]
    assert odd["level1"] == CM.UNCERTAIN, odd
    assert odd["level2"] == "", odd
    assert odd["confidence"] == 1.0, odd
    assert odd["reason"] == "依据 两行", odd
    cross = CM.parse(
        json.dumps([{"level1": "文学", "level2": "计算机与技术", "confidence": 0.5, "reason": ""}], ensure_ascii=False),
        1,
    )[0]
    assert cross["level2"] == "", cross

    for bad, word in (("模型今天不说话", "JSON 数组"), ('[{"level1": }]', "JSON 解析失败"), ("[]", "条数不符")):
        err = None
        try:
            CM.parse(bad, 1)
        except CM.ClassifyError as exc:
            err = str(exc)
        assert err is not None, "坏了还过：" + bad
        assert word in err, (word, err)
    assert CM.classify([], []) == []
    print("parse ok（含坏 JSON 可读报错）")


def test_prompt_and_argv():
    prompt = CM.build_prompt(["阿加莎", "三体"], ["克里斯蒂", ""])
    for key in CM.TAXONOMY:
        assert ("%s：" % key) in prompt, key
        assert CM.TOP_RUBRIC[key] in prompt, key
    assert "《阿加莎》" in prompt, prompt
    assert "作者 未知" in prompt, prompt
    assert "JSON" in prompt, prompt
    assert CM.PROMPT_VERSION
    assert CM.DEFAULT_MODEL.endswith("deepseek-v4.1-flash")

    old = os.environ.get("PI_BIN")
    os.environ["PI_BIN"] = STUB
    try:
        argv = CM.pi_argv("opencode-go/deepseek-v4.1-flash", "PROMPT")
    finally:
        if old is None:
            os.environ.pop("PI_BIN", None)
        else:
            os.environ["PI_BIN"] = old
    assert argv[:2] == [sys.executable, STUB], argv
    for flag in ("-p", "--no-tools", "--no-session", "--no-context-files", "--no-skills", "--no-extensions"):
        assert flag in argv, argv
    assert argv[-3:] == ["--model", "opencode-go/deepseek-v4.1-flash", "PROMPT"], argv
    print("prompt/argv ok（TAXONOMY、一级说明、stub 用解释器跑）")


def test_classify_through_stub():
    old = dict(os.environ)
    os.environ.update({"PI_BIN": STUB, "PI_STUB_JSON": one()})
    try:
        got = CM.classify(["书名"], ["作者"], CM.DEFAULT_MODEL, 30)
        assert got == [{"level1": "文学", "level2": "类型小说", "confidence": 0.93, "reason": "作者是推理作家"}], got
        os.environ["PI_STUB_JSON"] = '{"level1":"文学"}'
        err = None
        try:
            CM.classify(["书名"], ["作者"], CM.DEFAULT_MODEL, 30)
        except CM.ClassifyError as exc:
            err = str(exc)
        assert err is not None, "对象也当数组过了"
        assert "没回 JSON 数组" in err, err
        os.environ["PI_STUB_JSON"] = '[{"level1": }]'
        err = None
        try:
            CM.classify(["书名"], ["作者"], CM.DEFAULT_MODEL, 30)
        except CM.ClassifyError as exc:
            err = str(exc)
        assert err is not None, "坏数组也过了"
        assert "JSON 解析失败" in err, err
    finally:
        os.environ.clear()
        os.environ.update(old)
    print("classify ok（stub 回 JSON，坏 JSON 报可读错）")


def test_ingest_classify_adds(tmp):
    root = make_library(tmp, "ingest")
    with open(os.path.join(root, "books", "_收件箱", "新书 (张三).epub"), "wb") as fh:
        fh.write(b"epub bytes")
    r = run("ingest.py", ["scan", "--root", root, "--json"], {"PI_STUB_JSON": one()})
    items = json.loads(r.stdout)
    assert len(items) == 1, items
    assert items[0]["action"] == "add", items
    add = items[0]
    assert add["subjects"] == ["文学", "类型小说"], add
    assert "自动分类" in add["note"], add
    assert add["classify"]["needs_review"] is False, add
    cache_path = os.path.join(root, "工作区", "批次", "ingest", "classify.json")
    with open(cache_path, encoding="utf-8") as fh:
        rec = next(iter(json.load(fh).values()))
    assert rec["model"] == CM.DEFAULT_MODEL, rec
    assert rec["prompt_version"] == CM.PROMPT_VERSION, rec
    assert rec["level1"] == "文学", rec
    assert rec["confidence"] == 0.93, rec
    print("ingest classify_adds ok（plan 形状不变，缓存带 model/prompt_version）")

    import ingest as I

    old_cache = os.path.join(root, "old-cache.json")
    with open(old_cache, "w", encoding="utf-8") as fh:
        json.dump({"aaa": {"level1": "生活与实用", "conf1": 0.9, "level2": "饮食", "conf2": 0.8}}, fh)
    items = [{"sha256": "aaa", "action": "add", "title": "旧缓存书", "author": "", "subjects": [], "note": ""}]
    env_bak = dict(os.environ)
    os.environ["PI_STUB_JSON"] = "不该被调用"
    try:
        I.classify_adds(items, old_cache, enabled=True)
    finally:
        os.environ.clear()
        os.environ.update(env_bak)
    assert items[0]["subjects"] == ["生活与实用", "饮食"], items[0]
    print("ingest old cache ok（无 model/prompt_version 的旧条目照读）")

    items = [{"sha256": "bbb", "action": "add", "title": "新书", "author": "", "subjects": [], "note": ""}]
    cache2 = os.path.join(root, "cls2.json")
    old = dict(os.environ)
    os.environ.update({"PI_BIN": STUB, "PI_STUB_JSON": one("拿不准", "", 0.2, "看不出来")})
    try:
        I.classify_adds(items, cache2, enabled=True)
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert items[0]["subjects"] == ["其他"], items[0]
    assert "低置信" in items[0]["note"], items[0]
    assert "暂放其他" in items[0]["note"], items[0]
    assert items[0]["classify"]["needs_review"] is True, items[0]
    print("ingest low confidence ok（拿不准落其他）")


def test_ingest_bad_json(tmp):
    root = make_library(tmp, "badjson")
    with open(os.path.join(root, "books", "_收件箱", "杂书.epub"), "wb") as fh:
        fh.write(b"x")
    r = run("ingest.py", ["scan", "--root", root, "--json"], {"PI_STUB_JSON": "模型今天不说话"})
    item = json.loads(r.stdout)[0]
    assert item["subjects"] == ["其他"], item
    assert "分类失败" in item["note"], item
    assert "暂放其他" in item["note"], item
    assert "分类失败" in r.stderr, r.stderr
    assert not os.path.exists(os.path.join(root, "工作区", "批次", "ingest", "classify.json"))
    print("ingest bad JSON ok（stderr 报错，note 让人工填）")


def make_books_root(tmp, name):
    root = os.path.join(tmp, name)
    cat = os.path.join(root, "tables")
    os.makedirs(cat)
    ledger = [
        {
            "uid": "B0001",
            "一级": "文学",
            "二级": "类型小说",
            "作者层": "0",
            "保留": "keep",
            "正式书名": "13.67",
            "备注": "依据:TypeSafe(1.00)；置信:1.0",
        },
        {
            "uid": "B0002",
            "一级": "人文社科",
            "二级": "历史",
            "作者层": "0",
            "保留": "keep",
            "正式书名": "旧书",
            "备注": "",
        },
    ]
    L.write_csv(os.path.join(cat, "分类表.csv"), ledger, L.LEDGER_HEADER)
    rows = [
        {"uid": "B0001", "正式书名": "13.67", "一级": "文学", "二级": "", "预填依据": "重建-2026-10-22"},
        {"uid": "B0002", "正式书名": "旧书", "一级": "人文社科", "二级": "", "预填依据": "系列:某某"},
        {"uid": "B9002", "正式书名": "新书", "一级": "其他", "二级": "", "预填依据": "关键词"},
        {"uid": "B9003", "正式书名": "只有旧缓存的书", "一级": "", "二级": "", "预填依据": "关键词"},
    ]
    L.write_csv(os.path.join(cat, "书目录清单.csv"), rows, list(rows[0]))
    lines = [
        {
            "uid": "B0001",
            "stage": "model",
            "backend": "model",
            "model": CM.DEFAULT_MODEL,
            "prompt_version": CM.PROMPT_VERSION,
            "level1": "文学",
            "level2": "类型小说",
            "confidence": 0.93,
            "reason": "推理",
            "title": "13.67",
        },
        {
            "uid": "B0002",
            "stage": "model",
            "backend": "model",
            "model": CM.DEFAULT_MODEL,
            "prompt_version": CM.PROMPT_VERSION,
            "level1": "人文社科",
            "level2": "思想与哲学",
            "confidence": 0.71,
            "reason": "思想史",
            "title": "旧书",
        },
        {
            "uid": "B9002",
            "stage": "model",
            "backend": "model",
            "model": CM.DEFAULT_MODEL,
            "prompt_version": CM.PROMPT_VERSION,
            "level1": "文学",
            "level2": "严肃文学小说",
            "confidence": 0.81,
            "reason": "当代严肃小说",
            "title": "新书",
        },
        {
            "uid": "B9003",
            "stage": 1,
            "backend": "typesafe",
            "model": "jev-latest",
            "choice": "艺术与图像",
            "confidence": 1.0,
            "level1": "",
            "title": "只有旧缓存的书",
        },
    ]
    with open(os.path.join(cat, "分类缓存.jsonl"), "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return root, cat


def db_ledger(cat):
    import sqlite3

    path = os.path.join(cat, "ledger.db")
    if not os.path.isfile(path):
        return None
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM 分类 ORDER BY uid")]
    con.close()
    return rows


def test_merge_identical(tmp):
    root, cat = make_books_root(tmp, "merge")
    ledger = os.path.join(cat, "分类表.csv")
    before = Path(ledger).read_bytes()
    run("classify_books.py", ["--merge", "--backend", "model", "--root", root])
    assert Path(ledger).read_bytes() == before, "已有行被改了"

    rows = {r["uid"]: r for r in L.read_csv(os.path.join(cat, "分类预填.csv"))}
    header = list(L.read_csv(os.path.join(cat, "分类预填.csv"))[0])
    for col in ("一级模型", "二级模型", "模型置信", "模型依据"):
        assert col in header, header
    assert rows["B0001"]["一级模型"] == "文学", rows["B0001"]
    assert rows["B0001"]["二级模型"] == "类型小说", rows["B0001"]
    assert rows["B0001"]["模型置信"] == "0.93", rows["B0001"]
    assert rows["B0001"]["模型依据"] == "推理", rows["B0001"]
    assert rows["B9002"]["一级模型"] == "文学", rows["B9002"]
    assert rows["B9003"]["一级模型"] == "", "model 后端读了 typesafe 条目：%s" % rows["B9003"]
    assert rows["B9003"]["一级TypeSafe"] == "", rows["B9003"]
    assert len(db_ledger(cat)) == 2, "merge 不加行（B9002/B9003 不入人账）"
    print("merge identical ok（人账字节不变，预填四列写入，typesafe 条目不读）")


def test_add_new_one_row(tmp):
    root, cat = make_books_root(tmp, "addnew")
    ledger = os.path.join(cat, "分类表.csv")
    before = Path(ledger).read_bytes()
    run("classify_books.py", ["--merge", "--add-new", "--backend", "model", "--root", root])
    after = Path(ledger).read_bytes()
    assert after == before, "人账 CSV 是切换档，merge 不该再写"
    added = next(r for r in db_ledger(cat) if r["uid"] == "B9002")
    assert added["一级"] == "文学", added
    assert added["二级"] == "严肃文学小说", added
    assert added["备注"] == "auto:classify；待复核", added
    assert added["保留"] == "keep", added
    assert added["作者层"] == "0", added
    assert len(db_ledger(cat)) == 3, "只该多一行：%d" % len(db_ledger(cat))
    print("add-new one row ok（只追加 B9002，其余字节不变）")


def test_typesafe_merge(tmp):
    root, cat = make_books_root(tmp, "typesafe")
    cache = os.path.join(cat, "分类缓存.jsonl")
    recs = [
        {"uid": "B0001", "stage": 1, "choice": "文学", "confidence": 0.93, "title": "13.67"},
        {"uid": "B0001", "stage": 2, "choice": "类型小说", "confidence": 0.88, "level1": "文学", "title": "13.67"},
        {"uid": "B0002", "stage": 1, "choice": "人文社科", "confidence": 0.6, "title": "旧书"},
    ]
    with open(cache, "w", encoding="utf-8") as fh:
        for rec in recs:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ledger = os.path.join(cat, "分类表.csv")
    before = Path(ledger).read_bytes()
    run("classify_books.py", ["--merge", "--backend", "typesafe", "--root", root])
    assert Path(ledger).read_bytes() == before, "typesafe 合并改了人账"
    rows = {r["uid"]: r for r in L.read_csv(os.path.join(cat, "分类预填.csv"))}
    assert rows["B0001"]["一级TypeSafe"] == "文学", rows["B0001"]
    assert rows["B0001"]["二级TypeSafe"] == "类型小说", rows["B0001"]
    assert rows["B0001"]["一级模型"] == "", "typesafe 后端不该写模型列：%s" % rows["B0001"]
    assert rows["B0002"]["一级TypeSafe"] == "人文社科", rows["B0002"]
    print("typesafe merge ok（旧接口照旧，人账不动）")


def main():
    tmp = tempfile.mkdtemp(prefix="classify-test-")
    try:
        test_parse()
        test_prompt_and_argv()
        test_classify_through_stub()
        test_ingest_classify_adds(tmp)
        test_ingest_bad_json(tmp)
        test_merge_identical(tmp)
        test_add_new_one_row(tmp)
        test_typesafe_merge(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS")


if __name__ == "__main__":
    main()
