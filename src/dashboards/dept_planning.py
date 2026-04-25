"""診療科×月の再編診断ダッシュボード生成。

旧 dept_drilldown のうち「再編分析」に該当するセクションだけを残し、
医師フィードバック寄りの内容（KPI評価・医師別内訳）は除外している。

出力構成:
    00 当月のミニサマリ（件数・初診率・紹介率・未来院率）
    01 時間帯ゾーン × 曜日 棒グラフ
    02 逆紹介候補（時間階級バケット表）
    03 薬再診スコア（医師×枠の上位）
    04 枠ミックス（予約名称ごとの初診率・件数・命名乖離）
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.classify import DeptClassifier, DeptInfo
from src.core.data_loader import (
    AggregatedData,
    list_available_months,
    load_aggregated_data,
)

logger = logging.getLogger(__name__)

DRUG_REVISIT_TOP_N = 15
SLOT_TOP_N = 20
RARE_SLOT_THRESHOLD = 5

TIMEZONE_ORDER = [
    "午前(〜12時)",
    "午後前半(12-15時)",
    "午後後半(15-17時)",
    "夕方以降(17時〜)",
]
WEEKDAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]

_TYPE_KEY = {"内科系": "naika", "外科系": "geka", "その他": "other"}
_TYPE_LABEL = {"naika": "内科", "geka": "外科", "other": "その他"}


@dataclass
class _DeptSummary:
    total: int
    sho: int
    sai: int
    shokai_sho: int
    mirain: int
    sho_rate: float
    shokai_rate: float
    mirain_rate: float


def _summary_for_dept(
    kpi_df: pd.DataFrame, dept: str, month: str
) -> _DeptSummary | None:
    row = kpi_df[
        (kpi_df["診療科名"] == dept) & (kpi_df["月"].astype(str) == month)
    ]
    if row.empty:
        return None
    r = row.iloc[0]
    return _DeptSummary(
        total=int(r["総件数"]),
        sho=int(r["初診件数"]),
        sai=int(r["再診件数"]),
        shokai_sho=int(r["紹介状あり初診"]),
        mirain=int(r["未来院件数"]),
        sho_rate=float(r["初診率"]),
        shokai_rate=float(r["紹介率"]),
        mirain_rate=float(r["未来院率"]),
    )


def _timezone_chart_data(tz_df: pd.DataFrame, dept: str) -> dict[str, Any]:
    sub = tz_df[tz_df["診療科名"] == dept]
    if sub.empty:
        return {"labels": [], "datasets": []}

    datasets: list[dict[str, Any]] = []
    for zone in TIMEZONE_ORDER:
        zone_df = sub[sub["時間帯ゾーン"] == zone]
        counts = []
        for wd in range(7):
            count = int(zone_df[zone_df["曜日"] == wd]["件数"].sum())
            counts.append(count)
        datasets.append({"label": zone, "data": counts})

    return {"labels": WEEKDAY_LABELS, "datasets": datasets}


def _reverse_referral(
    rr_df: pd.DataFrame, dept: str, month: str
) -> tuple[list[dict[str, Any]], int]:
    sub = rr_df[
        (rr_df["診療科名"] == dept)
        & (rr_df["月"].astype(str) == month)
        & (rr_df["初再診区分"] == "再診")
        & (rr_df["紹介状有無"] == "紹介状無し")
        & (rr_df["併科受診フラグ"] == "無")
        & (rr_df["診察前検査フラグ"] == "なし")
        & (rr_df["診察時間_階級"].isin(["0-4分", "5-9分"]))
    ]
    grouped = (
        sub.groupby("診察時間_階級")["件数"]
        .sum()
        .reset_index()
        .sort_values("診察時間_階級")
    )
    rows = [
        {"bucket": str(r["診察時間_階級"]), "count": int(r["件数"])}
        for _, r in grouped.iterrows()
    ]
    total = int(grouped["件数"].sum()) if not grouped.empty else 0
    return rows, total


def _drug_revisit_rows(
    drug_df: pd.DataFrame, dept: str, month: str
) -> list[dict[str, Any]]:
    """当月・当該科の薬再診スコア上位 N 行を返す。"""
    if drug_df.empty:
        return []
    sub = drug_df[
        (drug_df["診療科名"] == dept) & (drug_df["月"].astype(str) == month)
    ]
    if sub.empty:
        return []
    # スコア降順、NaN は最後尾
    sub = sub.sort_values("スコア", ascending=False, na_position="last").head(
        DRUG_REVISIT_TOP_N
    )
    rows = []
    for _, r in sub.iterrows():
        score = r.get("スコア")
        median = r.get("診察時間中央値_再診")
        rows.append(
            {
                "medic": str(r["医師匿名ID"]),
                "slot": str(r["予約名称"]) if pd.notna(r["予約名称"]) else "(未設定)",
                "sai": int(r["再診件数"]),
                "short_ratio": round(float(r["短時間再診比率"]) * 100, 1),
                "no_shokai_ratio": round(float(r["紹介状なし再診比率"]) * 100, 1),
                "median": float(median) if pd.notna(median) else None,
                "score": float(score) if pd.notna(score) else None,
            }
        )
    return rows


def _slot_rows(slot_df: pd.DataFrame, dept: str, month: str) -> list[dict[str, Any]]:
    """当月・当該科の予約名称ごとの初診率・件数を返す。"""
    if slot_df.empty:
        return []
    sub = slot_df[
        (slot_df["診療科名"] == dept) & (slot_df["月"].astype(str) == month)
    ]
    if sub.empty:
        return []

    # 予約名称 × 初再診区分 で集約
    pivot = (
        sub.groupby(["予約名称", "初再診区分"], dropna=False)["件数"]
        .sum()
        .unstack(fill_value=0)
    )
    sho_col = pivot.get("初診", pd.Series(0, index=pivot.index))
    sai_col = pivot.get("再診", pd.Series(0, index=pivot.index))

    # 紹介状あり初診の集計（slot_analysis は紹介状有無も含む）
    sho_shokai = (
        sub[(sub["初再診区分"] == "初診") & (sub["紹介状有無"] == "紹介状あり")]
        .groupby("予約名称")["件数"]
        .sum()
    )

    rows = []
    for name in pivot.index:
        sho = int(sho_col.get(name, 0))
        sai = int(sai_col.get(name, 0))
        total = sho + sai
        if total == 0:
            continue
        sho_rate = round(sho / total * 100, 1)
        flags: list[str] = []
        if total < RARE_SLOT_THRESHOLD:
            flags.append("稀用")
        is_mismatch = "初診" in str(name) and sho_rate < 50
        if is_mismatch:
            flags.append("命名乖離")
        rows.append(
            {
                "name": str(name) if pd.notna(name) else "(未設定)",
                "total": total,
                "sho": sho,
                "sai": sai,
                "shokai_sho": int(sho_shokai.get(name, 0)),
                "sho_rate": sho_rate,
                "is_mismatch": is_mismatch,
                "flags": flags,
            }
        )
    rows.sort(key=lambda x: -x["total"])
    return rows[:SLOT_TOP_N]


def _resolve_navigation(
    targets: list[DeptInfo],
    current: DeptInfo,
    months: list[str],
    current_month: str,
) -> dict[str, str | None]:
    """前後の科・前後の月への相対リンクを組み立てる。"""
    nav: dict[str, str | None] = {
        "prev_dept_href": None,
        "prev_dept_label": None,
        "next_dept_href": None,
        "next_dept_label": None,
        "prev_month_href": None,
        "next_month_href": None,
    }

    code_list = [t.code for t in targets]
    if current.code in code_list:
        idx = code_list.index(current.code)
        if idx > 0:
            prev = targets[idx - 1]
            nav["prev_dept_href"] = f"{prev.code}.html"
            nav["prev_dept_label"] = prev.name
        if idx < len(code_list) - 1:
            nxt = targets[idx + 1]
            nav["next_dept_href"] = f"{nxt.code}.html"
            nav["next_dept_label"] = nxt.name

    if current_month in months:
        i = months.index(current_month)
        if i > 0:
            nav["prev_month_href"] = f"../{months[i - 1]}/{current.code}.html"
        if i < len(months) - 1:
            nav["next_month_href"] = f"../{months[i + 1]}/{current.code}.html"

    return nav


def build_dept_planning(
    month: str,
    aggregated_root: Path,
    templates_dir: Path,
    output_dir: Path,
    classification_path: Path,
) -> list[Path]:
    """当該月の評価対象全科について再編診断HTMLを生成する。

    Args:
        month: "YYYY-MM" 形式
        aggregated_root: data/aggregated/ のパス
        templates_dir: Jinja2 テンプレ格納ディレクトリ
        output_dir: 出力先 (docs/dept/YYYY-MM/)
        classification_path: config/dept_classification.csv

    Returns:
        書き出した HTML パスのリスト
    """
    classifier = DeptClassifier(classification_path)
    data = load_aggregated_data(aggregated_root, month)
    months = list_available_months(aggregated_root)
    targets = classifier.evaluation_targets()

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("dept_planning.html")

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    latest_month = months[-1] if months else month

    for info in targets:
        summary = _summary_for_dept(data.referral_kpi, info.name, month)
        if summary is None or summary.total == 0:
            logger.info("データなしのためスキップ: %s", info.name)
            continue

        timezone_data = _timezone_chart_data(data.dept_timezone, info.name)
        rr_rows, rr_total = _reverse_referral(data.reverse_referral, info.name, month)
        drug_rows = _drug_revisit_rows(data.drug_revisit_score, info.name, month)
        slot_rows = _slot_rows(data.slot_analysis, info.name, month)

        type_key = _TYPE_KEY.get(info.type, "other")
        type_label = _TYPE_LABEL[type_key]
        nav = _resolve_navigation(targets, info, months, month)

        breadcrumb = [
            {"label": "ホーム", "href": "../../index.html"},
            {"label": "診療科", "href": f"index.html"},
            {"label": f"{month} ／ {info.name}", "href": None},
        ]

        html = template.render(
            title=f"{info.name} ／ {month}",
            site_title="外来再編分析",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            root_prefix="../../",
            latest_month=latest_month,
            active="dept",
            breadcrumb=breadcrumb,
            dept_name=info.name,
            type_key=type_key,
            type_label=type_label,
            month=month,
            summary={
                "total": summary.total,
                "sho_rate": summary.sho_rate,
                "shokai_rate": summary.shokai_rate,
                "mirain_rate": summary.mirain_rate,
            },
            timezone_data_json=json.dumps(timezone_data, ensure_ascii=False),
            reverse_referral=rr_rows,
            reverse_referral_total=rr_total,
            drug_revisit_rows=drug_rows,
            slot_rows=slot_rows,
            **nav,
            feedback_url=None,
        )

        out_path = output_dir / f"{info.code}.html"
        out_path.write_text(html, encoding="utf-8")
        generated.append(out_path)
        logger.info("再編診断HTML出力: %s", out_path)

    # 同月の科一覧 index.html も生成
    _build_dept_index(env, targets, month, output_dir, latest_month)

    return generated


def _build_dept_index(
    env: Environment,
    targets: list[DeptInfo],
    month: str,
    output_dir: Path,
    latest_month: str,
) -> Path:
    """同月内の科一覧ページを生成する（dept/<month>/index.html）。"""
    template = env.get_template("dept_index.html")
    grouped: dict[str, list[DeptInfo]] = {"内科系": [], "外科系": [], "その他": []}
    for t in targets:
        if (output_dir / f"{t.code}.html").exists():
            grouped.setdefault(t.type, []).append(t)

    groups = []
    for typ in ("内科系", "外科系", "その他"):
        items = grouped.get(typ, [])
        if not items:
            continue
        groups.append(
            {
                "name": typ,
                "type_key": _TYPE_KEY[typ],
                "items": [
                    {"code": t.code, "name": t.name, "href": f"{t.code}.html"}
                    for t in items
                ],
            }
        )

    breadcrumb = [
        {"label": "ホーム", "href": "../../index.html"},
        {"label": f"診療科（{month}）", "href": None},
    ]

    html = template.render(
        title=f"診療科一覧 ／ {month}",
        root_prefix="../../",
        latest_month=latest_month,
        active="dept",
        breadcrumb=breadcrumb,
        month=month,
        groups=groups,
        feedback_url=None,
    )
    out = output_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    return out
