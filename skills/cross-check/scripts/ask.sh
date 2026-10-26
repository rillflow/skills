#!/usr/bin/env bash
# cross-check: 相手のモデルのCLIを非対話で1回呼び、応答・stderr・metaを残す。
# 使い方: ask.sh <codex|claude> --prompt-file <F> --out-dir <D> [--cwd <dir>] [--timeout <秒>] [--model <id>] [--effort <low|medium|high|xhigh>]
# 終了コード: 0 成功 / 124 時間切れ / 2 使い方の誤り / 3 応答本文なし / 4 モデル未確認 / 5 モデル不一致(拒否・代替) / それ以外はCLIの終了コード
set -u

usage() { sed -n '3,4p' "$0" | sed 's/^# //' >&2; exit 2; }

[ $# -ge 1 ] || usage
SIDE="$1"; shift
case "$SIDE" in codex|claude) ;; *) usage ;; esac

PROMPT_FILE=""; OUT_DIR=""; CWD="$PWD"; TIMEOUT=600; MODEL=""; EFFORT="high"
while [ $# -gt 0 ]; do
  case "$1" in
    --prompt-file) PROMPT_FILE="${2:-}"; shift 2 ;;
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    --cwd) CWD="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --effort) EFFORT="${2:-}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done
[ -n "$PROMPT_FILE" ] && [ -f "$PROMPT_FILE" ] || { echo "prompt file not found: $PROMPT_FILE" >&2; exit 2; }
[ -n "$OUT_DIR" ] || usage
[ -d "$CWD" ] || { echo "cwd not found: $CWD" >&2; exit 2; }
case "$EFFORT" in low|medium|high|xhigh) ;; *) echo "effort must be low|medium|high|xhigh" >&2; exit 2 ;; esac
case "$TIMEOUT" in ''|*[!0-9]*) echo "timeout must be integer seconds" >&2; exit 2 ;; esac

mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%dT%H%M%S)-$$"
BASE="$OUT_DIR/$SIDE-$STAMP"
OUT="$BASE.out"; ERR="$BASE.err"; META="$BASE.meta.json"; RAW="$BASE.raw.json"
: > "$OUT"; : > "$ERR"

START_EPOCH=$(date +%s); START_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# プロンプトはファイルのリダイレクトでstdinに渡し、EOFを保証する。
# --modelを省いた場合は各CLIの既定モデルを使い、実際のモデルをmetaに記録する。
if [ "$SIDE" = codex ]; then
  MODEL_ARGS=()
  [ -n "$MODEL" ] && MODEL_ARGS=(-c "model=\"$MODEL\"")
  ( cd "$CWD" && exec codex exec --skip-git-repo-check -s read-only ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
      -c "model_reasoning_effort=\"$EFFORT\"" -C "$CWD" - ) \
    < "$PROMPT_FILE" > "$OUT" 2> "$ERR" &
else
  MODEL_ARGS=()
  [ -n "$MODEL" ] && MODEL_ARGS=(--model "$MODEL")
  # 書き込み系ツールを止め、権限モードをdefaultに固定する(-pでは承認要求が拒否される)。
  ( cd "$CWD" && exec claude -p ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} --effort "$EFFORT" --output-format json \
      --permission-mode default --disallowedTools Edit Write NotebookEdit ) \
    < "$PROMPT_FILE" > "$RAW" 2> "$ERR" &
fi
PID=$!

kill_tree() {
  local p="$1" sig="$2" c
  for c in $(pgrep -P "$p" 2>/dev/null); do kill_tree "$c" "$sig"; done
  kill "-$sig" "$p" 2>/dev/null
}

TIMED_OUT=()
while kill -0 "$PID" 2>/dev/null; do
  if [ $(( $(date +%s) - START_EPOCH )) -ge "$TIMEOUT" ]; then
    TIMED_OUT=(--timed-out)
    kill_tree "$PID" TERM; sleep 2; kill_tree "$PID" KILL
    break
  fi
  sleep 1
done
wait "$PID" 2>/dev/null; EXIT=$?

END_EPOCH=$(date +%s); END_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

python3 "$(dirname "$0")/collect_meta.py" --side "$SIDE" --model "$MODEL" --effort "$EFFORT" \
  --exit-code "$EXIT" ${TIMED_OUT[@]+"${TIMED_OUT[@]}"} --timeout "$TIMEOUT" \
  --started-at "$START_ISO" --ended-at "$END_ISO" --elapsed $((END_EPOCH - START_EPOCH)) \
  --prompt-file "$PROMPT_FILE" --cwd "$CWD" --out "$OUT" --err "$ERR" --raw "$RAW" --meta "$META"
