"""docs_to_md.py convert：真写盘的全流程（假 worker，不联网、不装 markitdown）。

覆盖 §3.3/§3.4：md/ 原子落地、README + manifest 字段、源 sha、data URI 抽图、
txt 编码兜底、>200MB 预检、300s 超时杀进程组、失败不动旧 md、覆盖前备份。
"""

import base64
import hashlib
import json
import os
import shlex
import sys
import time
from pathlib import Path

import pytest

from conftest import run_cli

HERE = Path(__file__).resolve().parents[1]
SCRIPTS = HERE / "src" / "bookcorpus" / "scripts"

PNG_B64 = "iVBORw0KGgo="
FAKE_WORKER = """\
import base64, json, os, sys, time

opts = dict(zip(sys.argv[1::2], sys.argv[2::2]))
out = opts["--out"]
os.makedirs(out, exist_ok=True)
mode = os.environ.get("FAKE_MODE", "ok")
pid_file = os.environ.get("FAKE_PID_FILE", "")


def say(**kw):
    print(json.dumps(kw, ensure_ascii=False))


if pid_file:
    with open(pid_file, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
if mode == "sleep":
    time.sleep(60)
if mode == "empty":
    open(os.path.join(out, "body.md"), "w", encoding="utf-8").write("   \\n")
    say(ok=True, chars=3, images=0, images_dropped=0, warnings=[], error="")
elif mode == "bad":
    say(ok=False, chars=0, images=0, images_dropped=0, warnings=[], error="ValueError: 坏文件")
elif mode == "noh1":
    open(os.path.join(out, "body.md"), "w", encoding="utf-8").write("只有段落，没有一级标题。\\n")
    say(ok=True, chars=12, images=0, images_dropped=0, warnings=[], error="")
else:
    img_dir = os.path.join(out, "images")
    os.makedirs(img_dir, exist_ok=True)
    with open(os.path.join(img_dir, "img-1.png"), "wb") as fh:
        fh.write(base64.b64decode("iVBORw0KGgo="))
    body = "# 标题\\n\\n正文第一段。\\n\\n![图](images/img-1.png)\\n"
    open(os.path.join(out, "body.md"), "w", encoding="utf-8").write(body)
    say(ok=True, chars=len(body), images=1, images_dropped=0, warnings=[], error="")
"""


def _mod():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import docs_to_md

    return docs_to_md


@pytest.fixture
def worker_cmd(tmp_path, monkeypatch):
    path = tmp_path / "fake_worker.py"
    path.write_text(FAKE_WORKER, encoding="utf-8")
    cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(path))}"
    monkeypatch.setenv("BOOKS_DOC_WORKER_CMD", cmd)
    return cmd


def _root(tmp_path):
    root = tmp_path / "ws"
    (root / "books").mkdir(parents=True, exist_ok=True)
    return root


def _book(root, uid, title, fmt, data, sha=None):
    name = f"{title}.{fmt}"
    src = root / "books" / uid / "source" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest() if sha is None else sha
    return {
        "uid": uid,
        "title": title,
        "format": fmt,
        "path": f"books/{uid}/source/{name}",
        "sha256": digest,
        "reason": "new",
    }


