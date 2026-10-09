"""재무·수급 사건과 그 뒤 — 실적 발표 반응·상승의 출처·공매도 급증 뒤 (docs/analysis.md 40~42장, docs/infra.md 25.1063).

사건 날을 하루만 잘못 잡아도 "발표 뒤 평균 +3%" 가 발표 **전** 움직임을 센다. 그래서 기준가가 접수일 전날 종가인지,
분해가 곱으로 주가를 되살리는지, 급증이 앞 창의 중앙값과만 견주는지를 손으로 셀 수 있는 계열로 묶는다.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

from batch.jobs import metrics as metrics_job
from batch.services import event_history as ev
from batch.services import verdict as vd
from tests.test_metrics_beta import client as client  # noqa: F401 — 픽스처
from tests.test_metrics_beta import 계열, 수익률, 시세넣기
from tests.test_metrics_beta import 기준일 as 지표_기준일


def _날(n: int, d0: date = date(2024, 1, 1)) -> list[str]:
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _재무(fy: int, code: str, rd: str, oi: float, ni: float = 100.0, cons: int = 1) -> dict:
    return {"stock_id": 1, "fiscal_year": fy, "report_code": code, "consolidated": cons, "report_date": rd,
            "operating_income": oi, "net_income": ni}  # fmt: skip


def test_발표_반응은_접수일_전날_종가에서() -> None:
    날 = _날(600)
    c = [100.0] * 600
    i = 날.index("2025-03-15")
    for k in range(i, 600):
        c[k] = 110.0  # 접수일 당일에 +10% 뛰고 그대로
    지수 = {d: 1000.0 for d in 날}
    # 별도 행이 같은 해 연결보다 늦게 접수됐어도 기준(가장 최근 보고서의 연결)이 다르면 보지 않는다
    재무 = [_재무(2023, "11011", "2024-03-15", 100), _재무(2024, "11011", "2025-03-15", 150),
          _재무(2023, "11011", "2024-03-20", 10, cons=0)]  # fmt: skip
    e = ev.earnings_reactions(재무, 날, c, 지수)
    r = e["recent"][0]
    assert r["date"] == "2025-03-15" and r["yoy"] == 0.5 and r["r1"] == 0.1 and r["x5"] == 0.1
    assert e["grew"] == {"n": 1, "avg": 0.1, "up": 1} and e["shrank"] is None and e["basis"] == "연결"
    # 2024-03-15 발표는 앞해 보고서가 없어 증가율이 없다 — 반응은 재지만 늘었다·줄었다로 나누지 않는다
    assert e["events"] == 2 and e["recent"][1]["yoy"] is None


def test_상승의_출처는_곱으로_주가를_되살린다() -> None:
    날 = _날(1300, date(2021, 1, 1))
    c = [100.0] * 1300
    i = 날.index("2022-03-20")
    c[i:] = [100.0] * (1300 - i)
    c[-1] = 150.0
    재무 = [_재무(2021, "11011", "2022-03-20", 0, ni=100), _재무(2024, "11011", "2025-03-20", 0, ni=120)]
    배당 = [{"fiscal_year": 2022, "cash_dividend_total": 1000}, {"fiscal_year": 2024, "cash_dividend_total": 2000},
          {"fiscal_year": 2021, "cash_dividend_total": 99999}]  # 시작 해 배당은 그 사이가 아니다  # fmt: skip
    s = ev.return_sources(재무, 배당, 날, c, 1000.0)
    assert s["price"] == 0.5 and s["earnings"] == 0.2
    assert math.isclose((1 + s["earnings"]) * (1 + s["multiple"]), 1.5, abs_tol=1e-4)
    assert math.isclose(s["dividends"], 3000 / (100 * 1000))
    assert s["from_year"] == 2021 and s["to_year"] == 2024
    # 순이익이 0 이하인 끝이 있으면 나누지 않는다
    assert ev.return_sources([_재무(2021, "11011", "2022-03-20", 0, ni=-5), 재무[1]], [], 날, c, 1000.0) is None


def test_공매도_급증은_앞_창_중앙값과만_견준다() -> None:
    날 = _날(120)
    c = [100 * 1.001**i for i in range(120)]
    값 = [1.0 if i % 2 else 1.5 for i in range(120)]  # 앞 20일 중앙값 1.25
    값[40] = 2.5  # 정확히 두 배 — 사건. 오늘을 창에 넣으면 중앙값이 1.5 가 되어 사건이 아니게 된다
    값[42] = 5.0  # 식힘 안 — 같은 사건
    값[80] = 2.4  # 두 배 미만 — 사건 아님
    s = ev.short_surges([{"date": d, "short_vol_pct": v} for d, v in zip(날, 값, strict=True)], 날, c)
    assert s["events"] == 1 and s["last"] == 날[40]
    assert math.isclose(s["h"]["5"]["median"], 1.001**5 - 1, abs_tol=1e-4) and s["h"]["5"]["n"] == 1
    assert s["base"]["20"]["n"] == 100  # 20거래일 뒤가 있는 날 수
    assert ev.short_surges([{"date": d, "short_vol_pct": 1.0} for d in 날[:20]], 날, c) is None


def test_진단_줄과_주간_작업(client) -> None:  # noqa: ANN001
    h = {"earnings": {"basis": "연결", "events": 3, "grew": {"n": 2, "avg": 0.03, "up": 2}, "shrank": None},
         "sources": {"from_year": 2021, "to_year": 2024, "since": "2022-03-21", "price": 0.5, "earnings": 0.2,
                     "multiple": 0.25, "dividends": 0.03},
         "short": {"since": "2026-01-01", "events": 2, "h": {"5": {"median": -0.01, "up": 0.5, "n": 2}},
                   "base": {"5": {"median": 0.002, "up": 0.52, "n": 200}}}}  # fmt: skip
    o = vd.outlook(close=1.0, close_date="d", currency="KRW", momentum=None, risk=None, band=None, opinions=[],
                   today=date(2026, 10, 8), history=h)  # fmt: skip
    assert any(x.startswith("실적 발표 반응(연결, 3번): 영업이익이 늘어난 발표 2번 평균 +3.0%") for x in o["lines"])
    assert any("주가 +50.0% = 순이익 +20.0% × 배수(PER) +25.0% · 그 사이 배당 +3.0%" in x for x in o["lines"])
    assert any(x.startswith("공매도 급증 뒤(2026-01-01~, 2번)") for x in o["lines"])
    # 주간 작업 — 재무·수급을 나라마다 한 번 읽어 쌓는다
    시세넣기(client, 1, 계열(1000, 수익률(1500)))
    c = client.conn
    for fy, rd, oi in ((2023, "2024-03-15", 100), (2024, "2025-03-14", 130), (2025, "2026-03-16", 90)):
        c.execute("INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
                  " receipt_no, currency, unit, operating_income, net_income, source, fetched_at)"
                  " VALUES (1, ?, '11011', 'A', 1, ?, ?, 'KRW', 'KRW', ?, 50, 'dart', 't')", [fy, rd, f"r{fy}", oi])  # fmt: skip
    metrics_job.compute_all(client, [(1, "005930", "KR")], ["5Y"], 지표_기준일)  # type: ignore[arg-type]
    행 = json.loads(c.execute("SELECT stats_json FROM price_patterns").fetchone()[0])
    assert 행["earnings"]["events"] == 3 and 행["earnings"]["recent"][0]["yoy"] == round(90 / 130 - 1, 4)
    assert "sources" in 행 and "short" in 행
