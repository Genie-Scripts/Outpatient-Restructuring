"""ハイライト候補抽出と定型文生成。

planning 側では LLM を当面使わないため、上流リポの `highlights.py` の
候補抽出ロジックと、`llm_client.py` の `_fallback` 定型文生成だけを
このファイルに統合している。

- `extract_highlights(depts_data)` … best / declining / worst の3候補を抽出
- `format_highlights(candidates)` … テンプレが期待する head/body 辞書に整形

NOTE: LLM導入は将来検討。導入時は別モジュールに切り出す。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class HighlightCandidate:
    """ハイライト候補の生データ。"""

    name: str
    sho_latest: int
    sho_prev: int
    pct_change: float
    achievement: float
    target: int


def extract_highlights(
    depts_data: list[dict[str, Any]],
) -> dict[str, HighlightCandidate | None]:
    """診療科別データから best / declining / worst の3候補を抽出する。

    Args:
        depts_data: 各要素が以下のキーを持つ診療科別データ
            - name: 診療科名
            - sho_m: 月別初診件数リスト
            - sho_target: 初診目標

    Returns:
        {"best": ..., "declining": ..., "worst": ...} の辞書。
        該当なしの項目は None。
    """
    candidates: list[HighlightCandidate] = []
    for d in depts_data:
        target = d.get("sho_target", 0)
        sho_m = d.get("sho_m", [])
        if target < 20 or len(sho_m) < 2:
            continue
        sho_latest = sho_m[-1]
        sho_prev = sho_m[-2]
        if sho_prev == 0:
            continue
        pct_change = (sho_latest / sho_prev - 1) * 100
        ach = (sho_latest / target) * 100 if target > 0 else 0
        candidates.append(
            HighlightCandidate(
                name=d["name"],
                sho_latest=sho_latest,
                sho_prev=sho_prev,
                pct_change=round(pct_change, 1),
                achievement=round(ach, 0),
                target=target,
            )
        )

    return {
        "best": _pick_best(candidates),
        "declining": _pick_declining(depts_data),
        "worst": _pick_worst(candidates),
    }


def _pick_best(candidates: list[HighlightCandidate]) -> HighlightCandidate | None:
    positives = [c for c in candidates if c.pct_change > 0]
    return max(positives, key=lambda x: x.pct_change) if positives else None


def _pick_worst(candidates: list[HighlightCandidate]) -> HighlightCandidate | None:
    return min(candidates, key=lambda x: x.achievement) if candidates else None


def _pick_declining(depts_data: list[dict[str, Any]]) -> HighlightCandidate | None:
    for d in depts_data:
        target = d.get("sho_target", 0)
        m = d.get("sho_m", [])
        if target < 20 or len(m) < 4:
            continue
        if m[-2] < m[-3] < m[-4]:
            return HighlightCandidate(
                name=d["name"],
                sho_latest=m[-1],
                sho_prev=m[-2],
                pct_change=0.0,
                achievement=round(m[-1] / target * 100, 0) if target > 0 else 0,
                target=target,
            )
    return None


def format_highlights(
    candidates: dict[str, HighlightCandidate | None],
) -> dict[str, dict[str, Any] | None]:
    """ハイライト候補から head/body/raw 形式の定型文を生成する。

    LLMを使わず数値ベースの安全な定型文のみで構成（CLAUDE.md「LLMには計算をさせない」）。

    Returns:
        {"best": {head, body, raw}, "declining": ..., "worst": ...}
    """
    results: dict[str, dict[str, Any] | None] = {
        "best": None,
        "declining": None,
        "worst": None,
    }
    best = candidates.get("best")
    if best:
        results["best"] = {
            "head": "今月の好事例（初診件数）",
            "body": (
                f"{best.name}の初診件数が前月比 {best.pct_change:+.1f}%。"
                f"最新月 {best.sho_latest}件（前月 {best.sho_prev}件）。"
                "要因検証と横展開を検討。"
            ),
            "raw": asdict(best),
        }
    declining = candidates.get("declining")
    if declining:
        results["declining"] = {
            "head": "連続悪化傾向",
            "body": (
                f"{declining.name}の初診件数が3ヶ月連続で減少傾向。"
                f"最新月 {declining.sho_latest}件。早期の要因分析が必要。"
            ),
            "raw": asdict(declining),
        }
    worst = candidates.get("worst")
    if worst:
        results["worst"] = {
            "head": "目標達成率 最下位",
            "body": (
                f"{worst.name}の初診達成率は {worst.achievement:.0f}%。"
                f"最新月 {worst.sho_latest}件／目標 {worst.target}件。個別ヒアリング対象。"
            ),
            "raw": asdict(worst),
        }
    return results
