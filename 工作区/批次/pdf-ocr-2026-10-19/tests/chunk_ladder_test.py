#!/usr/bin/env python3
"""pdf_md_batch.py 的分片阶梯离线测试：桩掉 run_pilot，不调接口。

跑法: 工作区/.venv-pdf/bin/python 工作区/批次/pdf-ocr-2026-10-19/tests/chunk_ladder_test.py

九个用例：上传断连切半（两半都成）、一路失败停在 <25 页、接口队列满只退避不切分、
文件过大类错误一次就切半、token 类错误直接停、日额度用尽（403/12001）中止整批、切片页数公式、
平台判 job failed 后丢 jobId 重提（成功 / 重提有上限）。
用例 10 与 11 锁额度用尽后的中断语义：在飞的 pilot 在宽限期后被收掉、被中断的分片记
interrupted 且不增失败次数、下次按账上 jobId 续跑而不是重新提交。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

REPO = "/home/shike/文档/books"
SRC = os.path.join(REPO, "工作区/脚本/pdf_md_batch.py")
spec = importlib.util.spec_from_file_location("pmb", SRC)
pmb = importlib.util.module_from_spec(spec)
sys.modules["pmb"] = pmb
spec.loader.exec_module(pmb)

REAL_SRC = pmb.__dict__.get("REAL_SRC") or (
    "库/人文社科/宗教与神话/中国神话史.袁珂1986/source/"
    "中国神话史.袁珂1986 (中国神话史.袁珂1986.pdf) (z-library.sk, 1lib.sk, z-lib.sk).pdf")
CALLS = []
WAITS = []
CTX = []


def make_ctx(root, queue_rounds=2):
    ctx = {"batch": root, "batch_name": "unit", "out": os.path.join(root, "out"),
           "tmp": os.path.join(root, "tmp"), "logs": os.path.join(root, "logs"),
           "work": os.path.join(root, "work"), "review": os.path.join(root, "review"),
           "land": False, "force": False, "replace": False, "api": True,
           "assemble_only": None, "queue_rounds": queue_rounds, "stamp": "unit",
           "abort": "",
           "pilots": {}, "pilot_lock": pmb.threading.Lock(), "cancelling": False,
           "canceller": None,
           "tool_version": "stub"}
    for d in ("out", "tmp", "logs", "work", "review"):
        os.makedirs(ctx[d], exist_ok=True)
    return ctx


def stub_transport(ctx, uid, a, b, cdir, job, on_job, log):
    """网络断：pilot 抛异常的样子，日志里有 Traceback 与 ConnectionError。"""
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    log.write("  桩：%d-%d 上传断连\n" % (a, b))
    return 1, ["Traceback (most recent call last):", "requests.exceptions.ConnectionError: reset"]


def stub_split_then_ok(ctx, uid, a, b, cdir, job, on_job, log):
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    if b - a + 1 > 40:
        log.write("  桩：%d-%d 上传断连\n" % (a, b))
        return 1, ["Traceback (most recent call last):", "TimeoutError: read timed out"]
    on_job("9999%d" % a)
    os.makedirs(os.path.join(cdir, "pages"), exist_ok=True)
    with open(os.path.join(cdir, "paddle_run.json"), "w", encoding="utf-8") as fh:
        json.dump({"per_page": [{"page": p, "chars": 1} for p in range(a, b + 1)]}, fh)
    return 0, []


def stub_queue_full(ctx, uid, a, b, cdir, job, on_job, log):
    """官方错误码 10010：提交队列已满，切小不解决排队。"""
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    log.write("  桩：%d-%d 队列满\n" % (a, b))
    return 1, ['提交 HTTP 400',
               '{"traceId":"t","code":10010,"msg":"任务提交队列已满，请稍后重试"}']


def stub_too_large(ctx, uid, a, b, cdir, job, on_job, log):
    """文件过大/超页：切小才有意义。"""
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    log.write("  桩：%d-%d 文件过大\n" % (a, b))
    return 1, ['提交 HTTP 413', '{"code":10003,"msg":"File size exceeds limit"}']


def stub_fatal(ctx, uid, a, b, cdir, job, on_job, log):
    """token 不对：重试与切分都没用。"""
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    log.write("  桩：%d-%d Token 无效\n" % (a, b))
    return 1, ['提交 HTTP 403', '{"code":10004,"msg":"Token Invalid"}']


def stub_quota(ctx, uid, a, b, cdir, job, on_job, log):
    """日额度用尽：2026-10-19 实测的真实回话——HTTP 403 code 12001，不是 429。
    要中止整批，不能当鉴权错，也不能切分。"""
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    log.write("  桩：%d-%d 已达每日页数上限\n" % (a, b))
    return 1, ['提交 HTTP 403',
               '{"traceId":"t","code":12001,"msg":"已达每日页数上限"}']


def stub_dead_job(ctx, uid, a, b, cdir, job, on_job, log):
    """平台把任务判 failed（state=failed）：新提交的 job 也立即 failed，重提救不回来。
    2026-10-20 实测：B1623 末段 5 页两次续跑同一 jobId 都 failed，卡成 chunk-failed。"""
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    if not job:
        on_job("dead-%d" % a)
    log.write("  桩：job 回 state=failed\n")
    return 1, ["[5s] failed None/None", "未完成，state=failed"]


def _mark_done(cdir, a, b, job, on_job):
    on_job(job)
    os.makedirs(os.path.join(cdir, "pages"), exist_ok=True)
    with open(os.path.join(cdir, "paddle_run.json"), "w", encoding="utf-8") as fh:
        json.dump({"per_page": [{"page": p, "chars": 1} for p in range(a, b + 1)]}, fh)


DEAD_ONCE = {"left": 1}


def stub_dead_job_then_ok(ctx, uid, a, b, cdir, job, on_job, log):
    """第一次提交的 job 死了，丢掉 jobId 重提之后成。"""
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    if DEAD_ONCE["left"]:
        DEAD_ONCE["left"] -= 1
        on_job("dead-job")
        log.write("  桩：job dead-job 回 state=failed\n")
        return 1, ["[5s] failed None/None", "未完成，state=failed"]
    _mark_done(cdir, a, b, "live-%d" % a, on_job)
    return 0, []


def run_case(name, stub, npages, queue_rounds=2):
    root = "/tmp/pmb-" + name
    shutil.rmtree(root, ignore_errors=True)
    ctx = make_ctx(root, queue_rounds)
    CTX.clear()
    CTX.append(ctx)
    src = os.path.join(REPO, REAL_SRC)
    ranges, per_chunk = pmb.plan_ranges(npages, src)
    ledger, result = [], {"uid": "T1", "title": "テスト"}
    log = open(os.path.join(root, "logs", "T1.log"), "w", encoding="utf-8")
    pmb.run_pilot = stub
    ok = True
    for a, b in ranges:
        pmb.entry_for(ledger, a, b)
        pmb.save_ledger(ctx, "T1", ledger)
        if not pmb.work_chunk(ctx, "T1", ledger, a, b, log, result):
            ok = False
            break
    log.close()
    n = len(CALLS)
    print("== %s：每段 %d 页，plan=%s" % (name, per_chunk, ranges))
    print("   work_chunk 全成=%s result=%s" % (ok, {k: v for k, v in result.items() if k != "uid"}))
    for e in ledger:
        print("   %s attempts=%d status=%s jobId=%s"
              % (e["range"], e["attempts"], e["status"], e["jobId"]))
    print("   pilot 调用 %d 次: %s" % (n, CALLS))
    out = (ledger, result, list(CALLS), list(WAITS))
    CALLS.clear()
    WAITS.clear()
    return out


def stub_killed(ctx, uid, a, b, cdir, job, on_job, log):
    """额度耗尽时被 cancel_pilots() terminate 的 pilot：jobId 已落账，进程被收掉。"""
    os.makedirs(cdir, exist_ok=True)
    CALLS.append((a, b, job))
    on_job("jobX")
    log.write("  桩：%d-%d 被 terminate（rc=-15）\n" % (a, b))
    return -15, ["Closed"]


def case_interrupt_resume():
    print("\n### 用例 10：额度用尽收掉在飞的 pilot，被中断的分片记 interrupted 且不计失败次数")
    root = "/tmp/pmb-interrupt"
    shutil.rmtree(root, ignore_errors=True)
    ctx = make_ctx(root)
    CTX.clear()
    CTX.append(ctx)
    src = os.path.join(REPO, REAL_SRC)
    ranges, _ = pmb.plan_ranges(300, src)
    assert len(ranges) >= 2, ranges
    (a1, b1), (a2, b2) = ranges[0], ranges[1]
    log = open(os.path.join(root, "logs", "T1.log"), "w", encoding="utf-8")
    ledger, result = [], {"uid": "T1", "title": "x"}
    pmb.entry_for(ledger, a1, b1)
    pmb.entry_for(ledger, a2, b2)
    pmb.save_ledger(ctx, "T1", ledger)

    inflight = subprocess.Popen(["sleep", "30"])
    ctx["pilots"]["T2"] = inflight          # 另一本在飞的分片
    old_grace = pmb.CANCEL_GRACE
    pmb.CANCEL_GRACE = 0
    pmb.run_pilot = stub_quota
    assert not pmb.work_chunk(ctx, "T1", ledger, a1, b1, log, result)
    assert ctx["abort"] == "日额度用尽", ctx
    canceller = ctx["canceller"]
    assert canceller is not None, "有在飞 pilot 时应起取消器"
    t0 = time.time()
    canceller.join(10)
    assert inflight.poll() is not None, "在飞 pilot 应被 terminate"
    print("   在飞 pilot pid=%d 已被收掉（宽限 %d 秒，实际等 %.1f 秒）"
          % (inflight.pid, pmb.CANCEL_GRACE, time.time() - t0))
    pmb.CANCEL_GRACE = old_grace

    CALLS.clear()
    pmb.run_pilot = stub_killed
    res2 = {"uid": "T1", "title": "x"}
    assert not pmb.work_chunk(ctx, "T1", ledger, a2, b2, log, res2)
    e2 = pmb.entry_for(ledger, a2, b2)
    assert e2["status"] == "interrupted" and e2["jobId"] == "jobX", e2
    assert e2["attempts"] == 0, e2                 # 中断不计失败次数
    assert res2["status"] == "interrupted", res2
    print("   %s status=%s attempts=%d jobId=%s"
          % (e2["range"], e2["status"], e2["attempts"], e2["jobId"]))

    print("### 用例 11：interrupted 的分片下次按账上 jobId 续跑，不重新提交")
    calls = []

    def stub_resume(ctx, uid, a, b, cdir, job, on_job, log):
        calls.append((a, b, job))
        _mark_done(cdir, a, b, job or "x", on_job)
        return 0, []

    ctx["abort"] = ""                              # 次日新进程
    ctx["cancelling"] = False
    pmb.run_pilot = stub_resume
    res3 = {"uid": "T1", "title": "x"}
    assert pmb.work_chunk(ctx, "T1", ledger, a2, b2, log, res3)
    assert calls == [(a2, b2, "jobX")], calls
    assert e2["status"] == "done", e2
    print("   续跑带的是 --job jobX，这一段没重提")
    log.close()


def main():
    pmb.time.sleep = lambda s: WAITS.append(s)   # 退避不真等，只看它睡了几次

    print("### 用例 1：上传断连两次后切半，两半都成（60 页）")
    led, res, calls, waits = run_case("transport-split", stub_split_then_ok, 60)
    assert led[0]["status"] == "split" and led[0]["attempts"] == pmb.MAX_CHUNK_ATTEMPTS, led[0]
    assert [e["status"] for e in led[1:]] == ["done", "done"], led
    assert [e["range"] for e in led[1:]] == [[1, 30], [31, 60]], led
    assert res.get("status") is None, res

    print("\n### 用例 2：一直断连，切到 <25 页标记 failed；两半都提交，整本 chunk-failed（60 页）")
    led, res, calls, waits = run_case("transport-fail", stub_transport, 60)
    # 切分后左右两半都要走：左半到底失败不能把右半挡掉（2026-10-22 审计修掉的 `and` 短路）
    assert [e["range"] for e in led] == [[1, 60], [1, 30], [1, 15], [16, 30],
                                         [31, 60], [31, 45], [46, 60]], led
    assert [e["status"] for e in led] == ["split", "split", "failed", "failed",
                                          "split", "failed", "failed"], led
    assert all(e["attempts"] == pmb.MAX_CHUNK_ATTEMPTS for e in led), led
    assert res["status"] == "chunk-failed", res
    assert any(c[0] == 31 and c[1] == 60 for c in calls), calls

    print("\n### 用例 3：接口队列满只退避不切分，退避用完记 deferred（60 页）")
    led, res, calls, waits = run_case("queue-full", stub_queue_full, 60, queue_rounds=3)
    assert len(led) == 1 and led[0]["range"] == [1, 60], led
    assert led[0]["status"] == "queue-full", led[0]
    assert led[0]["queue_rounds"] == 3, led[0]
    assert res["status"] == "deferred", res
    assert len(calls) == 4 and waits == [20, 45, 90], (calls, waits)
    print("   同一段重试 %d 次，退避真的等了 %s 秒（已桩掉 sleep），没有切分" % (len(calls), waits))

    print("\n### 用例 4：文件过大类错误一次就切半，不重传同一段（60 页）")
    led, res, calls, waits = run_case("too-large", stub_too_large, 60)
    assert led[0]["attempts"] == 1 and led[0]["status"] == "split", led[0]
    assert led[1]["range"] == [1, 30] and led[1]["attempts"] == 1, led

    print("\n### 用例 5：token/鉴权类错误直接停，不切分不重试")
    led, res, calls, waits = run_case("fatal", stub_fatal, 60)
    assert len(led) == 1 and led[0]["status"] == "api-fatal", led
    assert res["status"] == "api-fatal", res
    assert len(calls) == 1 and waits == [], (calls, waits)

    print("\n### 用例 6：切片页数公式（MAX_SLICE_MB / 每页字节，下限 25 上限 150）")
    cap = pmb.MAX_SLICE_MB * 1e6
    for npages, size in ((518, 10.6e6), (1000, 100e6), (200, 80e6), (300, 12e6), (34, 11.8e6)):
        per_page = size / npages
        want = max(pmb.MIN_CHUNK_PAGES, min(pmb.MAX_CHUNK_PAGES, int(cap / per_page)))
        print("   %d 页 %.1f MB -> 每页 %.3f MB，每段 %d 页，约 %.1f MB/段"
              % (npages, size / 1e6, per_page / 1e6, want, want * per_page / 1e6))

    print("\n### 用例 7：日额度用尽（HTTP 403 code 12001）中止整批，不切分不重试")
    led, res, calls, waits = run_case("quota", stub_quota, 60)
    assert len(led) == 1 and led[0]["status"] == "quota", led
    assert res["status"] == "deferred", res
    assert len(calls) == 1 and waits == [], (calls, waits)
    assert CTX[0]["abort"] == "日额度用尽", CTX[0]

    print("\n### 用例 8：平台判任务 failed（state=failed）时丢掉 jobId 重提，不无限续跑死 job（60 页）")
    DEAD_ONCE["left"] = 1
    led, res, calls, waits = run_case("dead-job-then-ok", stub_dead_job_then_ok, 60)
    assert [c[2] for c in calls] == [None, None], calls   # 重提没带旧 jobId
    assert led[0]["status"] == "done" and led[0].get("dead_jobs") == 1, led[0]
    assert res.get("status") is None, res
    print("### 用例 9：重提也一直 failed，有上限，不会无限重提（60 页）")
    led, res, calls, waits = run_case("dead-job-forever", stub_dead_job, 60)
    first = [c for c in calls if c[0] == 1 and c[1] == 60]
    assert len(first) == pmb.MAX_DEAD_JOB_RESUBMIT + 1, (len(first), calls)  # 首次 + 3 次重提
    assert all(c[2] is None for c in first), first        # 每次都是新任务，不带死 jobId
    assert res["status"] == "chunk-failed", res
    print("   重提 %d 次后放弃，转切分/失败，不会死循环" % pmb.MAX_DEAD_JOB_RESUBMIT)

    print("\n用例 1 到 9 全过")
    case_interrupt_resume()
    print("\n用例 1 到 11 全过")


if __name__ == "__main__":
    main()
