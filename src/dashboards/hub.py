"""planning サイトのトップページ生成。

aggregated/ から最新月のKPIとトレンドを読み、
ヒーローKPI + トレンドチャート + テーマナビを掲載する静的トップを作る。

設計のポイント:
- KPI／トレンドは `10_referral_kpi.csv` を **評価対象科のみ** で集計
  （月次経営サマリと完全に同じ値になるよう揃える）
- 件数系は **22営業日換算** とし、実数も併記する
- 04 月選択ナビは _layout.html のグローバルヘッダで提供されるため
  本ハブからは「月リンク」「科リンク」のセクションは廃止し、
  ヒーロー + トレンド + テーマナビのみのシンプル構成にする
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.classify import DeptClassifier
from src.core.normalization import (
    NORMALIZATION_BASE_DAYS,
    normalize_int,
    normalize_round,
)

logger = logging.getLogger(__name__)

_MONTH_FILE_RE = re.compile(r"^(\d{4}-\d{2})\.html$")
_MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")


def _list_monthly(docs_dir: Path) -> list[str]:
    d = docs_dir / "monthly"
    if not d.exists():
        return []
    months: list[str] = []
    for f in d.iterdir():
        m = _MONTH_FILE_RE.match(f.name)
        if m:
            months.append(m.group(1))
    return sorted(months, reverse=True)


def _list_dept_months(docs_dir: Path) -> list[str]:
    d = docs_dir / "dept"
    if not d.exists():
        return []
    months = [
        x.name
        for x in d.iterdir()
        if x.is_dir() and _MONTH_DIR_RE.match(x.name)
    ]
    return sorted(months, reverse=True)


def _delta(cur: float, prev: float) -> dict[str, Any]:
    if prev == 0:
        return {"pct": 0.0, "sign": "flat"}
    pct = (cur - prev) / prev * 100
    if pct > 2:
        sign = "up"
    elif pct < -2:
        sign = "down"
    else:
        sign = "flat"
    return {"pct": round(pct, 1), "sign": sign}


def _load_summary_meta(aggregated_root: Path, month: str) -> dict[str, Any]:
    """00_summary.csv から日数情報を取り出す。"""
    p = aggregated_root / month / "00_summary.csv"
    if not p.exists():
        return {"暦日数": 0, "営業日数": 0}
    s = pd.read_csv(p, encoding="utf-8-sig").iloc[0]
    return {
        "暦日数": int(s.get("期間_暦日数", 0) or 0),
        "営業日数": int(s.get("期間_営業日数", 0) or 0),
    }


def _load_trend(
    aggregated_root: Path,
    months: list[str],
    eval_dept_names: set[str],
) -> list[dict[str, Any]]:
    """各月の 10_referral_kpi を **評価対象科のみ** で集計してトレンド行に変換する。

    各月の値は件数の実数と22営業日換算の両方を返す。
    """
    rows: list[dict[str, Any]] = []
    for m in sorted(months):
        kpi_path = aggregated_root / m / "10_referral_kpi.csv"
        if not kpi_path.exists():
            continue
        kpi = pd.read_csv(kpi_path, encoding="utf-8-sig")
        sub = kpi[
            (kpi["月"].astype(str) == m)
            & (kpi["診療科名"].isin(eval_dept_names))
        ]
        if sub.empty:
            continue
        total = int(sub["総件数"].sum())
        sho = int(sub["初診件数"].sum())
        sai = int(sub["再診件数"].sum())
        ref = int(sub["紹介状あり初診"].sum())
        miraiin = int(sub["未来院件数"].sum())

        meta = _load_summary_meta(aggregated_root, m)
        biz = meta["営業日数"]
        rows.append(
            {
                "month": m,
                "total": total,
                "sho": sho,
                "sai": sai,
                "ref_rate": round(ref / sho * 100, 1) if sho else 0.0,
                "miraiin": miraiin,
                "miraiin_rate": round(miraiin / total * 100, 1) if total else 0.0,
                "biz_days": biz,
                "cal_days": meta["暦日数"],
                # 22営業日換算（部分月の補正にも有効）
                "total_norm": normalize_int(total, biz),
                "sho_norm": normalize_int(sho, biz),
                "sai_norm": normalize_int(sai, biz),
                "miraiin_norm": normalize_int(miraiin, biz),
            }
        )
    return rows


def _build_kpis(trend: list[dict[str, Any]]) -> dict[str, Any] | None:
    """ヒーロー部用に最新月／前月比のKPIを組み立てる。

    実数と22営業日換算の両方を持たせる。MoM比較は **換算ベース** で評価。
    """
    if not trend:
        return None
    latest = trend[-1]
    prev = trend[-2] if len(trend) >= 2 else {}

    # MoM 比較は換算後の値で（暦日数の差を排除）
    return {
        "total": latest["total"],
        "total_norm": latest["total_norm"],
        "total_delta": _delta(
            latest["total_norm"], prev.get("total_norm", 0)
        ),
        "sho": latest["sho"],
        "sho_norm": latest["sho_norm"],
        "sho_delta": _delta(latest["sho_norm"], prev.get("sho_norm", 0)),
        "ref_rate": latest["ref_rate"],
        "miraiin_rate": latest["miraiin_rate"],
        "miraiin_rate_delta": _delta(
            latest["miraiin_rate"], prev.get("miraiin_rate", 0)
        ),
        "biz_days": latest["biz_days"],
        "cal_days": latest["cal_days"],
        "norm_base": NORMALIZATION_BASE_DAYS,
    }


def _build_themes() -> list[dict[str, Any]]:
    """分析テーマナビ（Phase 3 以降で href を埋める）。"""
    return [
        {
            "title": "紹介・逆紹介",
            "desc": "薬再診スコア／逆紹介候補リスト／紹介率推移",
            "href": None,
        },
        {
            "title": "予約枠の再編",
            "desc": "枠×時間帯ヒートマップ／命名乖離・稀用枠の検出",
            "href": None,
        },
        {
            "title": "時間帯と看護師配置",
            "desc": "曜日×時間帯ヒートマップ／15時前後の負荷分布",
            "href": None,
        },
        {
            "title": "医師の負荷分布",
            "desc": "医師×時間帯ヒートマップ（匿名）",
            "href": None,
        },
    ]


def build_hub(
    docs_dir: Path,
    templates_dir: Path,
    aggregated_root: Path,
    classification_path: Path,
    feedback_url: str | None = None,
) -> Path:
    """planning サイトのトップページ docs/index.html を生成する。"""
    classifier = DeptClassifier(classification_path)
    eval_dept_names = {info.name for info in classifier.evaluation_targets()}

    monthly_months = _list_monthly(docs_dir)
    dept_months = _list_dept_months(docs_dir)
    all_months = sorted(set(monthly_months) | set(dept_months))
    latest = all_months[-1] if all_months else None

    trend = (
        _load_trend(aggregated_root, all_months, eval_dept_names) if all_months else []
    )
    kpis = _build_kpis(trend)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("index.html")

    breadcrumb = [{"label": "ホーム", "href": None}]

    html = template.render(
        title="ホーム",
        active="home",
        breadcrumb=breadcrumb,
        root_prefix="",
        latest_month=latest or "",
        all_months=list(reversed(all_months)),  # 新→旧
        kpis=kpis,
        trend_json=json.dumps(trend, ensure_ascii=False),
        themes=_build_themes(),
        norm_base=NORMALIZATION_BASE_DAYS,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        feedback_url=feedback_url,
    )

    out = docs_dir / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info("ハブページ出力: %s (%d chars)", out, len(html))
    return out
