"""営業日換算（30日換算）ユーティリティ。

外来は基本平日中心の運用なので、月の営業日数で正規化することで
月またぎ比較を公平に揃える。基準は **22営業日** （4週×5日 + 微調整の標準値）。

換算式:
    normalized = value × (22 / 期間_営業日数)

部分月（例: データが月の途中で打ち切られている場合）にも対応可能。
祝日は当面考慮しない（最初は素朴な月〜金の暦日数）。

CLAUDE.md「LLMには計算をさせない」原則に沿い、計算は全てここで完結する。
"""
from __future__ import annotations

# 標準営業日数。月平均（暦30日 ÷ 7 × 5 ≈ 21.4）を切り上げた値。
NORMALIZATION_BASE_DAYS = 22


def biz_factor(biz_days: int | None, base: int = NORMALIZATION_BASE_DAYS) -> float:
    """営業日換算の倍率を返す。

    Args:
        biz_days: 期間の営業日数（00_summary.csv の `期間_営業日数`）
        base: 基準営業日数（既定: 22）

    Returns:
        倍率。biz_days が 0/None なら 1.0（換算しない）。
    """
    if not biz_days or biz_days <= 0:
        return 1.0
    return base / biz_days


def normalize(value: float, biz_days: int | None) -> float:
    """件数等を 22営業日基準に換算する。"""
    return value * biz_factor(biz_days)


def normalize_int(value: int, biz_days: int | None) -> int:
    """件数を 22営業日基準に換算（四捨五入で整数）。"""
    return int(round(normalize(value, biz_days)))


def normalize_round(value: float, biz_days: int | None, ndigits: int = 1) -> float:
    """件数等を 22営業日基準に換算（指定桁で四捨五入）。"""
    return round(normalize(value, biz_days), ndigits)
