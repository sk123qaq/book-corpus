#!/bin/bash
# 【已弃用 2026-10-19】被 wave_supervisor.sh 取代：本脚本只在驱动退出后跑收尾，不处理
# 「被 10010 队列满中止但还有额度」与「额度用尽（HTTP 403 code 12001）」的区分，额度没用完也会直接收尾。
# 保留仅供 决议记录 2026-10-19 的那两条引用；要用请用 wave_supervisor.sh。
#
# 等 OCR 整批进程退出（自然跑完或 deferred/abort 自停）后，按 handoff 的 Done means 收尾。
# 只在 wave 退出后跑，避免与正在写 库/**/md/ 的批次抢（箱 5 重建哈希会因中途状态假失败）。
# 用法: nohup 本脚本 <driver_pid> > /tmp/ocr-close.log 2>&1 &
# 日志: /tmp/ocr-close.log
set -u
PID="${1:?需要 pdf_md_batch 的 pid}"
cd ~/文档/books || exit 1
while kill -0 "$PID" 2>/dev/null; do sleep 30; done
echo "=== wave pid $PID 已退出于 $(date -Is) ==="
echo
echo "=== build_library.py --emit-json ==="
python3 工作区/脚本/build_library.py --emit-json
echo "rc=$?"
echo
echo "=== gen_book_catalog.py ==="
python3 工作区/脚本/gen_book_catalog.py
echo "rc=$?"
echo
echo "=== verify_phase.py ==="
python3 工作区/脚本/verify_phase.py
echo "rc=$?"
echo
echo "=== pdf_md_batch.py --report ==="
工作区/.venv-pdf/bin/python 工作区/脚本/pdf_md_batch.py --batch pdf-ocr-2026-10-19 --report
echo "=== 收尾完成 $(date -Is) ==="