def _job(tmp_path, items, name="job"):
    job = tmp_path / name
    job.mkdir(parents=True, exist_ok=True)
    (job / "worklist.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return job


def _report(job):
    return json.loads((job / "report.json").read_text(encoding="utf-8"))


def _item(report, uid):
    return next(x for x in report["items"] if x["uid"] == uid)


def _manifest(root, uid):
    return json.loads((root / "books" / uid / "md" / ".manifest.json").read_text(encoding="utf-8"))


def _one(root, tmp_path, item):
    job = _job(tmp_path, [item])
    assert _mod().convert(str(root), str(job)) == 0
    return job, _report(job)


def test_txt_lands_single_file_with_readme_and_manifest(tmp_path):
    root = _root(tmp_path)
    item = _book(root, "B0001", "甲书", "txt", "# 第一章\n\n正文第一段。\n".encode())
    job, report = _one(root, tmp_path, item)

    md_dir = root / "books" / "B0001" / "md"
    assert sorted(p.name for p in md_dir.iterdir()) == [".manifest.json", "README.md", "甲书.md"]
    body = (md_dir / "甲书.md").read_text(encoding="utf-8")
    assert body.startswith("# 第一章")
    readme = (md_dir / "README.md").read_text(encoding="utf-8")
    assert "# 甲书" in readme
    assert "- [甲书](甲书.md)" in readme
    assert "- 无（整本单文件）" in readme

    man = _manifest(root, "B0001")
    assert man["uid"] == "B0001"
    assert man["method"] == "text-utf8"
    assert man["tool_version"] == "text 1"
    assert man["source_sha256"] == item["sha256"]  # 是源文件的 sha，不是 md 的
    assert man["source_sha256"] == hashlib.sha256((root / item["path"]).read_bytes()).hexdigest()
    assert man["chapters"] == 1
    assert man["structure"] == "single"
    assert man["single_file"] is True
    assert man["assembler"] == "docs_to_md.py"
    assert man["verified"] is False
    assert man["charset"] == "utf-8"
    assert man["charset_fallback"] is False
    assert man["bytes"] == (md_dir / "甲书.md").stat().st_size
    assert man["chars"] == len(body.rstrip("\n"))

    row = _item(report, "B0001")
    assert row["status"] == "done"
    assert row["md"] == "books/B0001/md/甲书.md"
    assert row["chars"] == man["chars"]
    assert report["summary"] == {"total": 1, "done": 1, "skipped": 0, "failed": 0}
    assert report["version"] == 1
    assert report["job_id"] == "job"
    assert report["scope"]["new"] == 1

    progress = json.loads((job / "progress.json").read_text(encoding="utf-8"))
    assert progress["phase"] == "done"
    assert (progress["done"], progress["total"]) == (1, 1)
    assert json.loads((job / "books" / "B0001.json").read_text(encoding="utf-8"))["status"] == "done"
    journal = (root / "工作区" / "批次" / "docs" / "journal.tsv").read_text(encoding="utf-8")
    assert "uid\tstatus\treason" in journal.splitlines()[0]
    assert "\tB0001\tdone\t" in journal


def test_gb18030_fallback_is_recorded(tmp_path):
    root = _root(tmp_path)
    data = "# 中文标题\n\n这是 GB18030 的正文。\n".encode("gb18030")
    item = _book(root, "B0002", "乙书", "txt", data)
    report = _one(root, tmp_path, item)[1]

    row = _item(report, "B0002")
    assert row["status"] == "done"
    assert row["charset"] == "gb18030"
    assert row["charset_fallback"] is False
    man = _manifest(root, "B0002")
    assert man["method"] == "text-gb18030"
    assert man["charset"] == "gb18030"
    body = (root / "books" / "B0002" / "md" / "乙书.md").read_text(encoding="utf-8")
    assert "这是 GB18030 的正文。" in body


def test_md_source_is_copied_and_keeps_h1(tmp_path):
    root = _root(tmp_path)
    item = _book(root, "B0003", "丙书", "md", "# 原有标题\n\n正文。\n".encode())
    report = _one(root, tmp_path, item)[1]

    row = _item(report, "B0003")
    assert row["status"] == "done"
    man = _manifest(root, "B0003")
    assert man["method"] == "copy-md"
    assert man["tool_version"] == "copy 1"
    body = (root / "books" / "B0003" / "md" / "丙书.md").read_text(encoding="utf-8")
    assert body.count("# 原有标题") == 1  # 不叠第二个 H1
    assert body.startswith("# 原有标题")


def test_md_source_is_byte_for_byte_copy(tmp_path):
    """md 源不处理：front matter、H1、结尾换行都原样保留。"""
    root = _root(tmp_path)
    raw = "---\ntitle: 原有\n---\n\n# 原有标题\n\n正文。"
    item = _book(root, "B0005", "戊书", "md", raw.encode())
    report = _one(root, tmp_path, item)[1]

    row = _item(report, "B0005")
    assert row["status"] == "done"
    body = (root / "books" / "B0005" / "md" / "戊书.md").read_text(encoding="utf-8")
    assert body == raw


def test_missing_h1_gets_title(tmp_path):
    root = _root(tmp_path)
    item = _book(root, "B0004", "丁书", "txt", "只有正文，没有标题。\n".encode())
    _ = _one(root, tmp_path, item)
    body = (root / "books" / "B0004" / "md" / "丁书.md").read_text(encoding="utf-8")
    assert body.startswith("# 丁书\n\n")
    assert "只有正文" in body


def test_data_uri_images_extracted_and_rewritten(tmp_path):
    root = _root(tmp_path)
    text = f"# 有图\n\n![图](data:image/png;base64,{PNG_B64})\n\n![坏图](data:image/png;base64,a)\n"
    item = _book(root, "B0005", "戊书", "txt", text.encode())
    report = _one(root, tmp_path, item)[1]

    md_dir = root / "books" / "B0005" / "md"
    img = md_dir / "images" / "img-1.png"
    assert img.is_file()
    assert img.read_bytes() == base64.b64decode(PNG_B64)
    body = (md_dir / "戊书.md").read_text(encoding="utf-8")
    assert "![图](images/img-1.png)" in body
    assert "base64" not in body
    assert "坏图" not in body  # 解不出的整段删掉

    row = _item(report, "B0005")
    assert (row["images"], _manifest(root, "B0005")["images"]) == (1, 1)
    assert _manifest(root, "B0005")["images_dropped"] == 1
    readme = (md_dir / "README.md").read_text(encoding="utf-8")
    assert "- 图片：1" in readme


def test_html_img_data_uri_is_extracted(tmp_path):
    root = _root(tmp_path)
    text = f'# 网页图\n\n<img src="data:image/jpeg;base64,{PNG_B64}">\n'
    item = _book(root, "B0006", "己书", "txt", text.encode())
    _ = _one(root, tmp_path, item)
    md_dir = root / "books" / "B0006" / "md"
    assert (md_dir / "images" / "img-1.jpg").is_file()
    assert "![](images/img-1.jpg)" in (md_dir / "己书.md").read_text(encoding="utf-8")


def test_docx_worker_output_goes_through_contract(tmp_path, worker_cmd):
    root = _root(tmp_path)
    item = _book(root, "B0007", "庚书", "docx", b"PK-fake-docx")
    report = _one(root, tmp_path, item)[1]

    row = _item(report, "B0007")
    assert row["status"] == "done", row
    assert row["images"] == 1
    assert row["chars"] == _manifest(root, "B0007")["chars"] > 0
    md_dir = root / "books" / "B0007" / "md"
    assert (md_dir / "images" / "img-1.png").is_file()
    assert not (md_dir / "body.md").exists()  # worker 的中间稿不落地
    man = _manifest(root, "B0007")
    assert man["method"] == "markitdown-docx"
    assert man["tool_version"] == "markitdown 0.1.9"
    assert man["source_sha256"] == item["sha256"]


def test_worker_without_h1_gets_title(tmp_path, worker_cmd, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "noh1")
    root = _root(tmp_path)
    item = _book(root, "B0008", "辛书", "xlsx", b"PK-fake-xlsx")
    report = _one(root, tmp_path, item)[1]
    assert _item(report, "B0008")["status"] == "done"
    body = (root / "books" / "B0008" / "md" / "辛书.md").read_text(encoding="utf-8")
    assert body.startswith("# 辛书\n\n")


def test_empty_output_fails_without_touching_md(tmp_path, worker_cmd, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "empty")
    root = _root(tmp_path)
    old = root / "books" / "B0009" / "md"
    old.mkdir(parents=True)
    (old / "旧.md").write_text("# 旧\n", encoding="utf-8")
    item = _book(root, "B0009", "壬书", "pptx", b"PK-fake-pptx")
    report = _one(root, tmp_path, item)[1]

    row = _item(report, "B0009")
    assert row["status"] == "failed"
    assert row["reason"] == "empty-output"
    assert (old / "旧.md").is_file()  # 旧 md 原样
    assert sorted(p.name for p in old.iterdir()) == ["旧.md"]
    assert not list((root / "books" / "B0009").glob(".md-build-*"))


def test_worker_error_is_convert_error(tmp_path, worker_cmd, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "bad")
    root = _root(tmp_path)
    item = _book(root, "B0010", "癸书", "docx", b"broken")
    job, report = _one(root, tmp_path, item)
    row = _item(report, "B0010")
    assert (row["status"], row["reason"]) == ("failed", "convert-error")
    assert "坏文件" in row["error"]
    assert not (root / "books" / "B0010" / "md").exists()
    assert not list((root / "books" / "B0010").glob(".md-build-*"))
    assert json.loads((job / "progress.json").read_text(encoding="utf-8"))["phase"] == "done"


def test_too_large_source_is_refused_before_work(tmp_path, worker_cmd):
    root = _root(tmp_path)
    big = root / "books" / "B0011" / "source" / "大书.docx"
    big.parent.mkdir(parents=True, exist_ok=True)
    with big.open("wb") as fh:
        fh.truncate(200 * 1024 * 1024 + 1)
    item = {
        "uid": "B0011",
        "title": "大书",
        "format": "docx",
        "path": "books/B0011/source/大书.docx",
        "sha256": "",
        "reason": "new",
    }
    report = _one(root, tmp_path, item)[1]
    row = _item(report, "B0011")
    assert (row["status"], row["reason"]) == ("failed", "too-large")
    assert not (root / "books" / "B0011" / "md").exists()


def test_worker_timeout_kills_process_group(tmp_path, worker_cmd, monkeypatch):
    pid_file = tmp_path / "worker.pid"
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    monkeypatch.setattr(_mod(), "WORKER_TIMEOUT", 2)

    root = _root(tmp_path)
    item = _book(root, "B0012", "慢书", "docx", b"PK-slow")
    t0 = time.time()
    report = _one(root, tmp_path, item)[1]
    assert time.time() - t0 < 30

    row = _item(report, "B0012")
    assert (row["status"], row["reason"]) == ("failed", "timeout")
    assert not (root / "books" / "B0012" / "md").exists()

    pid = int(pid_file.read_text(encoding="utf-8"))
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_source_missing_and_changed_are_reported(tmp_path):
    root = _root(tmp_path)
    missing = {
        "uid": "B0013",
        "title": "无书",
        "format": "docx",
        "path": "books/B0013/source/无书.docx",
        "sha256": "",
        "reason": "new",
    }
    job = _job(tmp_path, [missing], name="missing")
    assert _mod().convert(str(root), str(job)) == 0
    assert _item(_report(job), "B0013")["reason"] == "source-missing"

    item = _book(root, "B0014", "换书", "docx", b"PK-old", sha="0" * 64)
    job2 = _job(tmp_path, [item], name="changed")
    assert _mod().convert(str(root), str(job2)) == 0
    assert _item(_report(job2), "B0014")["reason"] == "source-changed"


def test_unsupported_format_is_reported(tmp_path, worker_cmd):
    root = _root(tmp_path)
    item = _book(root, "B0015", "杂书", "epub", b"PK-epub")
    report = _one(root, tmp_path, item)[1]
    assert _item(report, "B0015")["reason"] == "unsupported"


def test_missing_venv_fails_batch_with_venv_missing(tmp_path, monkeypatch):
    """没有 .venv-docs 且不建：整批 doc 格式落 venv-missing，校验不过。"""
    monkeypatch.delenv("BOOKS_DOC_WORKER_CMD", raising=False)
    docs = _mod()
    monkeypatch.setattr(docs.docenv, "ensure", lambda root: (False, f"没有 {root}，且 BOOKS_NO_DOCS_VENV 已禁用"))
    root = _root(tmp_path)
    item = _book(root, "B0016", "缺书", "docx", b"PK-no-venv")
    job = _job(tmp_path, [item])
    assert docs.convert(str(root), str(job)) == 0
    row = _item(_report(job), "B0016")
    assert (row["status"], row["reason"]) == ("failed", "venv-missing")
    assert ".venv-docs" in row["error"] or "BOOKS_NO_DOCS_VENV" in row["error"]
    assert not (root / "books" / "B0016" / "md").exists()


def test_overwrite_backs_up_old_md(tmp_path):
    root = _root(tmp_path)
    old = root / "books" / "B0017" / "md"
    old.mkdir(parents=True)
    (old / "旧.md").write_text("# 旧\n", encoding="utf-8")
    item = _book(root, "B0017", "补书", "txt", "# 新\n\n新正文\n".encode())
    report = _one(root, tmp_path, item)[1]

    row = _item(report, "B0017")
    assert row["status"] == "done"
    assert row["backup"].startswith("工作区/批次/覆盖-")
    backup = root / row["backup"]
    assert (backup / "旧.md").is_file()
    assert not (root / "books" / "B0017" / "md" / "旧.md").exists()
    assert (root / "books" / "B0017" / "md" / "补书.md").is_file()


def test_one_failure_does_not_stop_the_batch(tmp_path, worker_cmd, monkeypatch):
    root = _root(tmp_path)
    good = _book(root, "B0018", "好书", "txt", "# 好\n\n正文\n".encode())
    bad = _book(root, "B0019", "坏书", "docx", b"PK-bad")
    job = _job(tmp_path, [good, bad])

    monkeypatch.setenv("FAKE_MODE", "bad")  # 只影响 docx 那本
    assert _mod().convert(str(root), str(job)) == 0
    report = _report(job)
    assert report["summary"] == {"total": 2, "done": 1, "skipped": 0, "failed": 1}
    assert _item(report, "B0018")["status"] == "done"
    assert _item(report, "B0019")["status"] == "failed"


def test_skipped_items_land_in_report_not_on_disk(tmp_path):
    root = _root(tmp_path)
    item = _book(root, "B0020", "已书", "txt", "# 已\n\n正文\n".encode())
    item["skipped"] = True
    job = _job(tmp_path, [item])
    assert _mod().convert(str(root), str(job)) == 0
    report = _report(job)
    row = _item(report, "B0020")
    assert (row["status"], row["reason"]) == ("skipped", "up-to-date")
    assert report["summary"] == {"total": 1, "done": 0, "skipped": 1, "failed": 0}
    assert not (root / "books" / "B0020" / "md").exists()


FAKE_MD = """\
import os


class MarkItDown:
    def convert(self, source):
        text = os.environ.get("FAKE_MD_TEXT")
        if text is None:
            raise RuntimeError("坏 zip")

        class R:
            text_content = text

        return R()
"""


def _fake_markitdown(tmp_path):
    lib = tmp_path / "fakelib"
    lib.mkdir(exist_ok=True)
    (lib / "markitdown.py").write_text(FAKE_MD, encoding="utf-8")
    return {"PYTHONPATH": str(lib)}


def test_worker_function_prints_one_json_line(tmp_path):
    """真 worker：给个假 markitdown，验证 stdout 只有一行 JSON 且图被抽走。"""
    env = _fake_markitdown(tmp_path)
    env["FAKE_MD_TEXT"] = f"# T\n\n![x](data:image/png;base64,{PNG_B64})\n"
    out = tmp_path / "out"
    src = tmp_path / "a.docx"
    src.write_bytes(b"x")

    r = run_cli(
        "--root",
        tmp_path,
        "run",
        "docs_to_md",
        "worker",
        "--source",
        src,
        "--format",
        "docx",
        "--out",
        out,
        env=env,
    )
    lines = [x for x in r.stdout.splitlines() if x.strip()]
    assert len(lines) == 1, r.stdout + r.stderr
    payload = json.loads(lines[0])
    assert payload["ok"] is True, payload
    assert payload["images"] == 1
    assert (out / "images" / "img-1.png").is_file()
    assert "images/img-1.png" in (out / "body.md").read_text(encoding="utf-8")
    assert r.returncode == 0


def test_worker_reports_error_without_traceback(tmp_path):
    env = _fake_markitdown(tmp_path)
    src = tmp_path / "b.docx"
    src.write_bytes(b"x")
    r = run_cli(
        "--root",
        tmp_path,
        "run",
        "docs_to_md",
        "worker",
        "--source",
        src,
        "--format",
        "docx",
        "--out",
        tmp_path / "o",
        env=env,
    )
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "RuntimeError: 坏 zip" in payload["error"]
    assert r.returncode == 0
    assert "Traceback" not in r.stderr


def test_worker_requires_all_args(tmp_path):
    r = run_cli("--root", tmp_path, "run", "docs_to_md", "worker", "--source", "x")
    assert r.returncode == 2
    assert "worker 要 --source/--format/--out" in r.stderr
