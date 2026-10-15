"""모의 투자사 조사 자료 적재 (docs/company.md 2·5장, docs/infra.md 25.1463) — 실제 마이그레이션을 올린 메모리 SQLite 에서.

모든 읽기가 신호 기준일 이하인지(미래 정보), 창(90일·5거래일·1년)이 맞는지, 단위(백만원 → 원)가 맞는지를 본다.
"""

from __future__ import annotations

import pytest

from batch.jobs import company as job
from batch.services import company as co
from batch.services import report_picks
from tests.test_paper_job_1445 import Client

기준 = "2026-10-15"


def _행(sid: int, ticker: str, market: str, score: float, criteria: bool = True) -> report_picks.SignalRow:
    return report_picks.SignalRow(
        stock_id=sid, ticker=ticker, name=f"종목{sid}", market=market, horizon="mid", signal_type="중기",
        currency="KRW", buy_zone_low=None, buy_zone_high=None, suggested_weight_pct=10.0, suggested_amount=None,
        size_reduction=1.0, as_of_date=기준, total_score=score,
        factor_scores={"value": 60.0, "quality": 55.0, "growth": None, "momentum": 70.0, "risk": 50.0},
        criteria=[{"label": "x", "display": "1", "threshold": "t", "source": "s", "as_of": 기준, "passed": True}]
        if criteria else [],
    )  # fmt: skip


@pytest.fixture()
def c() -> Client:
    c = Client()
    x = c.conn.execute
    x("UPDATE stocks SET market = 'KOSPI' WHERE id IN (1, 3)")
    for day, close in (("2026-08-01", 9_000), ("2026-09-01", 10_000), ("2026-10-01", 12_000), ("2026-10-16", 20_000)):
        x("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
          " VALUES (1, ?, ?, ?, ?, ?, 1, 'KRW', 't', 't')", [day, close, close, close, close])  # fmt: skip
    for day, broker, target in (
        ("2026-06-01", "옛것", 99_000),  # 90일 밖
        ("2026-09-01", "가", 12_500),  # 그날 종가 10,000 → +25%
        ("2026-10-01", "나", 14_400),  # 그날 종가 12,000 → +20%
        ("2026-10-02", "목표없음", 0),
        ("2026-10-16", "미래", 99_000),  # 기준일 뒤 — 미래 정보
    ):
        x("INSERT INTO kr_opinions (stock_id, date, broker, target_price, source, fetched_at)"
          " VALUES (1, ?, ?, ?, 't', 't')", [day, broker, target or None])  # fmt: skip
    for day, f, o in (("2026-10-07", 9, 9), ("2026-10-08", 1, 1), ("2026-10-10", 2, 0), ("2026-10-13", 3, 0),
                      ("2026-10-14", 4, 0), ("2026-10-15", 5, 1), ("2026-10-16", 100, 100)):  # fmt: skip
        x("INSERT INTO kr_flows (stock_id, date, frgn_net_amt, orgn_net_amt, source, fetched_at)"
          " VALUES (1, ?, ?, ?, 't', 't')", [day, f, o])  # fmt: skip
    # 공매도만 있는 날(투자자별 금액 없음)은 세지 않는다 (25.1042)
    x("INSERT INTO kr_flows (stock_id, date, short_qty, source, fetched_at) VALUES (1, '2026-10-09', 5, 't', 't')")
    x("INSERT INTO universe_members (snapshot_date, stock_id, included, avg_turnover_20d, currency, created_at)"
      " VALUES ('2026-10-10', 1, 1, 30000000, 'KRW', 't')")  # fmt: skip
    for no, day in (("r1", "2025-09-01"), ("r2", "2026-05-01")):  # 1년 밖 · 안
        x("INSERT INTO governance_events (receipt_no, stock_id, day, type, title, covered_from, covered_to, source,"
          " computed_at) VALUES (?, 1, ?, 'fraud', 't', 'a', 'b', 't', 't')", [no, day])  # fmt: skip
    for no, day, pct, st in (("c1", "2026-03-01", 6.0, "ok"), ("c2", "2026-07-01", 5.0, "ok"), ("c3", "2026-07-02", 50, "no_row")):
        x("INSERT INTO cb_issuances (receipt_no, stock_id, decided_at, status, shares_pct, source, fetched_at)"
          " VALUES (?, 1, ?, ?, ?, 't', 't')", [no, day, st, pct])  # fmt: skip
    x("INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count, negative_count,"
      " negative_count_7d, decay_halflife_days, method, calc_version, created_at)"
      " VALUES (1, '2026-10-14', 35, 10, 6, 1, 0, 7, 'vader', 1, 't')")  # fmt: skip
    for win, mdd, vol in (("3Y", -0.42, 0.31), ("1Y", -0.2, 0.25)):
        x("INSERT INTO performance_metrics (stock_id, as_of_date, window, mdd, volatility_ann, cagr, data_points,"
          " calc_version, created_at) VALUES (1, '2026-10-14', ?, ?, ?, 0.1, 700, 1, 't')", [win, mdd, vol])  # fmt: skip
    return c


