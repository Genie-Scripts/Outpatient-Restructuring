"""外来再編分析ダッシュボードのCLI。

サブコマンド:
    build      指定月（または全月）のダッシュボードを生成
    list       利用可能な集計月を表示

使用例:
    python -m src.cli build --month 2026-04
    python -m src.cli build --all
    python -m src.cli build --all --local      # 実名版（local/aggregated → local/docs）
    python -m src.cli list
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src import paths
from src.core.data_loader import list_available_months
from src.dashboards.dept_planning import build_dept_planning
from src.dashboards.hub import build_hub
from src.dashboards.monthly import build_monthly_dashboard
from src.dashboards.themes import build_all_themes

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_one_month(month: str, docs_root: Path) -> None:
    """指定月の monthly + dept_planning + テーマページを生成する。"""
    logger.info("=== %s のビルド開始 ===", month)

    build_monthly_dashboard(
        month=month,
        output_path=docs_root / "monthly" / f"{month}.html",
        aggregated_root=paths.aggregated_root(),
        templates_dir=paths.templates_dir(),
        classification_path=paths.classification_path(),
        targets_path=paths.targets_path(),
    )

    build_dept_planning(
        month=month,
        aggregated_root=paths.aggregated_root(),
        templates_dir=paths.templates_dir(),
        output_dir=docs_root / "dept" / month,
        classification_path=paths.classification_path(),
    )

    build_all_themes(
        month=month,
        aggregated_root=paths.aggregated_root(),
        templates_dir=paths.templates_dir(),
        output_dir=docs_root / "themes" / month,
        classification_path=paths.classification_path(),
    )

    logger.info("=== %s のビルド完了 ===", month)


def cmd_build(args: argparse.Namespace) -> int:
    if getattr(args, "local", False):
        # --local: ローカル実名版。local/aggregated → local/docs に切替。
        # 上流（Dashboard 側 --no-anon ビルド）が local/aggregated/ を埋めている前提。
        local_root = paths.PROJECT_ROOT / "local"
        if not args.aggregated_root:
            args.aggregated_root = str(local_root / "aggregated")
        if not args.docs_dir:
            args.docs_dir = str(local_root / "docs")
        logger.info("--local: aggregated=%s, docs=%s", args.aggregated_root, args.docs_dir)

    docs_root = paths.docs_dir(args.docs_dir)
    aggregated_root = paths.aggregated_root(args.aggregated_root)

    if not aggregated_root.exists():
        logger.error("集計ディレクトリが存在しません: %s", aggregated_root)
        if getattr(args, "local", False):
            logger.error("先に Dashboard 側で `python -m src.cli run-all --no-anon` を実行するか、")
            logger.error("scripts/fetch_upstream.sh --local で取り込んでください。")
        else:
            logger.error("scripts/fetch_upstream.sh で取得するか、シンボリックリンクを張ってください。")
        return 2

    if args.all:
        months = list_available_months(aggregated_root)
        if not months:
            logger.error("利用可能な月がありません")
            return 2
        for m in months:
            _build_one_month(m, docs_root)
    elif args.month:
        _build_one_month(args.month, docs_root)
    else:
        # デフォルト: 最新月
        months = list_available_months(aggregated_root)
        if not months:
            logger.error("利用可能な月がありません")
            return 2
        logger.info("月指定なし → 最新月 (%s) を生成", months[-1])
        _build_one_month(months[-1], docs_root)

    # ハブを最後に生成（生成済みのファイルを走査するため）
    build_hub(
        docs_dir=docs_root,
        templates_dir=paths.templates_dir(),
        aggregated_root=aggregated_root,
        classification_path=paths.classification_path(),
        feedback_url=args.feedback_url,
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    aggregated_root = paths.aggregated_root(args.aggregated_root)
    months = list_available_months(aggregated_root)
    if not months:
        print("(利用可能な月はありません)")
        return 1
    print(f"集計ルート: {aggregated_root}")
    print(f"利用可能な月 ({len(months)} 件):")
    for m in months:
        print(f"  - {m}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="outpatient-restructuring",
        description="外来再編分析ダッシュボードのビルダ",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="ダッシュボードをビルドする")
    p_build.add_argument("--month", help="対象月 YYYY-MM（省略時は最新月）")
    p_build.add_argument("--all", action="store_true", help="全月を対象にビルド")
    p_build.add_argument("--aggregated-root", help="集計CSVのルート（既定: paths.py）")
    p_build.add_argument("--docs-dir", help="出力先（既定: docs/）")
    p_build.add_argument(
        "--local",
        action="store_true",
        help="ローカル確認専用（Gitコミット不可）。"
             "local/aggregated を入力、local/docs を出力にする実名版ビルド。",
    )
    p_build.add_argument(
        "--feedback-url",
        help="医師フィードバックサイトのURL（フッタの相互リンク用）",
    )

    p_list = sub.add_parser("list", help="利用可能な月を表示")
    p_list.add_argument("--aggregated-root")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "build":
        return cmd_build(args)
    if args.command == "list":
        return cmd_list(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
