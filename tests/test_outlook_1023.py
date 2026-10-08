"""가격·가치 진단 — 예측 대신 근거 있는 기준점 (docs/analysis.md 9장, docs/infra.md 25.1023).

2026-10-08 사용자: "종목분석에는 이런 예상치와 현재 주가에 대한 분석 등이 있어야 하는 거 아니야?" — 결론 한 줄만 있었다.
"""

from __future__ import annotations

import json
from datetime import date

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import verdict as vd
from tests.test_portfolio_job import MemClient

오늘 = date(2026, 10, 8)


def _진단(**kw):  # noqa: ANN003, ANN202
    base = dict(close=1069.0, close_date="2026-10-07", currency="KRW",
                momentum={"momentum_3m": -0.082, "momentum_6m": 0.05, "high_52w_proximity": 0.71, "as_of": "2026-10-07"},
                risk={"volatility_ann": 0.35, "mdd": -0.42, "window": "1Y", "as_of_date": "2026-10-07"},
                band={"p20": 0.5, "p50": 0.7, "p80": 0.9, "current_value": 0.6, "band_rank": 18.0, "band_close": 1100.0,
                      "price_date": "2026-10-02"},
                opinions=[], today=오늘)  # fmt: skip
    base.update(kw)
    return vd.outlook(**base)


def test_현재_주가_위치는_모멘텀_원값과_성과_지표로() -> None:
    o = _진단()
    assert o["lines"][0] == ("종가 1,069원(2026-10-07) · 3개월 -8.2% · 6개월 +5.0% · 52주 고점의 71% · "
                             "변동성 35%·최대 낙폭 -42%(1Y)")  # fmt: skip
    assert {e["label"] for e in o["evidence"]} >= {"종가", "3개월 수익률", "52주 고점 대비"}


def test_밴드_기준_가격은_분위_PBR_곱하기_주당순자산() -> None:
    o = _진단()
    # 주당순자산 = 밴드 계산 종가 1,100 ÷ 현재 PBR 0.6 → 중앙값 0.7 이면 1,283원
    assert round(o["band"]["prices"]["p50"]) == round(1100 / 0.6 * 0.7)
    assert "자기 3년 PBR 밴드의 18% 지점(장기 신호 문턱 30% 이하)" in o["lines"][1]
    assert "예측이 아님" in o["lines"][1]
    # 문턱 위면 문턱 말을 붙이지 않는다 — 새 문턱을 만들지 않는다
    o2 = _진단(band={"p20": 0.5, "p50": 0.7, "p80": 0.9, "current_value": 0.8, "band_rank": 64.0, "band_close": 1100.0,
                     "price_date": "d"})  # fmt: skip
    assert "문턱" not in o2["lines"][1]
    # 밴드를 못 내면 까닭을 말한다
    o3 = _진단(band=None, band_note="상장주식수 없음")
    assert o3["lines"][1] == "가치 밴드는 내지 못했습니다 — 상장주식수 없음" and "band" not in o3


def test_증권사_목표가는_증권사마다_최신_하나_지난_90일() -> None:
    의견 = [
        {"date": "2026-09-01", "broker": "가", "target_price": 1500},
        {"date": "2026-08-01", "broker": "나", "target_price": 1400},
        {"date": "2026-09-20", "broker": "나", "target_price": 1600},  # 나의 최신
        {"date": "2026-06-01", "broker": "다", "target_price": 3000},  # 90일 밖
        {"date": "2026-09-25", "broker": "라", "target_price": None},  # 목표가 없음
    ]
    o = _진단(opinions=의견)
    c = o["consensus"]
    assert (c["brokers"], c["median"], c["low"], c["high"]) == (2, 1550.0, 1500.0, 1600.0)
    assert o["lines"][-1] == "증권사 2곳 목표가 중앙값 1,550원(범위 1,500원~1,600원) — 종가 대비 +45.0% (증권사의 예측)"
    assert vd.consensus([], 오늘) is None


def test_진단_근거표가_결론의_근거표에_붙는다() -> None:
    o = _진단()
    r = vd.build(vd.Inputs(name="가", ticker="1", score={"as_of": "d", "total": 50.0}, outlook=o))
    assert r["outlook"]["lines"] == o["lines"] and "evidence" not in r["outlook"]
    assert any(e["label"] == "밴드 중앙값 기준 가격" for e in r["evidence"])


def test_일일_의견이_시장_한_번에_재료를_읽어_진단을_붙인다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
              " VALUES (1, '008730', 'KOSPI', 'KR', '가', 'KRW', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
              " weights_json, rank_in_market, calc_version, created_at) VALUES (1, '2026-10-07', 60, '{}', 0, '{}', 1,"
              " 10, 't')")  # fmt: skip
    for d, px in (("2026-10-02", 1100), ("2026-10-07", 1069)):
        c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                  " VALUES (1, ?, ?, ?, 'KRW', 't', 't')", [d, px, px])  # fmt: skip
    c.execute("INSERT INTO factors (stock_id, as_of_date, factor, raw_json, peer_group, peer_size, calc_version,"
              " created_at) VALUES (1, '2026-10-07', 'momentum', ?, 'g', 10, 10, 't')",
              [json.dumps({"momentum_3m": -0.1, "high_52w_proximity": 0.8})])  # fmt: skip
    c.execute("INSERT INTO valuation_bands (stock_id, as_of_date, metric, current_value, p20, p30, p50, p80, sample,"
              " band_rank, price_date, currency, calc_version, created_at) VALUES (1, '2026-10-03', 'PBR', 0.6, 0.5,"
              " 0.55, 0.7, 0.9, 700, 18, '2026-10-02', 'KRW', 1, 't')")  # fmt: skip
    c.execute("INSERT INTO kr_opinions (stock_id, date, broker, target_price, source, fetched_at)"
              " VALUES (1, '2026-09-20', '가', 1500, 'kis', 't')")  # fmt: skip
    warnings: list[str] = []
    mem.batch(job.build_market(mem, "KR", 오늘, warnings))  # type: ignore[arg-type]
    assert warnings == []
    상세 = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    o = 상세["outlook"]
    assert o["close"] == 1069 and o["momentum"]["momentum_3m"] == -0.1
    assert round(o["band"]["prices"]["p50"]) == round(1100 / 0.6 * 0.7)
    assert o["consensus"]["median"] == 1500 and o["consensus"]["brokers"] == 1
