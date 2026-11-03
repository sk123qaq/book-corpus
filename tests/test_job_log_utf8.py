"""作业日志分页：日志尾部是多字节 UTF-8 的半截字符时，不能抛异常、不能原地踏步。

/api/jobs/<id>/log?offset=N 的翻页契约：next 是「已消费的完整字节数」，
半截字符退回给下一页；下一页从同一个 offset 再读就能拼出完整字符。
这里直接调 handler 的 api_job_log，用桩对象顶掉 HTTP 层。
"""

import importlib.util
import json
import types
from pathlib import Path

SERVER = (Path(__file__).resolve().parents[1] / "apps" / "ops" / "server.py")


def _load_server():
    spec = importlib.util.spec_from_file_location("ops_server_joblog_under_test", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Handler:
    """只实现 api_job_log 用到的那几个口：store.get / env.job_dir / send_json / fail。"""

    def __init__(self, job_dir, job_id="job-utf8"):
        self.job_dir = Path(job_dir)
        self.job_id = job_id
        self.sent = None
        jobs = {job_id: {"id": job_id, "state": "running"}}
        env = types.SimpleNamespace(job_dir=lambda jid: str(self.job_dir))
        self.app = types.SimpleNamespace(store=types.SimpleNamespace(get=jobs.get), env=env)

    def send_json(self, code, payload):
        self.sent = (code, payload)

    def fail(self, code, message):
        self.sent = (code, {"ok": False, "error": message})


def _page(mod, job_dir, offset=0, job_id="job-utf8"):
    handler = _Handler(job_dir, job_id)
    mod.Handler.api_job_log(handler, job_id, {"offset": [str(offset)]})
    assert handler.sent is not None, "api_job_log 没回响应"
    code, payload = handler.sent
    assert code == 200, payload
    return payload


def test_truncated_multibyte_tail_is_held_back(tmp_path):
    """文件末尾卡在「中」字的第二个字节：本页只吐完整部分，剩下的留给下一页。"""
    mod = _load_server()
    log = tmp_path / "log.txt"
    good = "第一行\n中"
    log.write_bytes(good.encode("utf-8") + "文".encode("utf-8")[:1])

    first = _page(mod, tmp_path)
    assert first["text"] == good, first["text"]
    assert first["next"] == len(good.encode("utf-8")), first
    assert first["eof"] is False

    # 后续字节补上：从同一个 offset 再读，半截字符能完整拼出来
    log.write_bytes(log.read_bytes() + "文".encode("utf-8")[1:] + "结束\n".encode("utf-8"))
    second = _page(mod, tmp_path, first["next"])
    assert second["text"] == "文结束\n", second["text"]
    assert second["next"] == log.stat().st_size == second["size"], second
    assert second["eof"] is True

    # 到尾巴之后再翻页：空文本、offset 不动、eof 仍为真
    third = _page(mod, tmp_path, second["next"])
    assert (third["text"], third["next"], third["eof"]) == ("", second["next"], True)


def test_bad_byte_at_page_start_still_advances(tmp_path):
    """段首就是坏字节：不能一直返回空文本并停在原地（会翻页死循环）。"""
    mod = _load_server()
    log = tmp_path / "log.txt"
    log.write_bytes(b"\xff\xfeabc\n")

    first = _page(mod, tmp_path)
    assert first["next"] > 0, "坏字节页必须前进"
    seen = first["next"]
    for _ in range(6):
        page = _page(mod, tmp_path, seen)
        assert page["next"] > seen or page["eof"], "offset 停在原地：%r" % page
        seen = page["next"]
        if page["eof"]:
            break
    assert seen == log.stat().st_size


def test_offset_past_eof_is_clamped(tmp_path):
    """offset 超过文件大小按文件尾算，不是 500。"""
    mod = _load_server()
    (tmp_path / "log.txt").write_bytes(b"abc\n")
    page = _page(mod, tmp_path, 9999)
    assert page["offset"] == 4 and page["next"] == 4 and page["eof"] is True


def test_unknown_job_is_404(tmp_path):
    mod = _load_server()
    handler = _Handler(tmp_path, "job-missing")
    handler.app.store = types.SimpleNamespace(get=lambda _jid: None)
    mod.Handler.api_job_log(handler, "job-missing", {"offset": ["0"]})
    code, payload = handler.sent
    assert code == 404 and payload.get("ok") is False


def test_secret_in_log_is_redacted(tmp_path):
    """翻页响应与 /api/file 同口径脱敏。"""
    mod = _load_server()
    (tmp_path / "log.txt").write_text("key=sk-abcdefghijklmnop\n", encoding="utf-8")
    page = _page(mod, tmp_path)
    assert "sk-abcdefghijklmnop" not in json.dumps(page, ensure_ascii=False), page
