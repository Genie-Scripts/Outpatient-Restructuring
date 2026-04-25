"""月次経営サマリ生成（planning サイト版）。

集計CSV（data/aggregated/YYYY-MM/）を読み、6ヶ月分のトレンドを構築して
templates/monthly.html にデータ埋込みで出力する。

planning 版の設計:
- LLM依存を排除（数値ベースの定型文ハイライトのみ）
- 評価対象科のみ（運用系の3科を除外）
- **22営業日換算** トレンドを併用、MoM比較は換算ベースで実施
- SCREEN4（診療科別ドリルダウン）は廃止 → 重複情報は dept_planning に集約済
- monthly.html は _layout.html 継承の Jinja2 テンプレ
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.classify import DeptClassifier
from src.core.data_loader import (
    list_available_months,
    load_aggregated_data,
    load_last_n_months,
)
from src.core.highlights import extract_highlights, format_highlights
from src.core.normalization import (
    NORMALIZATION_BASE_DAYS,
    biz_factor,
    normalize_int,
)

logger = logging.getLogger(__name__)


def _load_targets(targets_path: Path) -> dict[str, dict[str, float]]:
    """dept_targets.csv を読み込む。存在しなければ空。"""
    if not targets_path.exists():
        logger.info("目標ファイル未検出: %s（自動算出のみ）", targets_path)
        return {}
    df = pd.read_csv(targets_path, encoding="utf-8-sig")
    targets: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        targets[str(row["診療科名"])] = {
            "sho_target": int(row.get("初診目標_月", 0) or 0),
            "kus_target": int(row.get("薬のみ再診_目標", 0) or 0),
            "sps_target": float(row.get("再診初診比率_目標", 0) or 0),
        }
    return targets


def _load_summary_meta(aggregated_root: Path, month: str) -> dict[str, int]:
    """00_summary.csv から営業日数を取り出す。"""
    p = aggregated_root / month / "00_summary.csv"
    if not p.exists():
        return {"暦日数": 0, "営業日数": 0}
    s = pd.read_csv(p, encoding="utf-8-sig").iloc[0]
    return {
        "暦日数": int(s.get("期間_暦日数", 0) or 0),
        "営業日数": int(s.get("期間_営業日数", 0) or 0),
    }


def _build_dashboard_data(
    aggregated_root: Path,
    month: str,
    classifier: DeptClassifier,
    user_targets: dict[str, dict[str, float]],
    n_months: int = 6,
) -> dict[str, Any]:
    """対象月を最終月として、過去n_months分のトレンドデータを構築。"""
    months = load_last_n_months(aggregated_root, month, n=n_months)
    if not months:
        raise ValueError(f"集計ディレクトリが見つかりません: {aggregated_root}")

    kpi_frames: list[pd.DataFrame] = []
    rr_frames: list[pd.DataFrame] = []

    for m in months:
        d = load_aggregated_data(aggregated_root, m)
        kpi_frames.append(d.referral_kpi)
        rr_frames.append(d.reverse_referral)

    kpi = pd.concat(kpi_frames, ignore_index=True)
    rr = pd.concat(rr_frames, ignore_index=True)

    if kpi.empty:
        raise ValueError("集計CSVに月データがありません")

    month_labels = [m.split("-")[1].lstrip("0") + "月" for m in months]

    # 各月の営業日数を取得（換算用）
    month_meta = {m: _load_summary_meta(aggregated_root, m) for m in months}
    biz_days_per_month = [month_meta[m]["営業日数"] for m in months]

    rr_best = rr[
        (rr["初再診区分"] == "再診")
        & (rr["紹介状有無"] == "紹介状無し")
        & (rr["併科受診フラグ"] == "無")
        & (rr["診察時間_階級"].isin(["0-4分", "5-9分"]))
        & (rr["診察前検査フラグ"] == "なし")
    ]
    kusuri = rr[(rr["診療区分"] == "薬のみ") & (rr["初再診区分"] == "再診")]

    depts_data: list[dict[str, Any]] = []

    for dept_name in kpi["診療科名"].unique():
        if not classifier.is_evaluation_target(dept_name):
            continue
        k = (
            kpi[kpi["診療科名"] == dept_name]
            .set_index("月")
            .reindex(months)
            .fillna(0)
        )
        total = k["総件数"].astype(int).tolist()
        sho = k["初診件数"].astype(int).tolist()
        sai = k["再診件数"].astype(int).tolist()

        kus_m = (
            kusuri[kusuri["診療科名"] == dept_name]
            .groupby("月")["件数"]
            .sum()
            .reindex(months)
            .fillna(0)
            .astype(int)
            .tolist()
        )
        cand_m = (
            rr_best[rr_best["診療科名"] == dept_name]
            .groupby("月")["件数"]
            .sum()
            .reindex(months)
            .fillna(0)
            .astype(int)
            .tolist()
        )

        # 22営業日換算列も並走で持つ（チャートで切替表示できるように）
        total_norm = [
            normalize_int(t, b) for t, b in zip(total, biz_days_per_month)
        ]
        sho_norm = [
            normalize_int(s, b) for s, b in zip(sho, biz_days_per_month)
        ]
        sai_norm = [
            normalize_int(s, b) for s, b in zip(sai, biz_days_per_month)
        ]
        kus_norm = [
            normalize_int(k_, b) for k_, b in zip(kus_m, biz_days_per_month)
        ]
        cand_norm = [
            normalize_int(c, b) for c, b in zip(cand_m, biz_days_per_month)
        ]

        avg_monthly = sum(total) // max(len(months), 1)
        # 評価対象科は KPI 合計に必ず含める（hub と一致させるため）。
        # 表示用のスコアカードでは sho_target>0 || kus_target>0 で別途フィルタする。

        sps_m = [round(s / h, 1) if h > 0 else None for s, h in zip(sai, sho)]

        n = len(months)
        # 目標は換算基準で算出（実数と換算で目標を分けるとややこしいので22営業日基準を採用）
        avg_sho_norm = sum(sho_norm) / n
        avg_kus_norm = sum(kus_norm) / n
        avg_sai_norm = sum(sai_norm) / n
        sps_avg = round(avg_sai_norm / avg_sho_norm, 1) if avg_sho_norm > 0 else 0

        ut = user_targets.get(dept_name, {})
        sho_target = (
            int(ut["sho_target"])
            if ut.get("sho_target")
            else (int(avg_sho_norm * 1.10) if avg_sho_norm > 0 else 0)
        )
        kus_target = (
            int(ut["kus_target"])
            if ut.get("kus_target")
            else (int(avg_kus_norm * 0.85) if avg_kus_norm > 0 else 0)
        )
        sps_target = (
            float(ut["sps_target"])
            if ut.get("sps_target")
            else (round(sps_avg * 0.9, 1) if sps_avg > 0 else 0)
        )

        dept_type = classifier.get_type(dept_name)
        type_key = {"外科系": "geka", "内科系": "naika"}.get(dept_type, "other")
        dept_code = classifier.get_code(dept_name)

        depts_data.append(
            {
                "name": dept_name,
                "type": type_key,
                "code": dept_code,
                "href": f"../dept/{month}/{dept_code}.html",
                "avg_monthly": avg_monthly,
                # 実数（履歴用に残す）
                "sho_m": sho,
                "sai_m": sai,
                "kus_m": kus_m,
                "cand_m": cand_m,
                "total_m": total,
                "sps_m": sps_m,
                # 22営業日換算
                "sho_m_norm": sho_norm,
                "sai_m_norm": sai_norm,
                "kus_m_norm": kus_norm,
                "cand_m_norm": cand_norm,
                "total_m_norm": total_norm,
                # 目標（換算基準）
                "sho_target": sho_target,
                "kus_target": kus_target,
                "sps_target": sps_target,
                # 当月単体（換算値で当月評価）
                "sho_apr": sho_norm[-1],
                "sai_apr": sai_norm[-1],
                "kus_apr": kus_norm[-1],
                "cand_apr": cand_norm[-1],
                "total_apr": total_norm[-1],
                "sps_apr": sps_m[-1],
                "sho_apr_raw": sho[-1],
                "total_apr_raw": total[-1],
            }
        )

    depts_data.sort(key=lambda x: x["avg_monthly"], reverse=True)

    # トレンド合計：丸め誤差を防ぐため「実数で合計 → 月単位で換算」の順で計算する
    # （hub.py と完全一致させるため）
    total_raw = [
        sum(d["total_m"][i] for d in depts_data) for i in range(len(months))
    ]
    sho_raw = [
        sum(d["sho_m"][i] for d in depts_data) for i in range(len(months))
    ]
    sai_raw = [
        sum(d["sai_m"][i] for d in depts_data) for i in range(len(months))
    ]
    kus_raw = [
        sum(d["kus_m"][i] for d in depts_data) for i in range(len(months))
    ]
    cand_raw = [
        sum(d["cand_m"][i] for d in depts_data) for i in range(len(months))
    ]
    total_norm = [
        normalize_int(t, b) for t, b in zip(total_raw, biz_days_per_month)
    ]
    total_sho_norm = [
        normalize_int(t, b) for t, b in zip(sho_raw, biz_days_per_month)
    ]
    total_sai_norm = [
        normalize_int(t, b) for t, b in zip(sai_raw, biz_days_per_month)
    ]
    total_kus_norm = [
        normalize_int(t, b) for t, b in zip(kus_raw, biz_days_per_month)
    ]
    total_cand_norm = [
        normalize_int(t, b) for t, b in zip(cand_raw, biz_days_per_month)
    ]

    # 当月＝月配列の最終要素（hub と同じ計算順）
    return {
        "months": months,
        "monthLabels": month_labels,
        "biz_days": biz_days_per_month,
        "norm_base": NORMALIZATION_BASE_DAYS,
        "depts": depts_data,
        # 換算配列（チャート用）
        "total_sho_monthly": total_sho_norm,
        "total_sai_monthly": total_sai_norm,
        "total_kus_monthly": total_kus_norm,
        "total_cand_monthly": total_cand_norm,
        "total_monthly": total_norm,
        # 実数配列（注釈・ツールチップ用）
        "total_raw_monthly": total_raw,
        "total_sho_raw_monthly": sho_raw,
        # 当月単体（換算値、配列の末尾と同じ）
        "total_sho_apr": total_sho_norm[-1],
        "total_sai_apr": total_sai_norm[-1],
        "total_kus_apr": total_kus_norm[-1],
        "total_apr": total_norm[-1],
        # 当月の実数（KPIカードの併記用）
        "total_apr_raw": total_raw[-1],
        "total_sho_apr_raw": sho_raw[-1],
        "global_sho_target": sum(d["sho_target"] for d in depts_data),
        "global_kus_target": sum(d["kus_target"] for d in depts_data),
        "generated_at": datetime.now().isoformat(),
    }


def build_monthly_dashboard(
    month: str,
    output_path: Path,
    aggregated_root: Path,
    templates_dir: Path,
    classification_path: Path,
    targets_path: Path,
    feedback_url: str | None = None,
) -> None:
    """月次経営サマリダッシュボードを生成する。"""
    classifier = DeptClassifier(classification_path)
    user_targets = _load_targets(targets_path)

    data = _build_dashboard_data(aggregated_root, month, classifier, user_targets)
    candidates = extract_highlights(data["depts"])
    highlights = format_highlights(candidates)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("monthly.html")

    months = data["months"]
    all_months = list_available_months(aggregated_root)
    breadcrumb = [
        {"label": "ホーム", "href": "../index.html"},
        {"label": f"月次経営サマリ ／ {month}", "href": None},
    ]

    html = template.render(
        title=f"月次経営サマリ ／ {month}",
        active="monthly",
        breadcrumb=breadcrumb,
        root_prefix="../",
        latest_month=all_months[-1] if all_months else month,
        current_month=month,
        all_months=list(reversed(all_months)),  # 新→旧
        # 主データ
        data=data,
        highlights=highlights,
        norm_base=NORMALIZATION_BASE_DAYS,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        feedback_url=feedback_url,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("月次経営サマリ出力: %s (%d chars)", output_path, len(html))
