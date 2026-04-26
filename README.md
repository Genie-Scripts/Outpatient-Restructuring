# 外来再編分析ダッシュボード（Outpatient-Restructuring）

東京医療センター経営企画室向け、外来再編のための戦略分析ダッシュボード。

## 双子サイト構成

| リポ | 役割 | 出力 |
|---|---|---|
| **Outpatient-Dashboard**（上流） | 医師フィードバック ＋ データパイプライン | feedback サイト |
| **Outpatient-Restructuring**（本リポ） | 経営企画・外来再編分析 | planning サイト |

両者は `data/aggregated/YYYY-MM/*.csv` を唯一の契約面として疎結合に分離されている。

## セットアップ

### 必要なもの

- Python 3.11+
- 上流リポ（`Outpatient-Dashboard`）が同じ階層に clone されているか、`scripts/fetch_upstream.sh` で取得

### ローカル開発

```bash
# 仮想環境
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# 上流データをシンボリックリンクで参照
ln -s ../Outpatient-Dashboard/data/aggregated data/aggregated
ln -s ../Outpatient-Dashboard/config/dept_classification.csv config/dept_classification.csv

# ビルド
python -m src.cli build --month 2026-04
```

### 本番（浅いクローン）

```bash
./scripts/fetch_upstream.sh    # 上流を depth=1 で取得し data/aggregated/ を準備
python -m src.cli build --all  # 全月をビルド
```

### 一括 deploy（fetch → build → commit → push）

```bash
./scripts/deploy.sh                    # 全月再ビルドして push
./scripts/deploy.sh --month 2026-04    # 単一月のみ
./scripts/deploy.sh --skip-fetch       # symlink 想定で fetch_upstream をスキップ
```

`deploy.sh` は内部で `fetch_upstream.sh` → `build` → `git add` →
`git commit` → `git push` を実行し、macOS 通知でステータスを表示します。
フッタの「医師フィードバックサイト」リンクは
`FEEDBACK_URL` 環境変数で上書き可能（既定: Dashboard の GitHub Pages URL）。

両サイト（Dashboard + Restructuring）を一括で更新する場合は
`~/dev/ai-apps/deploy_all.sh` を使ってください。
Dashboard の最新集計CSVを push してから本サイトを再ビルドします。

```bash
~/dev/ai-apps/deploy_all.sh                    # 両方とも自動検出
~/dev/ai-apps/deploy_all.sh 2026-05            # 単月指定
~/dev/ai-apps/deploy_all.sh --restructuring-only  # 本リポだけ更新
```

## ディレクトリ構成

```
src/
  core/                # 集計CSV読込・診療科分類・評価ロジック
  dashboards/          # monthly / dept_planning / hub
  cli.py
templates/             # Jinja2 テンプレ
static/                # 共通CSS/JS（warm light テーマ）
docs/                  # GitHub Pages 出力（planning サイト）
config/                # 上流から参照する設定（シンボリックリンク or 浅いクローン）
scripts/               # 運用スクリプト
tests/                 # pytest
```

## 環境変数

| 変数 | 既定値 | 意味 |
|---|---|---|
| `OUTPATIENT_AGGREGATED` | `./data/aggregated` | 集計CSVのルート |
| `OUTPATIENT_DEPT_CLASSIFICATION` | `./config/dept_classification.csv` | 診療科分類CSV |
| `OUTPATIENT_UPSTREAM_REPO` | `git@github.com:.../Outpatient-Dashboard.git` | `fetch_upstream.sh` で参照 |

## 関連ドキュメント

- `CLAUDE.md`: Claude Code 用の指示書
- 上流の集計CSV仕様: `Outpatient-Dashboard/src/aggregate.py` 参照
