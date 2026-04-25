"""4テーマページのHTML一括生成。

Phase 3 で実装する分析テーマ:
    referral  — 紹介・逆紹介 (08/10/13 CSV)
    slot      — 予約枠の再編  (07 CSV)
    nursing   — 時間帯と看護師配置 (11/12 CSV)
    doctor    — 医師の負荷分布 (14 CSV)

出力先: docs/themes/YYYY-MM/{referral|slot|nursing|doctor}.html
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

_WEEKDAY_LABELS = ["月", "火", "水", "木", "金"]
_WEEKDAY_RANGE = range(5)

_TIMEZONE_ORDER = [
    "午前(〜12時)",
    "午後前半(12-15時)",
    "午後後半(15-17時)",
    "夕方以降(17時〜)",
]

# 表示対象の30分binを業務時間帯に絞る (08:00–18:30)
_HOUR_BINS = [
    "08:00", "08:30", "09:00", "09:30",
    "10:00", "10:30", "11:00", "11:30",
    "12:00", "12:30", "13:00", "13:30",
    "14:00", "14:30", "15:00", "15:30",
    "16:00", "16:30", "17:00", "17:30",
    "18:00", "18:30",
]

DRUG_REVISIT_GLOBAL_TOP = 30
REVERSE_REFERRAL_TOP = 30
SLOT_MISMATCH_TOP = 50
SLOT_RARE_TOP = 50
DOCTOR_TOP = 25


# ─────────────────────────── helpers ────────────────────────────

def _pct(num: float, den: float, ndigits: int = 1) -> float:
    return round(num / den * 100, ndigits) if den else 0.0


def _heatmap_intensity(value: float, max_val: float) -> float:
    """セルの色強度 0.0–1.0 を返す。"""
    if max_val <= 0:
        return 0.0
    return min(1.0, value / max_val)


def _nav(month: str, months: list[str], slug: str) -> dict[str, str | None]:
    """前後月へのリンクを構築する。"""
    nav: dict[str, str | None] = {
        "prev_month_href": None,
        "next_month_href": None,
    }
    if month in months:
        i = months.index(month)
        if i > 0:
            nav["prev_month_href"] = f"../{months[i - 1]}/{slug}.html"
        if i < len(months) - 1:
            nav["next_month_href"] = f"../{months[i + 1]}/{slug}.html"
    return nav


# ─────────────────── Theme 1: 紹介・逆紹介 ───────────────────────

def _referral_summary(kpi: pd.DataFrame, eval_names: set[str]) -> dict[str, Any]:
    sub = kpi[kpi["診療科名"].isin(eval_names)]
    total = int(sub["総件数"].sum())
    sho = int(sub["初診件数"].sum())
    ref = int(sub["紹介状あり初診"].sum())
    mirain = int(sub["未来院件数"].sum())
    return {
        "total": total,
        "sho": sho,
        "ref": ref,
        "mirain": mirain,
        "ref_rate": _pct(ref, sho),
        "mirain_rate": _pct(mirain, total),
    }


def _referral_trend(
    aggregated_root: Path,
    month: str,
    eval_names: set[str],
) -> list[dict[str, Any]]:
    """直近6か月の評価科合計の紹介率・未来院率トレンド。"""
    months = load_last_n_months(aggregated_root, month, n=6)
    rows: list[dict[str, Any]] = []
    for m in months:
        p = aggregated_root / m / "10_referral_kpi.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")
        sub = df[(df["月"].astype(str) == m) & (df["診療科名"].isin(eval_names))]
        if sub.empty:
            continue
        total = int(sub["総件数"].sum())
        sho = int(sub["初診件数"].sum())
        ref = int(sub["紹介状あり初診"].sum())
        mirain = int(sub["未来院件数"].sum())
        rows.append(
            {
                "month": m,
                "ref_rate": _pct(ref, sho),
                "mirain_rate": _pct(mirain, total),
                "sho": sho,
                "ref": ref,
            }
        )
    return rows


def _reverse_referral_by_dept(
    rr: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    """全科横断の逆紹介候補（件数降順）。"""
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
        .head(REVERSE_REFERRAL_TOP)
    )
    return [
        {"name": str(r["診療科名"]), "count": int(r["件数"])}
        for _, r in agg.iterrows()
    ]


def _drug_revisit_global(
    drug: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    """全科横断の薬再診スコア上位。"""
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
    trend = _referral_trend(aggregated_root, month, eval_names)
    rr_total = sum(r["count"] for r in rr_rows)
    nav = _nav(month, months, "referral")
    return {
        "summary": summary,
        "reverse_referral_dept_rows": rr_rows,
        "reverse_referral_total": rr_total,
        "drug_revisit_rows": drug_rows,
        "trend_json": json.dumps(trend, ensure_ascii=False),
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────────── Theme 2: 予約枠の再編 ───────────────────────

def _slot_mismatch_rows(
    slot: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    """「初診」名称で初診率<50% の枠を全科横断で抽出。"""
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
        if total < 5:
            continue
        sho_rate = _pct(sho, total)
        if "初診" in str(name) and sho_rate < 50:
            rows.append(
                {
                    "dept": str(dept),
                    "name": str(name) if pd.notna(name) else "(未設定)",
                    "total": total,
                    "sho": sho,
                    "sai": sai,
                    "sho_rate": sho_rate,
                }
            )
    rows.sort(key=lambda x: x["sho_rate"])
    return rows[:SLOT_MISMATCH_TOP]


def _slot_rare_rows(
    slot: pd.DataFrame, eval_names: set[str], threshold: int = 5
) -> list[dict[str, Any]]:
    """月間件数が threshold 未満の稀用枠一覧。"""
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
        if 0 < total < threshold:
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
    return rows[:SLOT_RARE_TOP]


def _slot_dept_summary(
    slot: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    """科別の総件数・初診率・枠数・命名乖離枠数サマリ。"""
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
        d = dept_data.setdefault(dept, {
            "dept": dept, "total": 0, "sho": 0, "sai": 0,
            "slot_count": 0, "mismatch_count": 0, "rare_count": 0,
        })
        d["total"] += total
        d["sho"] += sho
        d["sai"] += sai
        d["slot_count"] += 1
        if total < 5:
            d["rare_count"] += 1
        if "初診" in str(name) and total >= 5:
            sho_rate = _pct(sho, total)
            if sho_rate < 50:
                d["mismatch_count"] += 1

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
    mismatch_rows = _slot_mismatch_rows(data.slot_analysis, eval_names)
    rare_rows = _slot_rare_rows(data.slot_analysis, eval_names)
    dept_rows = _slot_dept_summary(data.slot_analysis, eval_names)
    nav = _nav(month, months, "slot")
    return {
        "mismatch_rows": mismatch_rows,
        "rare_rows": rare_rows,
        "dept_rows": dept_rows,
        "mismatch_count": len(mismatch_rows),
        "rare_count": len(rare_rows),
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────────── Theme 3: 時間帯と看護師配置 ─────────────────

def _timezone_stacked_chart(
    tz: pd.DataFrame, eval_names: set[str]
) -> dict[str, Any]:
    """全評価科合計の 時間帯ゾーン × 曜日 積み上げ棒グラフデータ。"""
    sub = tz[tz["診療科名"].isin(eval_names)]
    if sub.empty:
        return {"labels": [], "datasets": []}
    datasets: list[dict[str, Any]] = []
    for zone in _TIMEZONE_ORDER:
        counts = []
        for wd in _WEEKDAY_RANGE:
            v = int(sub[(sub["時間帯ゾーン"] == zone) & (sub["曜日"] == wd)]["件数"].sum())
            counts.append(v)
        datasets.append({"label": zone, "data": counts})
    return {"labels": _WEEKDAY_LABELS, "datasets": datasets}


def _late_ratio_by_dept(
    tz: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    """科別の15時以降比率（降順）。"""
    sub = tz[tz["診療科名"].isin(eval_names)]
    if sub.empty:
        return []
    late_zones = {"午後後半(15-17時)", "夕方以降(17時〜)"}
    agg_total = sub.groupby("診療科名")["件数"].sum()
    agg_late = (
        sub[sub["時間帯ゾーン"].isin(late_zones)]
        .groupby("診療科名")["件数"]
        .sum()
    )
    rows = []
    for dept in agg_total.index:
        total = int(agg_total[dept])
        late = int(agg_late.get(dept, 0))
        if total < 10:
            continue
        rows.append(
            {
                "dept": str(dept),
                "total": total,
                "late": late,
                "late_rate": _pct(late, total),
            }
        )
    rows.sort(key=lambda x: -x["late_rate"])
    return rows[:20]


def _hourly_heatmap(
    hl: pd.DataFrame, eval_names: set[str]
) -> dict[str, Any]:
    """曜日 × 時間帯bin のヒートマップデータ。

    Returns:
        {
            "bins": list[str],         # 時間帯ラベル
            "rows": list[{             # 曜日ごとの行
                "label": "月",
                "values": list[float], # 各binの到着件数_日平均 (eval科合計)
                "intensities": list[float], # 0.0-1.0 色強度
            }],
            "max_val": float,
        }
    """
    sub = hl[hl["診療科名"].isin(eval_names)]
    if sub.empty:
        return {"bins": _HOUR_BINS, "rows": [], "max_val": 0}

    # eval科合計 = bin × 曜日
    agg = (
        sub.groupby(["曜日", "bin_label"])["到着件数_日平均"]
        .sum()
        .reset_index()
    )

    matrix: dict[tuple[int, str], float] = {
        (int(r["曜日"]), str(r["bin_label"])): float(r["到着件数_日平均"])
        for _, r in agg.iterrows()
    }

    max_val = max(matrix.values()) if matrix else 0.0

    heatmap_rows = []
    for wd in _WEEKDAY_RANGE:
        vals = [round(matrix.get((wd, b), 0.0), 1) for b in _HOUR_BINS]
        intens = [_heatmap_intensity(v, max_val) for v in vals]
        heatmap_rows.append(
            {
                "label": _WEEKDAY_LABELS[wd],
                "counts": vals,
                "intens": [round(x, 3) for x in intens],
            }
        )

    return {"bins": _HOUR_BINS, "rows": heatmap_rows, "max_val": round(max_val, 1)}


def _build_nursing_ctx(
    data: AggregatedData,
    eval_names: set[str],
    month: str,
    months: list[str],
    biz_days: int,
    excluded_days: int,
) -> dict[str, Any]:
    tz_chart = _timezone_stacked_chart(data.dept_timezone, eval_names)
    late_rows = _late_ratio_by_dept(data.dept_timezone, eval_names)
    heatmap = _hourly_heatmap(data.hourly_load, eval_names)
    nav = _nav(month, months, "nursing")
    return {
        "timezone_chart_json": json.dumps(tz_chart, ensure_ascii=False),
        "late_ratio_rows": late_rows,
        "heatmap": heatmap,
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────────── Theme 4: 医師の負荷分布 ─────────────────────

def _doctor_top(
    dh: pd.DataFrame, eval_names: set[str]
) -> list[dict[str, Any]]:
    """全科横断 医師TOP（日平均件数合計の高い順）。"""
    if dh.empty:
        return []
    sub = dh[
        (dh["診療科名"].isin(eval_names))
        & (dh["区分"] == "全体")
        & (dh["曜日"].isin(list(_WEEKDAY_RANGE)))
    ]
    if sub.empty:
        return []
    # 医師×科ごとに 件数合計・出勤日数を合算
    agg = (
        sub.groupby(["診療科名", "予約担当者匿名ID"])
        .agg(件数合計=("件数合計", "sum"), 出勤日数=("出勤日数", "max"))
        .reset_index()
    )
    agg["日平均"] = (agg["件数合計"] / agg["出勤日数"].replace(0, 1)).round(1)
    top = agg.sort_values("日平均", ascending=False).head(DOCTOR_TOP)
    return [
        {
            "dept": str(r["診療科名"]),
            "medic": str(r["予約担当者匿名ID"]),
            "total": int(r["件数合計"]),
            "days": int(r["出勤日数"]),
            "daily_avg": float(r["日平均"]),
        }
        for _, r in top.iterrows()
    ]


def _doctor_dept_heatmap(
    dh: pd.DataFrame, eval_names: set[str]
) -> dict[str, Any]:
    """全評価科合計の 曜日 × 時間帯bin 医師ベースの件数ヒートマップ。

    `14_doctor_hourly.csv` の区分=全体・曜日0-4 の件数合計を
    評価科で集約し、`12_hourly_load.csv` と同形式で返す。
    """
    if dh.empty:
        return {"bins": _HOUR_BINS, "rows": [], "max_val": 0}
    sub = dh[
        (dh["診療科名"].isin(eval_names))
        & (dh["区分"] == "全体")
        & (dh["曜日"].isin(list(_WEEKDAY_RANGE)))
    ]
    if sub.empty:
        return {"bins": _HOUR_BINS, "rows": [], "max_val": 0}

    # 科×医師×曜日×bin を科レベルに集約（出勤日数を代理で最大値取得）
    agg = (
        sub.groupby(["曜日", "bin_label"])
        .agg(件数合計=("件数合計", "sum"))
        .reset_index()
    )
    # 月の実営業日で割って日平均を推定（全科なので素直にsumで可）
    # ここでは月 20営業日（近似）で割るよりも「月合計件数」として表示
    matrix: dict[tuple[int, str], float] = {
        (int(r["曜日"]), str(r["bin_label"])): float(r["件数合計"])
        for _, r in agg.iterrows()
    }
    max_val = max(matrix.values()) if matrix else 0.0

    heatmap_rows = []
    for wd in _WEEKDAY_RANGE:
        vals = [round(matrix.get((wd, b), 0.0), 0) for b in _HOUR_BINS]
        intens = [_heatmap_intensity(v, max_val) for v in vals]
        heatmap_rows.append(
            {
                "label": _WEEKDAY_LABELS[wd],
                "counts": [int(v) for v in vals],
                "intens": [round(x, 3) for x in intens],
            }
        )
    return {"bins": _HOUR_BINS, "rows": heatmap_rows, "max_val": int(max_val)}


def _build_doctor_ctx(
    data: AggregatedData,
    eval_names: set[str],
    month: str,
    months: list[str],
    biz_days: int,
    excluded_days: int,
) -> dict[str, Any]:
    top_rows = _doctor_top(data.doctor_hourly, eval_names)
    heatmap = _doctor_dept_heatmap(data.doctor_hourly, eval_names)
    nav = _nav(month, months, "doctor")
    return {
        "doctor_top_rows": top_rows,
        "heatmap": heatmap,
        "biz_days": biz_days,
        "excluded_days": excluded_days,
        **nav,
    }


# ─────────────────── メイン build 関数 ──────────────────────────

def build_all_themes(
    month: str,
    aggregated_root: Path,
    templates_dir: Path,
    output_dir: Path,
    classification_path: Path,
) -> list[Path]:
    """4テーマページを生成して書き出したパスのリストを返す。

    Args:
        month: "YYYY-MM"
        aggregated_root: data/aggregated/ のパス
        templates_dir: Jinja2 テンプレ格納ディレクトリ
        output_dir: 出力先 (docs/themes/YYYY-MM/)
        classification_path: config/dept_classification.csv
    """
    classifier = DeptClassifier(classification_path)
    eval_names: set[str] = {info.name for info in classifier.evaluation_targets()}
    months = list_available_months(aggregated_root)
    data = load_aggregated_data(aggregated_root, month)

    # 営業日情報
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
            "referral",
            "紹介・逆紹介",
            "theme_referral.html",
            _build_referral_ctx(
                data, eval_names, month, aggregated_root, months, biz_days, excluded_days
            ),
        ),
        (
            "slot",
            "予約枠の再編",
            "theme_slot.html",
            _build_slot_ctx(
                data, eval_names, month, months, biz_days, excluded_days
            ),
        ),
        (
            "nursing",
            "時間帯と看護師配置",
            "theme_nursing.html",
            _build_nursing_ctx(
                data, eval_names, month, months, biz_days, excluded_days
            ),
        ),
        (
            "doctor",
            "医師の負荷分布",
            "theme_doctor.html",
            _build_doctor_ctx(
                data, eval_names, month, months, biz_days, excluded_days
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
            site_title="外来再編分析",
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
