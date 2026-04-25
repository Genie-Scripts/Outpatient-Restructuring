#!/usr/bin/env bash
#
# 上流リポ（Outpatient-Dashboard）から集計CSVと診療科分類CSVを取得する。
# 浅いクローン（depth=1）で必要最小限を取り込み、ローカルの data/aggregated/ と
# config/dept_classification.csv を最新化する。
#
# 環境変数:
#   OUTPATIENT_UPSTREAM_REPO  上流リポのURL（既定: 下記の DEFAULT_REPO）
#   OUTPATIENT_UPSTREAM_REF   ブランチ/タグ/コミット（既定: main）
#   UPSTREAM_CACHE_DIR        クローン先（既定: .cache/upstream）
#
# 使い方:
#   ./scripts/fetch_upstream.sh
#
set -euo pipefail

DEFAULT_REPO="https://github.com/Genie-Scripts/Outpatient-Dashboard.git"
REPO_URL="${OUTPATIENT_UPSTREAM_REPO:-$DEFAULT_REPO}"
REF="${OUTPATIENT_UPSTREAM_REF:-main}"
CACHE_DIR="${UPSTREAM_CACHE_DIR:-.cache/upstream}"

# プロジェクトルート（このスクリプトの親ディレクトリの親）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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
echo "[fetch_upstream] data/aggregated/ を反映"
mkdir -p data/aggregated
rsync -a --delete "$CACHE_DIR/data/aggregated/" data/aggregated/

echo "[fetch_upstream] config/ を反映"
mkdir -p config
cp "$CACHE_DIR/config/dept_classification.csv" config/dept_classification.csv
if [ -f "$CACHE_DIR/config/dept_targets.csv" ]; then
  cp "$CACHE_DIR/config/dept_targets.csv" config/dept_targets.csv
fi

echo "[fetch_upstream] 完了"
echo "  aggregated: $(ls data/aggregated 2>/dev/null | wc -l | tr -d ' ') ヶ月分"
echo "  config:     $(ls config 2>/dev/null | wc -l | tr -d ' ') ファイル"
