"""共通コアロジック（集計CSV読込・診療科分類・評価）。

上流リポ（Outpatient-Dashboard）から `data_loader.py` / `classify.py` /
`grading.py` を最小コピーしている。集計仕様の真実は上流の `aggregate.py`
にあり、変更があったときはここも追従する必要がある。
"""
