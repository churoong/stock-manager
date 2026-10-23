"""시장 이름 목록의 단일 정의처 (docs/infra.md 25.1746, 중복 규칙 감사 ⑦).

국내 시장 목록이 네 곳에 따로 적혀 있었고(`jobs/backtest`·`jobs/signals`·`services/report_picks`·
`services/holding_scores`), KONEX 를 넣은 곳과 뺀 곳(`("KOSPI", "KOSDAQ")` 를 그 자리에 적은 곳)이 섞여
있었다. 지금 유니버스는 KOSPI·KOSDAQ 만 받아(`jobs/universe`) 탈이 없지만, KONEX 를 넣는 날 원화/달러 판정·
시총 보정·배당 중단 판정이 엇갈린다. 미국 시장 이름의 정의처는 `sources/nasdaq_symbols`
(`tests/test_market_names.py`).
"""

from __future__ import annotations

#: `stocks.market` 의 국내 값 — KRX `MKT_TP_NM` 그대로(`jobs/universe`)
KR_MARKETS = frozenset({"KOSPI", "KOSDAQ", "KONEX"})
