#!/bin/sh
# 逐条跑 OpsRunner 的规则。TLC 遇到第一条违规就停，所以每条规则单独跑一遍。
#
# 用法：.agents/skills/verify-ops-write-lock/scripts/check.sh [输出目录]
# 需要 java 11+ 与 tla2tools.jar。java 从 PATH 取；jar 按几个常见落点找。
# 可用 JAVA / TLA2TOOLS 覆盖。
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
spec="$here/spec"
out=${1:-"$here/out"}
JAVA=${JAVA:-$(command -v java 2>/dev/null || true)}
TLA2TOOLS=${TLA2TOOLS:-}
if [ -z "$TLA2TOOLS" ]; then
  for cand in "$HOME/.local/share/tla2tools/tla2tools.jar" \
              "$HOME/.local/share/tlaplus/tla2tools.jar" \
              /usr/share/java/tla2tools.jar \
              /usr/local/share/tla2tools/tla2tools.jar \
              /opt/tla2tools/tla2tools.jar; do
    [ -f "$cand" ] && { TLA2TOOLS=$cand; break; }
  done
fi
# TRUE = 当前实现（写子进程通过 pass_fds 继承锁 fd）；FALSE = 历史基线对照（锁仅在服务进程手里）。
CHILD_HOLDS_LOCK=${CHILD_HOLDS_LOCK:-TRUE}

[ -n "$JAVA" ] && [ -x "$JAVA" ] || {
  echo "找不到 java：PATH 里没有（用 JAVA=/path/to/java 指定）" >&2; exit 2; }
[ -n "$TLA2TOOLS" ] && [ -f "$TLA2TOOLS" ] || {
  echo "找不到 tla2tools.jar：用 TLA2TOOLS=/path/to/tla2tools.jar 指定" >&2
  echo "（下载：https://github.com/tlaplus/tlaplus/releases）" >&2; exit 2; }
mkdir -p "$out" || exit 2

# 规则名 : 中文说明。两处都改，别只改一处。
rules="WriterHoldsLock NoTwoWriteChildren NoLiveOrphan NoHttpWithChild CancelledNeverRuns"

fail=0
for rule in $rules; do
  cfg="$out/$rule.cfg"
  cat >"$cfg" <<EOF
SPECIFICATION Spec
CONSTANTS
    Jobs = {J1, J2}
    ChildHoldsLock = $CHILD_HOLDS_LOCK
INVARIANTS
    $rule
CHECK_DEADLOCK FALSE
EOF
  log="$out/$rule.log"
  (cd "$out" && "$JAVA" -XX:+UseParallelGC -cp "$TLA2TOOLS" tlc2.TLC \
      -config "$cfg" -workers 1 -cleanup "$spec/OpsRunnerSpec.tla" >"$log" 2>&1)
  if grep -q "No error has been found" "$log"; then
    states=$(sed -n 's/^\([0-9,]*\) states generated, \([0-9,]*\) distinct states found.*/\2/p' "$log" | tail -1)
    printf 'PASS  %-20s %s 个不同状态\n' "$rule" "${states:-?}"
  elif grep -q "is violated" "$log"; then
    fail=$((fail + 1))
    path=$(grep -o '^State [0-9]*: <[A-Za-z]*' "$log" |
           sed 's/^State [0-9]*: <//' | paste -sd ' ' -)
    printf 'FAIL  %-20s 最短反例：%s\n' "$rule" "${path:-见 $log}"
  else
    fail=$((fail + 1))
    printf 'ERROR %-20s 看 %s\n' "$rule" "$log"
  fi
done

echo
echo "ChildHoldsLock=$CHILD_HOLDS_LOCK"
if [ "$fail" -eq 0 ]; then
  echo "全部规则通过。"
  exit 0
else
  echo "$fail 条规则没通过（崩溃退出或违规）。逐条读 $out/<规则>.log，"
  echo "或用 pstack 的 /statechart 把每条反例画成状态图。"
  exit 1
fi
