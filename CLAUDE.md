# Claude Code への指示（Outpatient-Restructuring）

## このプロジェクトは何か

東京医療センター経営企画室のための、**外来再編分析**ダッシュボードシステム。
集計済みCSVを上流リポ（`Outpatient-Dashboard`）から受け取り、
枠再編・時間帯リソース最適化・薬再診逆紹介などの戦略分析を静的HTMLで可視化する。

**双子サイトの片割れ**：
- 上流（既存）: `Outpatient-Dashboard` … 医師フィードバック ＋ データパイプライン
- このリポ: `Outpatient-Restructuring` … 経営企画・外来再編分析

両者は **`data/aggregated/` の集計CSVを唯一の契約面**として疎結合になっている。
このリポは生データには一切触れない。匿名IDで完結する。

## 必ず守るべき原則

### 1. データ取り扱い

- **生データ・匿名化キーには一切アクセスしない**
- 入力は `data/aggregated/YYYY-MM/*.csv` のみ
- aggregated/ ディレクトリは Git にコミットしない（上流リポから取得する運用）
- 医師実名・予約名称マスタを参照する処理は禁止

### 2. LLMには計算をさせない

- 数値処理・集計・評価は全て Python で実装
- LLMはハイライトの文章生成のみに使う
- 上流リポと同じ原則。LLM導入は当面後回し

### 3. 上流リポへの依存

- `aggregate.py` 相当のロジックはこのリポには持たない
- 集計に新しいカラムが必要になったら **上流リポへ PR** を出す
- このリポでは集約済みCSVから派生計算するのみ

### 4. 静的HTML出力

- Jinja2テンプレートで生成
- Chart.js（CDN経由）でグラフ描画
- 複雑なJavaScriptフレームワーク不使用
- サーバ不要、GitHub Pages で完結

## 上流リポからのデータ受け取り

**ローカル開発**：シンボリックリンク
```bash
ln -s ../Outpatient-Dashboard/data/aggregated data/aggregated
ln -s ../Outpatient-Dashboard/config/dept_classification.csv config/dept_classification.csv
```

**本番（CI等）**：浅いクローン
```bash
scripts/fetch_upstream.sh   # 上流の最新 main を depth=1 で取得し data/aggregated/ を取り込む
```

環境変数で上書き可能：
- `OUTPATIENT_AGGREGATED` … aggregated CSV のルートパス
- `OUTPATIENT_DEPT_CLASSIFICATION` … 診療科分類CSVのパス

## コーディング規約

- Python 3.11+、型ヒント必須
- docstring は Google スタイル
- Ruff でリント、Black でフォーマット
- 関数は単一責務、1関数50行以内を目安
- モジュール名・関数名は英語、コメント・docstringは日本語OK

## ディレクトリ構成

```
src/                 # ロジック本体
  core/              # data_loader / classify など（上流からコピー）
  dashboards/        # monthly / dept_planning / hub
  cli.py
templates/           # Jinja2テンプレート
static/              # 共通CSS/JS（warm light テーマ）
config/              # シンボリックリンク or 浅いクローン経由で参照
scripts/             # fetch_upstream.sh, build.sh
docs/                # GitHub Pages 出力
tests/               # pytest
```

## 初期スコープ（Phase 2）

最小構成でまず動かす：

1. `src/core/` を上流からコピー（data_loader / classify / grading）
2. `monthly` ダッシュボード移植（経営トレンド）
3. `dept_planning` 新規実装（旧 dept_drilldown の B セクション部分のみ）
4. `index.html`（テーマナビ・ハブ）
5. `cli.py` で 1〜4 を統合ビルド

他のテーマ（slot/heatmap/drug_revisit）は Phase 3 以降に順次移植。

## 実装上の注意

- 集計CSVの列名は上流の `aggregate.py` と一致させる必要がある
- 上流の集計CSVの仕様変更を検知したら、このリポのテストも追従させる
- 双方の `dept/YYYY-MM/CODE.html` は **目的が違うだけで同じ科コードを使う** → URL構造を揃える
- 相互リンク用に「もう一方のビューを開く」を科ページ末尾に置く（URLは config で持つ）
