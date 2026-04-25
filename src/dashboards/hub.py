"""planning サイトのトップページ生成。

aggregated/ から最新月のKPIとトレンドを読み、
月次サマリ＋診療科一覧＋テーマナビを掲載する静的トップを作る。

Phase 2 では monthly + dept_planning までしか移植していないため、
テーマ欄には後続フェーズ用のプレースホルダを置いている。
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


def _list_dept_months(docs_dir: Path) -> list[dict[str, Any]]:
    d = docs_dir / "dept"
    if not d.exists():
        return []
    months: list[dict[str, Any]] = []
    for sub in sorted(
        (x for x in d.iterdir() if x.is_dir() and _MONTH_DIR_RE.match(x.name)),
        reverse=True,
    ):
        codes = [f for f in sub.glob("*.html") if f.stem != "index"]
        months.append({"month": sub.name, "count": len(codes)})
    if months:
        months[0]["is_latest"] = True
    for m in months[1:]:
        m["is_latest"] = False
    return months


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


def _load_trend(aggregated_root: Path, months: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in sorted(months):
        p = aggregated_root / m / "00_summary.csv"
        if not p.exists():
            continue
        s = pd.read_csv(p, encoding="utf-8-sig").iloc[0]
        sho = int(s["初診件数"])
        total = int(s["総件数"])
        ref = int(s["紹介状あり"])
        miraiin = int(s["未来院件数"])
        rows.append(
            {
                "month": m,
                "total": total,
                "sho": sho,
                "sai": int(s["再診件数"]),
                "ref_rate": round(ref / sho * 100, 1) if sho else 0.0,
                "miraiin": miraiin,
                "miraiin_rate": round(miraiin / total * 100, 1) if total else 0.0,
            }
        )
    return rows


def _build_kpis(trend: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not trend:
        return None
    latest = trend[-1]
    prev = trend[-2] if len(trend) >= 2 else {}
    return {
        "total": latest["total"],
        "total_delta": _delta(latest["total"], prev.get("total", 0)),
        "sho": latest["sho"],
        "sho_delta": _delta(latest["sho"], prev.get("sho", 0)),
        "ref_rate": latest["ref_rate"],
        "miraiin_rate": latest["miraiin_rate"],
        "miraiin_rate_delta": _delta(
            latest["miraiin_rate"], prev.get("miraiin_rate", 0)
        ),
    }


def _build_themes() -> list[dict[str, Any]]:
    """分析テーマナビ。Phase 3 以降で href を埋める。"""
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
    feedback_url: str | None = None,
) -> Path:
    """planning サイトのトップページ docs/index.html を生成する。"""
    monthly_months = _list_monthly(docs_dir)
    dept_months_meta = _list_dept_months(docs_dir)

    all_months = sorted(
        set(monthly_months) | {dm["month"] for dm in dept_months_meta}
    )
    latest = all_months[-1] if all_months else None

    trend = _load_trend(aggregated_root, all_months) if all_months else []
    kpis = _build_kpis(trend)

    monthly_links = [
        {"month": m, "href": f"monthly/{m}.html", "is_latest": m == latest}
        for m in monthly_months
    ]

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
        kpis=kpis,
        trend_json=json.dumps(trend, ensure_ascii=False),
        monthly_links=monthly_links,
        dept_months=dept_months_meta,
        themes=_build_themes(),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        feedback_url=feedback_url,
    )

    out = docs_dir / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info("ハブページ出力: %s (%d chars)", out, len(html))
    return out
