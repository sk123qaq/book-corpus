#!/bin/bash
# OCR 整批 supervisor：直到底层撞 429（日额度用尽）为止，反复续跑驱动。
# 驱动在「队列满/429 退避用完」时会中止整批；整批中止不等于没额度：
#   10010 队列满是平台拥堵，等一会再续跑还能继续吃额度；
#   HTTP 429 才是日额度用尽（见 决议记录 2026-10-19T04:10 与普查说明 §6）。
# 所以本脚本在每轮结束后看 log 再决定：见 429 且零进展 → 收尾退出；否则续跑。
#
# 用法: nohup 本脚本 [先等待的 driver_pid] [最大轮次] >> /tmp/ocr-wave.log 2>&1 &
# 每轮驱动输出: /tmp/ocr-wave-runN.log，汇总写入 /tmp/ocr-wave.log
set -u
cd ~/文档/books || exit 1
BATCH=pdf-ocr-2026-10-19
MAINLOG=/tmp/ocr-wave.log
PY=工作区/.venv-pdf/bin/python
WAITPID="${1:-}"
MAX_RUNS="${2:-60}"

books_done() {   # 打印 "范围内已完成本数 范围内总本数"，以 库/<path>/md 是否存在为准
                 # 范围 = worklist 减去语种账里 lang=ja 的书（日文原版暂缓，不算未完成）
  python3 - <<'PY'
import csv, os
D = '工作区/批次/pdf-ocr-2026-10-19/defer/lang.tsv'
defer = set()
if os.path.isfile(D):
    for r in csv.DictReader(open(D, encoding='utf-8'), delimiter='\t'):
        if r.get('lang') == 'ja':
            defer.add(r['uid'])
n = tot = 0
for r in csv.DictReader(open('工作区/批次/pdf-ocr-2026-10-19/worklist.tsv', encoding='utf-8'),
                        delimiter='\t'):
    if r['uid'] in defer:
        continue
    tot += 1
    if os.path.isdir(os.path.join('库', r['path'], 'md')):
        n += 1
print(n, tot)
PY
}

close_out() {
  { echo
    echo "=== 收尾 $(date -Is) ==="
    python3 工作区/脚本/build_library.py --emit-json;      echo "build_library rc=$?"
    python3 工作区/脚本/gen_book_catalog.py;               echo "gen_book_catalog rc=$?"
    echo "--- 重建检索索引（每日新落地的书不重建就搜不到，约 7 分钟）"
    python3 工作区/脚本/build_para_index.py --rebuild --concepts; echo "build_para_index rc=$?"
    python3 工作区/脚本/verify_phase.py;                   echo "verify_phase rc=$?"
    $PY 工作区/脚本/pdf_md_batch.py --batch "$BATCH" --report
    echo "=== 收尾完成 $(date -Is) ==="
  } >> "$MAINLOG" 2>&1
}

echo "=== supervisor 起 $(date -Is) ===" >> "$MAINLOG"
if [ -n "$WAITPID" ]; then
  while kill -0 "$WAITPID" 2>/dev/null; do sleep 30; done
  echo "=== 在跑的 wave pid $WAITPID 已退出，接管 $(date -Is) ===" >> "$MAINLOG"
fi
# 再等一层：确认没有残留的 driver 进程（含它派生的 pilot），免得两个 driver 并发切同一批分片
while pgrep -f "^[^ ]*venv-pdf/bin/python [^ ]*pdf_md_batch.py" >/dev/null; do sleep 15; done

zero=0
for i in $(seq 1 "$MAX_RUNS"); do
  read -r before tot < <(books_done)
  runlog=/tmp/ocr-wave-run$i.log
  echo "=== run $i 开始 $(date -Is)，已完成 $before/$tot 本 ===" >> "$MAINLOG"
  $PY 工作区/脚本/pdf_md_batch.py --batch "$BATCH" --workers 3 > "$runlog" 2>&1
  rc=$?
  read -r after tot < <(books_done)
  echo "=== run $i 结束 $(date -Is) rc=$rc，已完成 $after/$tot 本（+$((after-before))） ===" >> "$MAINLOG"
  cat "$runlog" >> "$MAINLOG"
  if [ "$after" -ge "$tot" ]; then
    echo "全部 $tot 本完成，收尾" >> "$MAINLOG"; break
  fi
  # 额度用尽的真实回话是 HTTP 403 code 12001「已达每日页数上限」，不是 429（2026-10-19 实测）
  if grep -qE "每日页数上限|HTTP 429" "$runlog"; then
    echo "本轮日志见「已达每日页数上限」(HTTP 403 code 12001)：日额度用尽（平台页数可复核），收尾退出" >> "$MAINLOG"
    break
  fi
  if [ "$after" -eq "$before" ]; then
    zero=$((zero + 1))
    if [ "$zero" -ge 6 ]; then
      echo "连续 $zero 轮零进展且无额度错，停下待查" >> "$MAINLOG"; break
    fi
    echo "零进展（疑似拥堵），等 900 秒再试" >> "$MAINLOG"; sleep 900
  else
    zero=0
    if grep -q "本批中止" "$runlog"; then
      echo "本轮有进展但被队列满中止，等 600 秒续跑" >> "$MAINLOG"; sleep 600
    else
      sleep 15
    fi
  fi
done
close_out
