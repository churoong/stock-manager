"""예측 성적표가 거래정지 종목의 예측을 버리지 않는다 (docs/infra.md 25.1085).

기간이 찬 날 오늘 종가가 없으면(정지) 예전엔 `through` 만 넘어가 영영 채점되지 않았다 — 정지·상폐는 대개 나쁜
소식이라 성적표가 살아남은 종목 쪽으로 기울었다.
"""

from __future__ import annotations

import json

from batch.core import db
from batch.jobs import verdicts as job
from tests.test_portfolio_job import MemClient


def _세계() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)
    c = mem.conn
    for sid, t in ((1, "000001"), (2, "000002")):
        c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
                  " VALUES (?, ?, 'KOSPI', 'KR', 'x', 'KRW', 'active', 't', 't')", [sid, t])  # fmt: skip
        c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                  " VALUES (?, '2026-09-07', 100, 100, 'KRW', 't', 't')", [sid])  # fmt: skip
        c.execute("INSERT INTO forecast_log (as_of_date, stock_id, country, close, models_json, computed_at)"
                  " VALUES ('2026-09-07', ?, 'KR', 100, ?, 't')",
                  [sid, json.dumps({"capm": {"1": {"exp": 0.01, "lo68": -0.05, "hi68": 0.08}}})])  # fmt: skip
    return mem


def test_정지_종목은_재개된_날_종가로_채점한다() -> None:
    mem = _세계()
    state: dict = {"logged": ["2026-09-07"]}
    prior: dict = {}
    job.evaluate_due(mem, "KR", "2026-10-07", {1: {"close": 104.0}}, prior, state, [])  # type: ignore[arg-type]
    assert state["through"]["1"] == "2026-09-07" and state["pending"] == [["2026-09-07", 1, 2]]
    assert 2 not in prior
    job.evaluate_due(mem, "KR", "2026-10-08", {1: {"close": 104.0}, 2: {"close": 60.0}}, prior, state, [])  # type: ignore[arg-type]
    assert 2 in prior and state["market"]["capm"]["1"][0] == 2 and state["pending"] == []
    assert prior[1]["capm"]["1"][0] == 1  # 1번은 한 번만 셌다


def test_너무_오래_멈추면_버리고_센다() -> None:
    mem = _세계()
    state: dict = {"logged": ["2026-09-07"], "through": {"1": "2026-09-07"}, "pending": [["2026-09-07", 1, 2]]}
    job.evaluate_due(mem, "KR", "2027-01-30", {}, {}, state, [])  # type: ignore[arg-type]
    # 1개월 예측은 1개월 + 90일을 넘겨 버리고 센다(그날 새로 찬 3·6개월 예측은 따로 기다린다)
    assert ["2026-09-07", 1, 2] not in state["pending"] and state["dropped"] == 1


def test_마지막_종가가_0_이면_매물대만_빠진다() -> None:
    """예전엔 0 으로 나눠 주간 지표 작업 전체가 저장 전에 죽었다 (25.1085)."""
    from batch.services import history as hs

    n = 300
    closes = [100.0 + i % 7 for i in range(n)]
    closes[-1] = 0.0
    assert hs.volume_profile([f"d{i:04d}" for i in range(n)], closes, [1000.0] * n) is None


def test_계절성은_안_끝난_이번_달을_넣지_않는다() -> None:
    """10-08 기준이면 10월 6거래일 수익이 '한 달' 표본으로 들어갔다 (25.1085)."""
    from datetime import date, timedelta

    from batch.services import history as hs

    d0 = date(2023, 1, 2)
    날 = [d0 + timedelta(days=i) for i in range(0, 1376) if (d0 + timedelta(days=i)).weekday() < 5]
    날 = [d for d in 날 if d <= date(2026, 10, 8)]
    종가 = [100.0 * (1.001 ** i) for i in range(len(날))]
    종가[-6:] = [x * 2 for x in 종가[-6:]]  # 이번 달(10월) 며칠만 두 배 — 들어가면 10월 평균이 튄다
    s = hs.season([d.isoformat() for d in 날], 종가)
    assert s["until"] == "2026-09"  # 달 열쇠(YYYY-MM)
    assert s["months"]["10"]["avg"] < 0.1
    assert hs._달이_끝났나("2026-10-30") and not hs._달이_끝났나("2026-10-29")  # 금요일·다음 월요일이 11월
