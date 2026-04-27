#!/usr/bin/env bash
#
# 上流リポ（Outpatient-Dashboard）から集計CSVと診療科分類CSVを取得する。
#
# 通常モード（GitHub から浅いクローン）:
#   ./scripts/fetch_upstream.sh
#   → data/aggregated/ + config/dept_*.csv を最新化（公開済み匿名版）
#
# ローカル実名モード（同一マシン上の sibling リポから rsync）:
#   ./scripts/fetch_upstream.sh --local
#   → local/aggregated/ + config/dept_*.csv を、隣の Dashboard リポの
#     local/aggregated/（実名集計）から取り込む。GitHub にも .cache/ にもアクセスせず、
#     完全にローカルで完結する。
#
# 環境変数:
#   OUTPATIENT_UPSTREAM_REPO  通常モードでのリモートURL（既定: 下記の DEFAULT_REPO）
#   OUTPATIENT_UPSTREAM_REF   通常モードでのブランチ/タグ（既定: main）
#   UPSTREAM_CACHE_DIR        通常モードのクローン先（既定: .cache/upstream）
#   DASHBOARD_LOCAL_DIR       --local モードのソース（既定: $HOME/dev/ai-apps/Outpatient-Dashboard）
#
set -euo pipefail

DEFAULT_REPO="https://github.com/Genie-Scripts/Outpatient-Dashboard.git"
REPO_URL="${OUTPATIENT_UPSTREAM_REPO:-$DEFAULT_REPO}"
REF="${OUTPATIENT_UPSTREAM_REF:-main}"
CACHE_DIR="${UPSTREAM_CACHE_DIR:-.cache/upstream}"
DASH_DIR="${DASHBOARD_LOCAL_DIR:-$HOME/dev/ai-apps/Outpatient-Dashboard}"

# 引数パース
LOCAL_MODE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --local) LOCAL_MODE=1; shift ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
    *) echo "[fetch_upstream] 未知の引数: $1" >&2; exit 1 ;;
  esac
done

# プロジェクトルート（このスクリプトの親ディレクトリの親）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "$LOCAL_MODE" -eq 1 ]; then
  # ── ローカル実名モード ──
  SRC_AGG="$DASH_DIR/local/aggregated"
  SRC_CFG="$DASH_DIR/config"
  if [ ! -d "$SRC_AGG" ]; then
    echo "[fetch_upstream] Dashboard 側の local/aggregated が見つかりません: $SRC_AGG" >&2
    echo "  先に Dashboard リポで以下を実行してください:" >&2
    echo "    cd $DASH_DIR && python -m src.cli run-all --no-anon --no-llm" >&2
    exit 2
  fi

  echo "[fetch_upstream] [local] $SRC_AGG → local/aggregated/"
  mkdir -p local/aggregated
  rsync -a --delete "$SRC_AGG/" local/aggregated/

  echo "[fetch_upstream] [local] config/ を Dashboard リポから反映"
  mkdir -p config
  # シンボリックリンクで同一ファイルを指している場合（既存の運用）は cp をスキップ
  copy_if_different() {
    local src="$1" dst="$2"
    if [ -e "$dst" ] && [ "$src" -ef "$dst" ]; then
      echo "  [skip] $dst は $src と同一ファイル（symlink 等）"
    else
      cp "$src" "$dst"
    fi
  }
  copy_if_different "$SRC_CFG/dept_classification.csv" config/dept_classification.csv
  if [ -f "$SRC_CFG/dept_targets.csv" ]; then
    copy_if_different "$SRC_CFG/dept_targets.csv" config/dept_targets.csv
  fi

  echo "[fetch_upstream] [local] 完了"
  echo "  local/aggregated: $(ls local/aggregated 2>/dev/null | wc -l | tr -d ' ') ヶ月分"
  echo "  config:           $(ls config 2>/dev/null | wc -l | tr -d ' ') ファイル"
  exit 0
fi

# ── 通常モード（GitHub 浅いクローン） ──
mkdir -p "$(dirname "$CACHE_DIR")"

if [ -d "$CACHE_DIR/.git" ]; then
  echo "[fetch_upstream] 既存キャッシュを更新: $CACHE_DIR (ref=$REF)"
  git -C "$CACHE_DIR" fetch --depth=1 origin "$REF"
  git -C "$CACHE_DIR" checkout -q "$REF"
  git -C "$CACHE_DIR" reset --hard "origin/$REF"
else
  echo "[fetch_upstream] 浅いクローン: $REPO_URL ($REF) → $CACHE_DIR"
  git clone --depth=1 --branch "$REF" "$REPO_URL" "$CACHE_DIR"
fi

# 集計CSVと診療科分類を反映（rsync で安全に上書き）
# ただし data/aggregated や config の各ファイルがシンボリックリンクの場合、
# rsync --delete や cp が上流リポ（Dashboard）の実体を上書きしてしまうため、
# symlink を検出したら反映をスキップする（--skip-fetch 等価）。
# README.md / CLAUDE.md に記載の「ローカル symlink 運用」を安全に成立させる保護。
if [ -L data/aggregated ]; then
  echo "[fetch_upstream] data/aggregated はシンボリックリンクのため反映をスキップ"
  echo "  link target: $(readlink data/aggregated)"
  echo "  上流（GitHub）の状態を取り込みたい場合は symlink を解除してから再実行してください"
else
  echo "[fetch_upstream] data/aggregated/ を反映"
  mkdir -p data/aggregated
  rsync -a --delete "$CACHE_DIR/data/aggregated/" data/aggregated/
fi

echo "[fetch_upstream] config/ を反映"
mkdir -p config
copy_unless_symlink() {
  local src="$1" dst="$2"
  if [ -L "$dst" ]; then
    echo "  [skip] $dst はシンボリックリンク（target: $(readlink "$dst")）"
  else
    cp "$src" "$dst"
  fi
}
copy_unless_symlink "$CACHE_DIR/config/dept_classification.csv" config/dept_classification.csv
if [ -f "$CACHE_DIR/config/dept_targets.csv" ]; then
  copy_unless_symlink "$CACHE_DIR/config/dept_targets.csv" config/dept_targets.csv
fi

echo "[fetch_upstream] 完了"
echo "  aggregated: $(ls data/aggregated 2>/dev/null | wc -l | tr -d ' ') ヶ月分"
echo "  config:     $(ls config 2>/dev/null | wc -l | tr -d ' ') ファイル"
