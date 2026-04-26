#!/bin/bash
# deploy.sh — Restructuring サイトの再ビルド〜push を自動化
#
# 使い方:
#   ./scripts/deploy.sh                # 全月再ビルド（既定）
#   ./scripts/deploy.sh --month 2026-04  # 単一月のみ
#   ./scripts/deploy.sh --skip-fetch     # 上流クローン更新をスキップ（ローカル symlink 想定）
#
# 前提:
#   - Outpatient-Dashboard 側で deploy.sh を回し終えている
#     （data/aggregated/ が GitHub に push 済み）
#   - 仮想環境 .venv/ が用意されている
set -euo pipefail

# Homebrew (Apple Silicon) のパスを明示
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

LOG="/tmp/restructuring_deploy.log"

log() {
  echo "$@" | tee -a "$LOG"
}

notify() {
  osascript -e "display notification \"$1\" with title \"外来再編分析サイト\" subtitle \"$2\"" 2>/dev/null || true
}

error_dialog() {
  osascript -e "display dialog \"$1\" buttons {\"OK\"} with title \"エラー\" with icon caution" 2>/dev/null || true
  log "❌ $1"
}

trap 'error_dialog "予期せぬエラーで停止しました。詳細は $LOG を確認してください。"' ERR

log "=== $(date '+%Y/%m/%d %H:%M:%S') Restructuring deploy 開始 ==="
notify "処理を開始しました。" "🚀 deploy 開始"

# ── 引数パース ──
MONTH_ARG=""
SKIP_FETCH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --month) MONTH_ARG="$2"; shift 2 ;;
    --skip-fetch) SKIP_FETCH=1; shift ;;
    *) error_dialog "未知の引数: $1"; exit 1 ;;
  esac
done

# ── 0. リポジトリルートへ移動 & 仮想環境有効化 ──
cd "$(dirname "$0")/.."

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  log "✅ 仮想環境を有効化"
  PY="python"
else
  log "ℹ️  .venv/ が無い。システム Python を使用。"
  if command -v python3 > /dev/null 2>&1; then
    PY="python3"
  elif command -v python > /dev/null 2>&1; then
    PY="python"
  else
    error_dialog "python / python3 が見つかりません。Python 3.11+ をインストールしてください。"
    exit 1
  fi
  log "  → $PY ($(${PY} --version 2>&1))"
fi

# ── 1. 上流リポから集計CSV / 分類CSVを取得 ──
if [ "$SKIP_FETCH" -eq 0 ]; then
  log "🔄 上流（Outpatient-Dashboard）から最新データを取得中..."
  notify "上流データを取得中..." "fetch_upstream"
  if ! ./scripts/fetch_upstream.sh 2>&1 | tee -a "$LOG"; then
    error_dialog "fetch_upstream.sh に失敗しました。$LOG を確認してください。"
    exit 1
  fi
  log "✅ 上流データ取得完了"
else
  log "⏭️  --skip-fetch 指定。上流クローンをスキップ（symlink 想定）"
fi

# ── 2. ビルド ──
log "🔨 ビルド中..."
notify "ビルド中..." "build"

FEEDBACK_URL="${FEEDBACK_URL:-https://genie-scripts.github.io/Outpatient-Dashboard/}"

if [ -n "$MONTH_ARG" ]; then
  if ! $PY -m src.cli build --month "$MONTH_ARG" --feedback-url "$FEEDBACK_URL" 2>&1 | tee -a "$LOG"; then
    error_dialog "build --month $MONTH_ARG に失敗しました。$LOG を確認してください。"
    exit 1
  fi
else
  if ! $PY -m src.cli build --all --feedback-url "$FEEDBACK_URL" 2>&1 | tee -a "$LOG"; then
    error_dialog "build --all に失敗しました。$LOG を確認してください。"
    exit 1
  fi
fi
log "✅ ビルド完了"
notify "サイトの再生成が完了しました。" "✅ ビルド完了"

# ── 3. ホワイトリスト方式でステージ ──
# data/aggregated/ や config/dept_*.csv は .gitignore 対象（上流由来のため）
git add \
  docs/ \
  src/ \
  templates/ \
  static/ \
  scripts/ \
  tests/ \
  pyproject.toml \
  .gitignore \
  README.md \
  CLAUDE.md 2>/dev/null || true

# ── 3b. 禁止ファイルがステージされていないか念のため検査 ──
FORBIDDEN=$(git diff --cached --name-only | grep -E '^(data/aggregated/|config/dept_classification\.csv|config/dept_targets\.csv|config/master_key\.csv|data/raw/)' || true)
if [ -n "$FORBIDDEN" ]; then
  error_dialog "上流由来ファイルがステージされました（コミット禁止）: $FORBIDDEN"
  git reset HEAD -- $FORBIDDEN >> "$LOG" 2>&1 || true
  exit 1
fi

# ── 4. 変更がなければスキップ ──
if git diff --cached --quiet; then
  log "⚠️  変更なし。スキップ。"
  notify "変更なし。スキップしました。" "deploy"
  exit 0
fi

# ── 5. コミット ──
TAG="${MONTH_ARG:-all}"
MSG="Restructuring update (${TAG}): $(date '+%Y/%m/%d %H:%M')"
git commit -m "$MSG" 2>&1 | tee -a "$LOG"
log "✅ コミット: $MSG"

# ── 6. プッシュ ──
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
log "⬆️  push 中 (branch: $CURRENT_BRANCH)..."
notify "GitHubへ送信中..." "⬆️ push 中"
if ! git push origin "$CURRENT_BRANCH" 2>&1 | tee -a "$LOG"; then
  error_dialog "GitHubへのpushに失敗しました (branch: $CURRENT_BRANCH)。SSH/PAT を確認してください。"
  exit 1
fi

log "✅ push 完了 (branch: $CURRENT_BRANCH)"
log "=== $(date '+%Y/%m/%d %H:%M:%S') Restructuring deploy 完了 ==="
notify "GitHubへの保存が完了しました ($CURRENT_BRANCH)。" "✅ deploy 完了"
