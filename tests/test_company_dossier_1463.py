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
    # 10-09 한글날 휴장 — 최근 5거래일은 10-15·14·13·12·08
    for day, f, o in (("2026-10-07", 9, 9), ("2026-10-08", 1, 1), ("2026-10-12", 2, 0), ("2026-10-13", 3, 0),
                      ("2026-10-14", 4, 0), ("2026-10-15", 5, 1), ("2026-10-16", 100, 100)):  # fmt: skip
        x("INSERT INTO kr_flows (stock_id, date, frgn_net_amt, orgn_net_amt, source, fetched_at)"
          " VALUES (1, ?, ?, ?, 't', 't')", [day, f, o])  # fmt: skip
    # 공매도만 있는 날(투자자별 금액 없음)은 세지 않는다 (25.1042)
    x("INSERT INTO kr_flows (stock_id, date, short_qty, source, fetched_at) VALUES (1, '2026-10-09', 5, 't', 't')")
    x("INSERT INTO universe_members (snapshot_date, stock_id, included, avg_turnover_20d, currency, created_at)"
      " VALUES ('2026-10-10', 1, 1, 30000000, 'KRW', 't')")  # fmt: skip
    for no, day in (("r1", "2025-09-01"), ("r2", "2026-05-01")):  # 1년 밖 · 안
        x("INSERT INTO governance_events (receipt_no, stock_id, day, type, title, covered_from, covered_to, source,"
          " computed_at) VALUES (?, 1, ?, 'fraud', 't', '2025-01-01', '2026-10-15', 't', 't')", [no, day])  # fmt: skip
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
    # 부서 판단으로 이어진다: 증권사는 의견 있는 종목이 하나뿐이라 50 백분위(중립), 수급 17백만 ÷ 3천만 = 0.57 찬성,
    # 지배구조 사건 → 반대·거부 조건
    assert co.broker([d])[1].stance == 0 and "+22.5%" in co.broker([d])[1].evidence[0]["display"]
    assert co.flows(d).stance == 1
    assert co.governance(d).veto
    j = job.dossier_json(d)
    assert j["broker"]["n"] == 2 and j["flows"]["days"] == 5


def test_us_dossier_has_no_domestic_reads(c: Client) -> None:
    [d] = job.load_dossiers(c, [_행(2, "AAPL", "NASDAQ", 80)], "US", 기준)
    assert not d.domestic and d.broker_upsides == [] and d.flow_days == 0
    assert co.broker([d])[2].note and co.flows(d).note and co.governance(d).note


def test_flows_need_all_recent_sessions_and_both_investors(c: Client) -> None:
    """최근 5거래일이 하나라도 빠지면 자료 없음 — 더 묵은 5일을 "최근" 으로 쓰지 않는다. 기관 금액이 빈 날은 0 이 아니다 (25.1465)."""
    c.conn.execute("UPDATE kr_flows SET orgn_net_amt = NULL WHERE date = '2026-10-13'")
    [d] = job.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert d.flow_days == 4 and co.flows(d).note


def test_governance_without_coverage_is_no_data_not_neutral(c: Client) -> None:
    """사건 표가 기준일 앞 1년을 덮지 않으면 "사건 없음 — 중립" 이 아니라 자료 없음 — 거부권이 조용히 꺼지지 않게 (25.1465)."""
    c.conn.execute("DELETE FROM governance_events WHERE day = '2026-05-01'")
    c.conn.execute("DELETE FROM cb_issuances")
    [d] = job.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert d.gov_covered and co.governance(d).note.startswith("사건 없음")  # type: ignore[union-attr]
    c.conn.execute("UPDATE governance_events SET covered_from = '2026-01-01'")
    [d] = job.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert not d.gov_covered and co.governance(d).note.startswith("자료 없음")  # type: ignore[union-attr]
    c.conn.execute("DELETE FROM governance_events")
    [d] = job.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert co.governance(d).note.startswith("자료 없음")  # type: ignore[union-attr]


def test_population_ranks_beyond_agenda(c: Client) -> None:
    """점수 부서의 백분위는 신호 종목 전체 안에서 — 안건 크기(회사마다 20·30)가 같은 종목의 입장을 바꾸지 않게 (25.1465 [필수] 1)."""
    rows = [_행(i, f"T{i:02d}", "KOSPI", 100 - i) for i in range(1, 41)]
    rows = [r.__class__(**{**r.__dict__, "factor_scores": {"value": 100 - r.stock_id, "quality": 50, "momentum": 50}})
            for r in rows]  # fmt: skip
    pop = job.population_dossiers(c, rows, "KR", 기준)
    assert len(pop) == 40
    ch = {**co.CHARTERS["A"], "weights": {"research": 1, "quant": 1}, "lead": "research", "veto": []}
    작은 = co.opinions(pop[:20], ch, pop)
    큰 = co.opinions(pop[:30], ch, pop)
    # 안건 20 이든 30 이든 같은 종목(밸류 90, 신호 40종목 안 77 백분위)은 같은 입장(찬성)
    assert 작은[10]["research"] == 큰[10]["research"] and 작은[10]["research"].stance == 1
    # 모집단 없이 안건 안에서만 매기면 20개 안건에서는 53, 30개 안건에서는 69 백분위 — 둘 다 중립(고치기 전의 모양)
    assert co.opinions(pop[:20], ch)[10]["research"].stance == 0 and co.opinions(pop[:30], ch)[10]["research"].stance == 0


def test_replay_runs_on_empty_history(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """소급 재생이 실제 스키마에서 끝까지 돈다(신호가 없으면 회의 0) — 운영에서 처음 돌다 죽지 않게."""
    import importlib.util
    from pathlib import Path

    경로 = Path(__file__).resolve().parent.parent / "scripts" / "company_replay.py"
    spec = importlib.util.spec_from_file_location("company_replay", 경로)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.replay(c, "KR", 10, 5) == ([], set())
