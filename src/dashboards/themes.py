"""4テーマページ（+ 外来枠×時間帯）の HTML 一括生成。

Phase 3 テーマ:
    referral       — 紹介・逆紹介
    slot           — 予約枠サマリ（時間帯ゾーン棒 + 稀用枠 + 科別）
    nursing        — 曜日×時間帯 分析（JS ヒートマップ）
    doctor         — 医師×時間帯 分析（JS ヒートマップ）
    slot-heatmap   — 外来枠×時間帯 分析（JS ヒートマップ）

出力先: docs/themes/YYYY-MM/{referral|slot|nursing|doctor|slot-heatmap}.html
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
    AggregatedData,
    list_available_months,
    load_aggregated_data,
    load_last_n_months,
)

logger = logging.getLogger(__name__)

_WEEKDAY_LABELS_5 = ["月", "火", "水", "木", "金"]
_WEEKDAY_RANGE_5 = list(range(5))

_TIMEZONE_ORDER = [
    "午前(〜12時)",
    "午後前半(12-15時)",
    "午後後半(15-17時)",
    "夕方以降(17時〜)",
]

# 30分ビン: 08:00(idx=0) → 19:30(idx=23)
_HEATMAP_BIN_COUNT = 24
_BIN_LABELS = [
    f"{(8 * 60 + i * 30) // 60:02d}:{(8 * 60 + i * 30) % 60:02d}"
    for i in range(_HEATMAP_BIN_COUNT)
]
_NURSE_CUTOFF_H = 15  # 時間帯カットオフ（看護師シフト基準）
_MORNING_PEAK_H = (9, 10)

DRUG_REVISIT_GLOBAL_TOP = 30
RARE_SLOT_THRESHOLD = 5


def _pct(num: float, den: float, ndigits: int = 1) -> float:
    return round(num / den * 100, ndigits) if den else 0.0


def _nav(month: str, months: list[str], slug: str) -> dict[str, str | None]:
    nav: dict[str, str | None] = {"prev_month_href": None, "next_month_href": None}
    if month in months:
        i = months.index(month)
        if i > 0:
            nav["prev_month_href"] = f"../{months[i - 1]}/{slug}.html"
        if i < len(months) - 1:
            nav["next_month_href"] = f"../{months[i + 1]}/{slug}.html"
    return nav


# ─────────────────────── Theme 1: 紹介・逆紹介 ──────────────────────

def _referral_summary(kpi: pd.DataFrame, eval_names: set[str]) -> dict[str, Any]:
    sub = kpi[kpi["診療科名"].isin(eval_names)]
    total = int(sub["総件数"].sum())
    sho = int(sub["初診件数"].sum())
    ref = int(sub["紹介状あり初診"].sum())
    mirain = int(sub["未来院件数"].sum())
    return {
        "total": total,
        "sho": sho,
        "sho_rate": _pct(sho, total),
        "ref_rate": _pct(ref, sho),
        "mirain_rate": _pct(mirain, total),
    }


def _referral_trend_all_depts(
    aggregated_root: Path,
    month: str,
    eval_names: set[str],
    n_months: int = 12,
) -> dict[str, list[dict[str, Any]]]:
    """直近 n か月の 紹介率・初診率 トレンド（評価科合計 + 科別）。

    Returns:
        {"全評価科合計": [...], "泌尿器科": [...], ...}
        各リストの要素: {"month": "YYYY-MM", "ref_rate": float, "sho_rate": float}
    """
    months = load_last_n_months(aggregated_root, month, n=n_months)
    result: dict[str, list[dict[str, Any]]] = {"全評価科合計": []}

    for m in months:
        p = aggregated_root / m / "10_referral_kpi.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")

        sub_all = df[(df["月"].astype(str) == m) & (df["診療科名"].isin(eval_names))]
        if not sub_all.empty:
            total = int(sub_all["総件数"].sum())
            sho = int(sub_all["初診件数"].sum())
            ref = int(sub_all["紹介状あり初診"].sum())
            result["全評価科合計"].append(
                {"month": m, "sho_rate": _pct(sho, total), "ref_rate": _pct(ref, sho)}
            )

        for dept in eval_names:
            row = df[(df["月"].astype(str) == m) & (df["診療科名"] == dept)]
            if row.empty:
                continue
            r = row.iloc[0]
            total_v = int(r.get("総件数", 0) or 0)
            sho_v = int(r.get("初診件数", 0) or 0)
            ref_v = int(r.get("紹介状あり初診", 0) or 0)
            if total_v == 0:
                continue
            result.setdefault(dept, []).append(
                {"month": m, "sho_rate": _pct(sho_v, total_v), "ref_rate": _pct(ref_v, sho_v)}
            )

    return result


def _reverse_referral_by_dept(
    rr: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    if rr.empty:
        return []
    cond = (
        (rr["初再診区分"] == "再診")
        & (rr["紹介状有無"] == "紹介状無し")
        & (rr["併科受診フラグ"] == "無")
        & (rr["診察前検査フラグ"] == "なし")
        & (rr["診察時間_階級"].isin(["0-4分", "5-9分"]))
        & (rr["診療科名"].isin(eval_names))
    )
    agg = (
        rr[cond]
        .groupby("診療科名")["件数"]
        .sum()
        .reset_index()
        .sort_values("件数", ascending=False)
    )
    return [
        {"name": str(r["診療科名"]), "count": int(r["件数"])}
        for _, r in agg.iterrows()
    ]


def _drug_revisit_global(
    drug: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    if drug.empty:
        return []
    sub = drug[drug["診療科名"].isin(eval_names)]
    sub = (
        sub.dropna(subset=["スコア"])
        .sort_values("スコア", ascending=False)
        .head(DRUG_REVISIT_GLOBAL_TOP)
    )
    rows = []
    for _, r in sub.iterrows():
        median = r.get("診察時間中央値_再診")
        rows.append(
            {
                "dept": str(r["診療科名"]),
                "medic": str(r["医師匿名ID"]),
                "slot": str(r["予約名称"]) if pd.notna(r.get("予約名称")) else "(未設定)",
                "sai": int(r["再診件数"]),
                "short_ratio": round(float(r["短時間再診比率"]) * 100, 1),
                "no_shokai_ratio": round(float(r["紹介状なし再診比率"]) * 100, 1),
                "median": round(float(median), 1) if pd.notna(median) else None,
                "score": round(float(r["スコア"]), 1),
            }
        )
    return rows


def _build_referral_ctx(
    data: AggregatedData,
    eval_names: set[str],
    month: str,
    aggregated_root: Path,
    months: list[str],
    biz_days: int,
    excluded_days: int,
) -> dict[str, Any]:
    summary = _referral_summary(data.referral_kpi, eval_names)
    rr_rows = _reverse_referral_by_dept(data.reverse_referral, eval_names)
    drug_rows = _drug_revisit_global(data.drug_revisit_score, eval_names)
    trend_all = _referral_trend_all_depts(aggregated_root, month, eval_names, n_months=12)
    rr_total = sum(r["count"] for r in rr_rows)
    nav = _nav(month, months, "referral")
    return {
        "summary": summary,
        "reverse_referral_dept_rows": rr_rows,
        "reverse_referral_total": rr_total,
        "drug_revisit_rows": drug_rows,
        "trend_all_json": json.dumps(trend_all, ensure_ascii=False),
        "trend_dept_names": json.dumps(sorted(trend_all.keys()), ensure_ascii=False),
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────────── Theme 2: 予約枠サマリ ───────────────────────────

def _timezone_stacked_chart(
    tz: pd.DataFrame, eval_names: set[str]
) -> dict[str, Any]:
    """全評価科合計の 時間帯ゾーン × 曜日 積み上げ棒グラフデータ。"""
    sub = tz[tz["診療科名"].isin(eval_names)]
    if sub.empty:
        return {"labels": [], "datasets": []}
    datasets: list[dict[str, Any]] = []
    for zone in _TIMEZONE_ORDER:
        counts = [
            int(sub[(sub["時間帯ゾーン"] == zone) & (sub["曜日"] == wd)]["件数"].sum())
            for wd in _WEEKDAY_RANGE_5
        ]
        datasets.append({"label": zone, "data": counts})
    return {"labels": _WEEKDAY_LABELS_5, "datasets": datasets}


def _slot_rare_rows(
    slot: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    sub = slot[slot["診療科名"].isin(eval_names)]
    pivot = (
        sub.groupby(["診療科名", "予約名称", "初再診区分"], dropna=False)["件数"]
        .sum()
        .unstack(fill_value=0)
    )
    sho_col = pivot.get("初診", pd.Series(0, index=pivot.index))
    sai_col = pivot.get("再診", pd.Series(0, index=pivot.index))
    rows: list[dict[str, Any]] = []
    for (dept, name) in pivot.index:
        sho = int(sho_col.get((dept, name), 0))
        sai = int(sai_col.get((dept, name), 0))
        total = sho + sai
        if 0 < total < RARE_SLOT_THRESHOLD:
            rows.append(
                {
                    "dept": str(dept),
                    "name": str(name) if pd.notna(name) else "(未設定)",
                    "total": total,
                    "sho": sho,
                    "sai": sai,
                    "sho_rate": _pct(sho, total),
                }
            )
    rows.sort(key=lambda x: x["total"])
    return rows


def _slot_dept_summary(
    slot: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    sub = slot[slot["診療科名"].isin(eval_names)]
    pivot = (
        sub.groupby(["診療科名", "予約名称", "初再診区分"], dropna=False)["件数"]
        .sum()
        .unstack(fill_value=0)
    )
    sho_col = pivot.get("初診", pd.Series(0, index=pivot.index))
    sai_col = pivot.get("再診", pd.Series(0, index=pivot.index))

    dept_data: dict[str, dict[str, Any]] = {}
    for (dept, name) in pivot.index:
        sho = int(sho_col.get((dept, name), 0))
        sai = int(sai_col.get((dept, name), 0))
        total = sho + sai
        if total == 0:
            continue
        d = dept_data.setdefault(
            dept,
            {"dept": dept, "total": 0, "sho": 0, "sai": 0,
             "slot_count": 0, "rare_count": 0},
        )
        d["total"] += total
        d["sho"] += sho
        d["sai"] += sai
        d["slot_count"] += 1
        if total < RARE_SLOT_THRESHOLD:
            d["rare_count"] += 1

    rows = list(dept_data.values())
    for r in rows:
        r["sho_rate"] = _pct(r["sho"], r["total"])
    rows.sort(key=lambda x: -x["total"])
    return rows


def _build_slot_ctx(
    data: AggregatedData,
    eval_names: set[str],
    month: str,
    months: list[str],
    biz_days: int,
    excluded_days: int,
) -> dict[str, Any]:
    tz_chart = _timezone_stacked_chart(data.dept_timezone, eval_names)
    rare_rows = _slot_rare_rows(data.slot_analysis, eval_names)
    dept_rows = _slot_dept_summary(data.slot_analysis, eval_names)
    nav = _nav(month, months, "slot")
    return {
        "timezone_chart_json": json.dumps(tz_chart, ensure_ascii=False),
        "rare_rows": rare_rows,
        "rare_count": len(rare_rows),
        "dept_rows": dept_rows,
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────────── Theme 3: 曜日×時間帯 分析 ──────────────────────

def _nursing_heatmap_dataset(
    hl: pd.DataFrame,
    eval_names: set[str],
    classifier: DeptClassifier,
) -> dict[str, Any]:
    """曜日×時間帯 ヒートマップデータ（診療科フィルタ対応）。

    Returns:
        {
            "filter_opts": [{"key": ..., "label": ...}, ...],
            "series": {
                "all": {"label": "全評価科合計",
                        "arrivals": [[float×24]×6],
                        "conc_med": [[float×24]×6],
                        "conc_max": [[float×24]×6]},
                "naika": {...},
                "DEPT_U":  {...},
            }
        }
    """
    if hl.empty:
        return {"filter_opts": [], "series": {}}

    df = hl[hl["診療科名"].isin(eval_names)].copy()
    df["_type"] = df["診療科名"].map(classifier.get_type)

    def _build_matrix_set(sub: pd.DataFrame) -> dict[str, list[list[float]]]:
        agg = (
            sub.groupby(["曜日", "bin_idx"])
            .agg(
                a=("到着件数_日平均", "sum"),
                m=("同時並行_中央値", "sum"),
                x=("同時並行_最大", "sum"),
            )
            .reset_index()
        )
        arrivals = [[0.0] * _HEATMAP_BIN_COUNT for _ in range(6)]
        conc_med = [[0.0] * _HEATMAP_BIN_COUNT for _ in range(6)]
        conc_max = [[0.0] * _HEATMAP_BIN_COUNT for _ in range(6)]
        for _, r in agg.iterrows():
            wd = int(r["曜日"])
            bi = int(r["bin_idx"])
            if 0 <= wd < 6 and 0 <= bi < _HEATMAP_BIN_COUNT:
                arrivals[wd][bi] = round(float(r["a"]), 2)
                conc_med[wd][bi] = round(float(r["m"]), 2)
                conc_max[wd][bi] = round(float(r["x"]), 2)
        return {"arrivals": arrivals, "conc_med": conc_med, "conc_max": conc_max}

    series: dict[str, Any] = {}
    filter_opts: list[dict[str, str]] = []

    # 全体
    ms = _build_matrix_set(df)
    series["all"] = {"label": "全評価科合計", **ms}
    filter_opts.append({"key": "all", "label": "全評価科合計"})

    # タイプ別
    for t_key, t_label, t_val in [("naika", "内科系", "内科系"), ("geka", "外科系", "外科系")]:
        sub = df[df["_type"] == t_val]
        if sub.empty:
            continue
        ms = _build_matrix_set(sub)
        series[t_key] = {"label": t_label, **ms}
        filter_opts.append({"key": t_key, "label": t_label})

    # 科別
    for info in classifier.evaluation_targets():
        sub = df[df["診療科名"] == info.name]
        if sub.empty:
            continue
        k = f"DEPT_{info.code}"
        ms = _build_matrix_set(sub)
        series[k] = {"label": info.name, **ms}
        filter_opts.append({"key": k, "label": f"{info.name}（{info.type}）"})

    return {"filter_opts": filter_opts, "series": series}


def _build_nursing_ctx(
    data: AggregatedData,
    eval_names: set[str],
    month: str,
    months: list[str],
    classifier: DeptClassifier,
    biz_days: int,
    excluded_days: int,
) -> dict[str, Any]:
    dataset = _nursing_heatmap_dataset(data.hourly_load, eval_names, classifier)
    nav = _nav(month, months, "nursing")
    return {
        "heatmap_dataset_json": json.dumps(dataset, ensure_ascii=False),
        "bin_labels_json": json.dumps(_BIN_LABELS, ensure_ascii=False),
        "weekdays_json": json.dumps(_WEEKDAY_LABELS_5, ensure_ascii=False),
        "nurse_cutoff_h": _NURSE_CUTOFF_H,
        "morning_peak_bins": [
            i for i in range(_HEATMAP_BIN_COUNT)
            if (8 * 60 + i * 30) // 60 in _MORNING_PEAK_H
        ],
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────────── Theme 4: 医師×時間帯 分析 ──────────────────────

def _doctor_heatmap_dataset(
    dh: pd.DataFrame,
    eval_names: set[str],
    classifier: DeptClassifier,
) -> dict[str, Any]:
    """医師×時間帯 ヒートマップデータ（診療科フィルタ対応）。

    区分=全体 のみ対象。指標: 出勤頻度率 + 件数_日平均。
    医師は月内総件数の降順で並べる。
    """
    if dh.empty:
        return {"filter_opts": [], "series": {}}

    df = dh[
        (dh["診療科名"].isin(eval_names))
        & (dh["区分"] == "全体")
        & (dh["曜日"].isin(_WEEKDAY_RANGE_5))
    ].copy()

    series: dict[str, Any] = {}
    filter_opts: list[dict[str, str]] = []

    for info in classifier.evaluation_targets():
        sub = df[df["診療科名"] == info.name]
        if sub.empty:
            continue
        k = f"DEPT_{info.code}"

        totals = (
            sub.groupby("予約担当者匿名ID")["件数合計"]
            .sum()
            .sort_values(ascending=False)
        )
        rows = []
        for did in totals.index:
            dsub = sub[sub["予約担当者匿名ID"] == did]
            freq_m = [[0.0] * _HEATMAP_BIN_COUNT for _ in range(5)]
            cpd_m = [[0.0] * _HEATMAP_BIN_COUNT for _ in range(5)]
            for _, r in dsub.iterrows():
                wd = int(r["曜日"])
                bi = int(r["bin_idx"])
                if 0 <= wd < 5 and 0 <= bi < _HEATMAP_BIN_COUNT:
                    freq_m[wd][bi] = round(float(r["出勤頻度率"]), 3)
                    cpd_m[wd][bi] = round(float(r["件数_日平均"]), 2)
            rows.append(
                {"id": str(did), "total": int(totals[did]), "freq": freq_m, "cpd": cpd_m}
            )
        series[k] = {"label": info.name, "type": info.type, "rows": rows}
        filter_opts.append({"key": k, "label": f"{info.name}（{info.type}）"})

    return {"filter_opts": filter_opts, "series": series}


def _build_doctor_ctx(
    data: AggregatedData,
    eval_names: set[str],
    month: str,
    months: list[str],
    classifier: DeptClassifier,
    biz_days: int,
    excluded_days: int,
) -> dict[str, Any]:
    dataset = _doctor_heatmap_dataset(data.doctor_hourly, eval_names, classifier)
    nav = _nav(month, months, "doctor")
    return {
        "heatmap_dataset_json": json.dumps(dataset, ensure_ascii=False),
        "bin_labels_json": json.dumps(_BIN_LABELS, ensure_ascii=False),
        "weekdays_json": json.dumps(_WEEKDAY_LABELS_5, ensure_ascii=False),
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────── Theme 5: 外来枠×時間帯 分析 ────────────────────────

def _slot_heatmap_dataset(
    sh: pd.DataFrame,
    eval_names: set[str],
    classifier: DeptClassifier,
) -> dict[str, Any]:
    """外来枠×時間帯 ヒートマップデータ（診療科フィルタ対応）。

    区分=全体 のみ対象。指標: 稼働頻度率 + 件数_日平均。
    枠は月内総件数の昇順（低稼働枠=縮小候補を先頭）で並べる。
    """
    if sh.empty:
        return {"filter_opts": [], "series": {}}

    df = sh[
        (sh["診療科名"].isin(eval_names))
        & (sh["区分"] == "全体")
        & (sh["曜日"].isin(_WEEKDAY_RANGE_5))
    ].copy()

    series: dict[str, Any] = {}
    filter_opts: list[dict[str, str]] = []

    for info in classifier.evaluation_targets():
        sub = df[df["診療科名"] == info.name]
        if sub.empty:
            continue
        k = f"DEPT_{info.code}"

        totals = (
            sub.groupby("予約名称")["件数合計"]
            .sum()
            .sort_values(ascending=True)  # 低稼働順
        )
        rows = []
        for sid in totals.index:
            ssub = sub[sub["予約名称"] == sid]
            freq_m = [[0.0] * _HEATMAP_BIN_COUNT for _ in range(5)]
            cpd_m = [[0.0] * _HEATMAP_BIN_COUNT for _ in range(5)]
            for _, r in ssub.iterrows():
                wd = int(r["曜日"])
                bi = int(r["bin_idx"])
                if 0 <= wd < 5 and 0 <= bi < _HEATMAP_BIN_COUNT:
                    freq_m[wd][bi] = round(float(r["稼働頻度率"]), 3)
                    cpd_m[wd][bi] = round(float(r["件数_日平均"]), 2)
            rows.append(
                {
                    "id": str(sid) if pd.notna(sid) else "(未設定)",
                    "total": int(totals[sid]),
                    "freq": freq_m,
                    "cpd": cpd_m,
                }
            )
        series[k] = {"label": info.name, "type": info.type, "rows": rows}
        filter_opts.append({"key": k, "label": f"{info.name}（{info.type}）"})

    return {"filter_opts": filter_opts, "series": series}


def _build_slot_heatmap_ctx(
    data: AggregatedData,
    eval_names: set[str],
    month: str,
    months: list[str],
    classifier: DeptClassifier,
    biz_days: int,
    excluded_days: int,
) -> dict[str, Any]:
    dataset = _slot_heatmap_dataset(data.slot_hourly, eval_names, classifier)
    nav = _nav(month, months, "slot-heatmap")
    return {
        "heatmap_dataset_json": json.dumps(dataset, ensure_ascii=False),
        "bin_labels_json": json.dumps(_BIN_LABELS, ensure_ascii=False),
        "weekdays_json": json.dumps(_WEEKDAY_LABELS_5, ensure_ascii=False),
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────────── メイン build 関数 ──────────────────────────────

def build_all_themes(
    month: str,
    aggregated_root: Path,
    templates_dir: Path,
    output_dir: Path,
    classification_path: Path,
) -> list[Path]:
    """5テーマページを生成して書き出したパスのリストを返す。"""
    classifier = DeptClassifier(classification_path)
    eval_names: set[str] = {info.name for info in classifier.evaluation_targets()}
    months = list_available_months(aggregated_root)
    data = load_aggregated_data(aggregated_root, month)

    biz_days = 0
    excluded_days = 0
    if not data.summary.empty:
        biz_days = int(data.summary.iloc[0].get("期間_営業日数", 0) or 0)
        excluded_days = int(data.summary.iloc[0].get("期間_除外日数", 0) or 0)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    latest_month = months[-1] if months else month
    all_months_desc = list(reversed(months))

    _THEMES = [
        (
            "referral", "紹介・逆紹介", "theme_referral.html",
            _build_referral_ctx(
                data, eval_names, month, aggregated_root, months, biz_days, excluded_days
            ),
        ),
        (
            "slot", "予約枠サマリ", "theme_slot.html",
            _build_slot_ctx(data, eval_names, month, months, biz_days, excluded_days),
        ),
        (
            "nursing", "曜日×時間帯 分析", "theme_nursing.html",
            _build_nursing_ctx(
                data, eval_names, month, months, classifier, biz_days, excluded_days
            ),
        ),
        (
            "doctor", "医師×時間帯 分析", "theme_doctor.html",
            _build_doctor_ctx(
                data, eval_names, month, months, classifier, biz_days, excluded_days
            ),
        ),
        (
            "slot-heatmap", "外来枠×時間帯 分析", "theme_slot_heatmap.html",
            _build_slot_heatmap_ctx(
                data, eval_names, month, months, classifier, biz_days, excluded_days
            ),
        ),
    ]

    for slug, title_ja, tpl_name, ctx in _THEMES:
        template = env.get_template(tpl_name)
        breadcrumb = [
            {"label": "ホーム", "href": "../../index.html"},
            {"label": f"テーマ：{title_ja} ／ {month}", "href": None},
        ]
        html = template.render(
            title=f"{title_ja} ／ {month}",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            root_prefix="../../",
            latest_month=latest_month,
            current_month=month,
            all_months=all_months_desc,
            active=f"theme-{slug}",
            breadcrumb=breadcrumb,
            month=month,
            feedback_url=None,
            **ctx,
        )
        out_path = output_dir / f"{slug}.html"
        out_path.write_text(html, encoding="utf-8")
        generated.append(out_path)
        logger.info("テーマページ出力: %s", out_path)

    return generated
