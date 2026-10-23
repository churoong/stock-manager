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

#: 국내 하루 가격제한폭(±30%, 2015-06-15 부터). **정의처는 여기 하나**(25.1755 — 신호 최근 낙폭 빈틈 판정·가상모의투자
#: 상·하한가 시장가 판정·기업행위 의심·후보 지표 창 넷에 따로 적혀 있었다. 25.1753 은 장부 모듈이 무거운 `signals` 를
#: import 하게 만들어 의존 없는 여기로 옮겼다). 웹 장중 의심 폭은 `tests/test_web_batch_constants_1753.py` 가 대조
KR_DAILY_LIMIT = 0.30
