"""パス解決ユーティリティ。

環境変数 / CLI 引数 / プロジェクトルート相対 の優先順でパスを決める。
上流リポからの aggregated CSV と diagnostic CSV を切り離して受け取れるよう、
すべてここで一元化する。
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_AGGREGATED = PROJECT_ROOT / "data" / "aggregated"
_DEFAULT_CLASSIFICATION = PROJECT_ROOT / "config" / "dept_classification.csv"
_DEFAULT_TARGETS = PROJECT_ROOT / "config" / "dept_targets.csv"
_DEFAULT_TEMPLATES = PROJECT_ROOT / "templates"
_DEFAULT_STATIC = PROJECT_ROOT / "static"
_DEFAULT_DOCS = PROJECT_ROOT / "docs"


def aggregated_root(override: Path | str | None = None) -> Path:
    """集計CSVのルート（OUTPATIENT_AGGREGATED で上書き可）。"""
    if override:
        return Path(override)
    env = os.environ.get("OUTPATIENT_AGGREGATED")
    if env:
        return Path(env)
    return _DEFAULT_AGGREGATED


def classification_path(override: Path | str | None = None) -> Path:
    """診療科分類CSV（OUTPATIENT_DEPT_CLASSIFICATION で上書き可）。"""
    if override:
        return Path(override)
    env = os.environ.get("OUTPATIENT_DEPT_CLASSIFICATION")
    if env:
        return Path(env)
    return _DEFAULT_CLASSIFICATION


def targets_path(override: Path | str | None = None) -> Path:
    """診療科目標CSV（OUTPATIENT_DEPT_TARGETS で上書き可）。存在しなくてもよい。"""
    if override:
        return Path(override)
    env = os.environ.get("OUTPATIENT_DEPT_TARGETS")
    if env:
        return Path(env)
    return _DEFAULT_TARGETS


def templates_dir(override: Path | str | None = None) -> Path:
    if override:
        return Path(override)
    return _DEFAULT_TEMPLATES


def static_dir(override: Path | str | None = None) -> Path:
    if override:
        return Path(override)
    return _DEFAULT_STATIC


def docs_dir(override: Path | str | None = None) -> Path:
    """出力ディレクトリ（OUTPATIENT_DOCS_DIR で上書き可）。"""
    if override:
        return Path(override)
    env = os.environ.get("OUTPATIENT_DOCS_DIR")
    if env:
        return Path(env)
    return _DEFAULT_DOCS