def test_agenda_takes_top_by_score_with_criteria_one_row_per_stock() -> None:
    rows = [_행(1, "B", "KOSPI", 70), _행(1, "B", "KOSPI", 70), _행(2, "A", "KOSPI", 70), _행(3, "C", "KOSPI", 90, False),
            _행(4, "D", "KOSPI", 50)]  # fmt: skip
    assert [r.stock_id for r in job.agenda_rows(rows, 2)] == [2, 1]  # 같은 점수는 티커 순, 근거표 없는 3 은 뺀다


def test_load_dossier_reads_only_up_to_as_of(c: Client) -> None:
    [d] = job.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    # 증권사: 90일 안·기준일 이하·목표가 있는 것만, 상승여력은 그 의견 날짜의 종가로
    assert [(day, b) for day, b, _u in d.broker_upsides] == [("2026-09-01", "가"), ("2026-10-01", "나")]
    assert [round(u, 4) for _d, _b, u in d.broker_upsides] == [0.25, 0.2]
    # 수급: 기준일 이하 투자자별 금액이 있는 최근 5거래일 (5+1)+4+3+2+(1+1) = 17 백만원 → 원
    assert d.flow_days == 5 and d.flow_sum == 17 * job.FLOW_AMOUNT_UNIT and d.flow_date == "2026-10-15"
    assert d.turnover_20d == 30_000_000
    # 공시: 1년 안 사건만, 전환사채는 정상 행 합
    assert d.gov_events == [("2026-05-01", "fraud")] and d.cb_pct == pytest.approx(11.0)
    assert d.sentiment == 35 and d.mdd == -0.42 and d.volatility == 0.31 and d.perf_window == "3Y"
    assert d.regime_index == "KOSPI" and d.regime in ("unknown", None)  # 지수 행이 없으면 국면 모름
    assert d.factors == {"value": 60.0, "quality": 55.0, "growth": None, "momentum": 70.0}
    # 부서 판단으로 이어진다: 증권사 중앙 +22.5% 찬성, 수급 17백만 ÷ 3천만 = 0.57 찬성, 지배구조 사건 → 반대·거부 조건
    assert co.broker(d).stance == 1 and co.flows(d).stance == 1
    assert co.governance(d).veto
    j = job.dossier_json(d)
    assert j["broker"]["n"] == 2 and j["flows"]["days"] == 5


def test_us_dossier_has_no_domestic_reads(c: Client) -> None:
    [d] = job.load_dossiers(c, [_행(2, "AAPL", "NASDAQ", 80)], "US", 기준)
    assert not d.domestic and d.broker_upsides == [] and d.flow_days == 0
    assert co.broker(d).note and co.flows(d).note and co.governance(d).note
