#!/usr/bin/env bash
# 把 record_demo.js 录下的片段拼成 showcase/demo.mp4。
# 唯一一处剪辑：问书那一段的等待（模型真跑 60–80 秒），剪掉中间，留下
# 「已快进 · 模型实际用了 N 秒」的字幕说明，其余全部原速。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/.build"
CLIPS="$BUILD/clips"
SEG="$BUILD/seg"
OUT="$HERE/.."
FONT="$(fc-match -f '%{file}' 'Noto Serif CJK SC:style=Bold')"

# 中间产物（.build/）不入库，重跑时先补录一遍，避免拿着空目录切片。
if [ ! -f "$BUILD/marks.json" ]; then
  echo "没有录屏痕迹（$BUILD/marks.json），先录一遍"
  node "$HERE/record_demo.js"
fi

rm -rf "$SEG"; mkdir -p "$SEG"

read -r SEND ANSWER ELAPSED ASK_WALL <<<"$(python3 - "$BUILD/marks.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))['ask']
print(m['sendAt'], m['answerAt'], m['elapsed'] or '', m['wall'])
PY
)"

# 时间点：等待段只留开头的进度，然后跳到回答落地前一秒
CUT_START=$(python3 -c "print(round(max(1.0, $SEND + 11.0), 2))")
CUT_RESUME=$(python3 -c "print(round($ANSWER - 1.0, 2))")
TOTAL_ASK="$ASK_WALL"

echo "问书：发送 $SEND → 回答 $ANSWER（模型用时 $ELAPSED s）；等待段留到 $CUT_START，$CUT_RESUME 处接回"

head=$(python3 -c "print(round($SEND + 4.2, 2))")
python3 - "$BUILD" "$ELAPSED" <<'PY'
import sys
build, elapsed = sys.argv[1], sys.argv[2]
open(build + '/skip.txt', 'w').write('已快进 · 模型实际用了 %s 秒' % elapsed)
PY

enc () { # enc <in> <out> <ss> <t|-> [extra vf]
  local src="$1" dst="$2" ss="$3" t="$4" extra="${5:-}"
  local vf="scale=1920:1080:flags=lanczos,fps=30,format=yuv420p"
  [ -n "$extra" ] && vf="$vf,$extra"
  if [ "$t" = "-" ]; then
    ffmpeg -v error -y -ss "$ss" -i "$src" -vf "$vf" -c:v libx264 -preset medium -crf 19 -an "$dst"
  else
    ffmpeg -v error -y -ss "$ss" -t "$t" -i "$src" -vf "$vf" -c:v libx264 -preset medium -crf 19 -an "$dst"
  fi
}

echo "— 编码片段"
enc "$CLIPS/card-title.webm" "$SEG/01.mp4" 0.35 4.60 "fade=t=in:st=0:d=0.55"
enc "$CLIPS/home.webm"       "$SEG/02.mp4" 0.35 9.90
enc "$CLIPS/browse.webm"     "$SEG/03.mp4" 0.35 -
enc "$CLIPS/ask.webm"        "$SEG/04.mp4" 0.35 "$CUT_START" \
   "drawtext=fontfile=$FONT:textfile=$BUILD/skip.txt:fontcolor=0xA8442F:fontsize=30:box=1:boxcolor=0xF3F0E7@0.94:boxborderw=16:x=(w-text_w)/2:y=h-190:enable='gte(t,$head)'"
enc "$CLIPS/ask.webm"        "$SEG/05.mp4" "$CUT_RESUME" -
enc "$CLIPS/card-outro.webm" "$SEG/06.mp4" 0.35 4.60 "fade=t=out:st=3.9:d=0.7"

: > "$SEG/list.txt"
for f in "$SEG"/0*.mp4; do echo "file '$f'" >> "$SEG/list.txt"; done

echo "— 拼接"
ffmpeg -v error -y -f concat -safe 0 -i "$SEG/list.txt" -c copy -movflags +faststart "$OUT/demo.mp4"

echo "— 配乐"
# 原创铺底：脚本合成（音频里没有一个采样来自外部素材），-24 LUFS 垫在画面下面
if command -v uv >/dev/null 2>&1; then
  uv run --quiet --with numpy python tools/make_music.py
else
  python3 tools/make_music.py
fi
ffmpeg -v error -y -i "$OUT/demo.mp4" -i "$BUILD/music.wav" -map 0:v:0 -map 1:a:0 \
  -c:v copy -c:a aac -b:a 192k -af "loudnorm=I=-24:TP=-6:LRA=7" \
  -movflags +faststart -shortest "$BUILD/demo+mux.mp4"
mv "$BUILD/demo+mux.mp4" "$OUT/demo.mp4"

ffprobe -v error -show_entries format=duration,size -of default=nw=1 "$OUT/demo.mp4"
echo "— 封面"
ffmpeg -v error -y -ss 32 -i "$OUT/demo.mp4" -frames:v 1 -q:v 3 "$OUT/poster.jpg"
echo "写好了：$OUT/demo.mp4"
