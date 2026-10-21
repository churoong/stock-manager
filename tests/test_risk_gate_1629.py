"""주문 전 위험 점검 감사 [필수] 3건 (docs/infra.md 25.1629, docs/paper.md 11장).

1. 같은 실행에서 거둔 매수 회차가 관문 문맥(현금·보유)에 남아 정상 매수를 "현금 넘음" 으로 막았다
2. 기준 종가 묵음을 달력일 7일로 세어 긴 연휴(2025 추석) 뒤 첫 아침 매수가 모두 막혔다
3. 보유가 오늘 기업행위 멈춤에 들면 시가 → 원가 평가 변화가 "하루 손실" 로 읽혀 매수가 막혔다
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from typing import Any

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import report_picks
from batch.services import risk_gate as rg
from tests.test_paper_job_1445 import Client, _시세, _신호, 설정


@pytest.fixture(autouse=True)
def _틀(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)


def _입금(c: Client, day: str) -> None:
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES (?, 1000000, 'deposit', ?, 'k1')", [day, f"{day}T00:00:00+00:00"])  # fmt: skip


def _시세3(c: Client, day: str, o: float, cl: float) -> None:
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (3, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [day, o, max(o, cl), min(o, cl), cl])  # fmt: skip


def _돌림(c: Client, mp: pytest.MonkeyPatch, trade_date: str, today: str, now: str, rows: list, settings: dict,
         after_close: bool = False) -> dict[str, Any]:  # fmt: skip
    mp.setattr(cal, "local_today", lambda _m=None: date.fromisoformat(today))
    _줄, 기록 = job.run_step(c, market="KR", trade_date=trade_date, late=False, signal_rows=rows, settings=settings,
                            price_series={}, series_unknown={}, allocate=bool(rows), now=datetime.fromisoformat(now),
                            after_close=after_close)  # fmt: skip
    return 기록


def test_거둔_회차는_관문_현금에서_빠진다(monkeypatch: pytest.MonkeyPatch) -> None:
    """손절 보유의 남은 회차를 거둔 실행 — 관문 현금은 장부 현금 그대로, 배분이 낸 매수는 통과."""
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    c = Client(accounts=("B",))
    _입금(c, "2026-10-01")
    신호 = lambda d: dataclasses.replace(_신호(), as_of_date=d, close_date=d)  # noqa: E731
    _시세(c, "2026-10-13", 10_000, 10_000)
    _돌림(c, monkeypatch, "2026-10-13", "2026-10-14", "2026-10-13T23:30:00+00:00", [신호("2026-10-13")], 설정)
    _시세(c, "2026-10-14", 10_000, 9_900)
    _돌림(c, monkeypatch, "2026-10-14", "2026-10-15", "2026-10-15T07:00:00+00:00", [신호("2026-10-14")], 설정,
          after_close=True)  # fmt: skip
    _시세(c, "2026-10-15", 9_300, 9_200)  # 손절 → 다음 장 회차 거둠
    _시세3(c, "2026-10-15", 10_000, 10_000)
    s3 = dataclasses.replace(_신호(), stock_id=3, ticker="000660", name="종목3", suggested_weight_pct=95.0,
                             as_of_date="2026-10-15", close_date="2026-10-15",
                             tranche_plan=[{"step": 1, "ratio": 1.0, "price": 10_000, "amount": None}])  # fmt: skip
    넓은 = {"max_weight_per_sector": 100.0, "max_weight_per_stock": 100.0, "min_order_amount": 0}
    본: dict[str, Any] = {}
    원래 = rg.gate

    def 엿봄(후보: list, ctx: rg.Context) -> Any:
        if 후보 and 후보[0].side == "buy":
            본["ctx"] = ctx
        return 원래(후보, ctx)

    monkeypatch.setattr(rg, "gate", 엿봄)
    기록 = _돌림(c, monkeypatch, "2026-10-15", "2026-10-16", "2026-10-15T23:30:00+00:00", [s3], 넓은)
    a = 기록["accounts"]["B"]
    assert a["buys_withdrawn"] == 3
    장부현금 = c.q("SELECT cash_krw FROM paper_daily WHERE account_id='B' ORDER BY as_of DESC LIMIT 1")[0][0]
    assert 본["ctx"].cash_available_krw == pytest.approx(장부현금)  # 거둔 예약을 또 빼지 않는다
    assert 3 not in 본["ctx"].holding_krw and 본["ctx"].holding_krw[1] < 50_000  # 종목 1 은 평가만(예약 0)
    assert a["risk_blocked"] == 0
    assert c.q("SELECT kind FROM paper_decisions WHERE decided_on='2026-10-15' AND stock_id=3") == [("buy",)]


def test_긴_연휴_뒤_첫_아침도_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    """2025 추석 — 마지막 장 10-02, 다음 장 10-10(달력일 8일). 바로 앞 거래일 종가는 묵음이 아니다."""
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    assert cal.next_session("KR", date(2025, 10, 2)) == date(2025, 10, 10)
    c = Client(accounts=("B",))
    _입금(c, "2025-09-01")
    _시세(c, "2025-10-02", 10_000, 10_000)
    s = dataclasses.replace(_신호(), as_of_date="2025-10-02", close_date="2025-10-02")
    기록 = _돌림(c, monkeypatch, "2025-10-02", "2025-10-10", "2025-10-09T23:30:00+00:00", [s], 설정)
    assert 기록["accounts"]["B"]["risk_blocked"] == 0
    assert c.q("SELECT kind FROM paper_decisions") == [("buy",)]
    assert c.q("SELECT COUNT(*) FROM paper_orders")[0][0] == 3


def test_ref_floor_는_거래일로_센다() -> None:
    # 평소 주: 월요일 앞 5거래일 = 지난 월요일(달력일 7일, 예전 잣대와 같다)
    assert rg.ref_floor("KR", date(2026, 10, 26)) == "2026-10-19"
    # 긴 연휴 뒤: 10-10 앞 5거래일 = 9-26 — 10-02 종가는 묵음 아님
    assert rg.ref_floor("KR", date(2025, 10, 10)) == "2025-09-26"


def test_관문_묵음_잣대() -> None:
    def 문맥(**k: Any) -> rg.Context:
        return rg.Context(equity_krw=1e6, day_return_pct=None, cash_available_krw=1e6, holding_krw={}, sector_of={},
                          max_stock_pct=100, max_sector_pct=100, today="2025-10-10", **k)  # fmt: skip

    후보 = [rg.Candidate(1, "buy", 1000.0, "2025-10-02")]
    assert rg.gate(후보, 문맥(ref_floor="2025-09-26"))[1] == []
    _통, 막 = rg.gate([rg.Candidate(1, "buy", 1000.0, "2025-09-25")], 문맥(ref_floor="2025-09-26"))
    assert 막 and 막[0][1][0][0] == "stale" and "5거래일" in 막[0][1][0][1]
    # 달력을 주지 않은 문맥은 옛 잣대(달력일 7일) 그대로
    assert rg.gate(후보, 문맥())[1][0][1][0][0] == "stale"


def test_멈춤으로_원가_평가가_바뀐_날은_하루_손실을_보지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    멈춤: set[int] = set()
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set(멈춤))
    c = Client(accounts=("B",))
    _입금(c, "2026-10-01")
    긴 = lambda d: dataclasses.replace(_신호(), horizon="long", as_of_date=d, close_date=d)  # noqa: E731
    _시세(c, "2026-10-13", 10_000, 10_000)
    _돌림(c, monkeypatch, "2026-10-13", "2026-10-14", "2026-10-13T23:30:00+00:00", [긴("2026-10-13")], 설정)
    _시세(c, "2026-10-14", 9_500, 9_500)  # 세 회차 다 참
    _돌림(c, monkeypatch, "2026-10-14", "2026-10-15", "2026-10-14T23:30:00+00:00", [], 설정)
    _시세(c, "2026-10-15", 13_500, 13_500)  # 평가이익 +39%
    _돌림(c, monkeypatch, "2026-10-15", "2026-10-16", "2026-10-15T23:30:00+00:00", [], 설정)
    멈춤.add(1)  # 시세는 그대로, 기업행위 경계만 — 평가가 원가로 내려가 지수가 약 −3.5%
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (1, '2026-10-16', 13500, 13500, 13500, 13500, 1000, 'KRW', 't', 't')")  # fmt: skip
    s3 = dataclasses.replace(_신호(), stock_id=3, ticker="000660", name="종목3", horizon="long",
                             as_of_date="2026-10-16", close_date="2026-10-16")  # fmt: skip
    기록 = _돌림(c, monkeypatch, "2026-10-16", "2026-10-19", "2026-10-18T23:30:00+00:00", [s3], 설정)
    a = 기록["accounts"]["B"]
    지수 = [r[0] for r in c.q("SELECT twr_index FROM paper_daily ORDER BY as_of")]
    assert 지수[-1] / 지수[-2] - 1 < rg.DAILY_LOSS_HALT_PCT / 100  # 지수는 그대로 깎였다(기록은 손대지 않음)
    assert a["day_loss_basis"] == "보정" and a["risk_blocked"] == 0  # 멈춘 종목은 어제 평가 그대로 — 0%
    assert c.q("SELECT kind FROM paper_decisions WHERE decided_on='2026-10-16'") == [("buy",)]


def _멈춤_계좌(monkeypatch: pytest.MonkeyPatch, 멈춤: set[int], 어제부터: bool = False) -> Client:
    """종목 1(+35% 평가이익, 작은 몫)·종목 3(큰 몫)을 든 B — 10-16 아침 전까지. `어제부터` 면 10-15 행부터 종목 1 멈춤."""
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set(멈춤))
    c = Client(accounts=("B",))
    _입금(c, "2026-10-01")
    넓은 = {"max_weight_per_sector": 100.0, "max_weight_per_stock": 100.0, "min_order_amount": 0}
    s1 = dataclasses.replace(_신호(), horizon="long", suggested_weight_pct=10.0, as_of_date="2026-10-13",
                             close_date="2026-10-13", tranche_plan=[{"step": 1, "ratio": 1.0, "price": 10_000}])
    s3 = dataclasses.replace(_신호(), stock_id=3, ticker="000660", name="종목3", horizon="long",
                             suggested_weight_pct=80.0, as_of_date="2026-10-13", close_date="2026-10-13",
                             tranche_plan=[{"step": 1, "ratio": 1.0, "price": 10_000}])  # fmt: skip
    _시세(c, "2026-10-13", 10_000, 10_000)
    _시세3(c, "2026-10-13", 10_000, 10_000)
    _돌림(c, monkeypatch, "2026-10-13", "2026-10-14", "2026-10-13T23:30:00+00:00", [s1, s3], 넓은)
    _시세(c, "2026-10-14", 10_000, 10_000)
    _시세3(c, "2026-10-14", 10_000, 10_000)
    _돌림(c, monkeypatch, "2026-10-14", "2026-10-15", "2026-10-14T23:30:00+00:00", [], 넓은)
    _시세(c, "2026-10-15", 13_500, 13_500)
    _시세3(c, "2026-10-15", 10_000, 10_000)
    if 어제부터:
        멈춤.add(1)
    _돌림(c, monkeypatch, "2026-10-15", "2026-10-16", "2026-10-15T23:30:00+00:00", [], 넓은)
    assert {r[0] for r in c.q("SELECT stock_id FROM paper_fills")} == {1, 3}
    return c


def _종목4로_사봄(c: Client, monkeypatch: pytest.MonkeyPatch, 종목1: float, 종목3: float) -> dict[str, Any]:
    c.conn.execute("INSERT OR IGNORE INTO stocks (id, ticker, market, country, name_ko, sector, currency, status,"
                   " source, fetched_at, asset_type) VALUES (4, '000270', 'M', 'KR', '종목4', '다른업종', 'KRW',"
                   " 'active', 't', 't', 'stock')")  # fmt: skip
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (1, '2026-10-16', ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [종목1] * 4)  # fmt: skip
    _시세3(c, "2026-10-16", 종목3, 종목3)
    s4 = dataclasses.replace(_신호(), stock_id=4, ticker="000270", name="종목4", horizon="long",
                             suggested_weight_pct=5.0, as_of_date="2026-10-16", close_date="2026-10-16")  # fmt: skip
    넓은 = {"max_weight_per_sector": 100.0, "max_weight_per_stock": 100.0, "min_order_amount": 0}
    return _돌림(c, monkeypatch, "2026-10-16", "2026-10-19", "2026-10-18T23:30:00+00:00", [s4], 넓은)["accounts"]["B"]


def test_멈춤과_진짜_하락이_겹친_날도_막는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """교차검증 [권고] 1 — 멈춘 종목만 어제 평가로 두므로 다른 보유의 −12% 는 그대로 하루 손실이다."""
    멈춤: set[int] = set()
    c = _멈춤_계좌(monkeypatch, 멈춤)
    멈춤.add(1)
    a = _종목4로_사봄(c, monkeypatch, 13_500, 8_800)
    assert a["day_loss_basis"] == "보정"
    assert c.q("SELECT kind FROM paper_decisions WHERE decided_on='2026-10-16' AND stock_id=4")[0][0] == "blocked"


def test_이틀째_이어진_멈춤은_평소대로_본다(monkeypatch: pytest.MonkeyPatch) -> None:
    """어제부터 멈춘 종목은 기준이 바뀌지 않았다 — 보정 없이 지수 그대로(진짜 하락은 막힘)."""
    멈춤: set[int] = set()
    c = _멈춤_계좌(monkeypatch, 멈춤, 어제부터=True)
    assert c.q("SELECT unknown_json IS NOT NULL FROM paper_daily ORDER BY as_of DESC LIMIT 1") == [(1,)]
    a = _종목4로_사봄(c, monkeypatch, 13_500, 8_800)
    assert "day_loss_basis" not in a
    assert c.q("SELECT kind FROM paper_decisions WHERE decided_on='2026-10-16' AND stock_id=4")[0][0] == "blocked"


def test_달력_범위_밖은_옛_잣대() -> None:
    """교차검증 [권고] 2 — 긴 백테스트(`--years 20`)가 첫 매수일에 멈추지 않게."""
    assert rg.ref_floor("KR", date(1990, 1, 10)) is None
    assert rg.stale("1990-01-01", date(1990, 1, 10), None) is True
    assert rg.stale("1990-01-05", date(1990, 1, 10), None) is False
    assert rg.stale("2025-10-02", date(2025, 10, 10), rg.ref_floor("KR", date(2025, 10, 10))) is False


def test_무작위_대체도_긴_연휴_뒤_자리를_비우지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """R 의 대체 종목 자료 검사도 관문과 같은 거래일 잣대(25.1629) — 달력일이면 추석 뒤 첫날 자리가 비었다."""
    from batch.services import paper as pp

    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    c = Client(accounts=None)
    for sid in (1, 3):
        c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source,"
                       " fetched_at) VALUES (?, '2025-10-02', 10000, 10000, 10000, 10000, 1000, 'KRW', 't', 't')",
                       [sid])  # fmt: skip
    s = dataclasses.replace(_신호(), as_of_date="2025-10-02", close_date="2025-10-02")
    기록: dict[str, Any] = {}
    줄 = job.random_substitute(c, [s], account="R1", market="KR", pool=[1, 3], cooldown=set(), stock_cache={},
                               today=date(2025, 10, 10), 기록=기록)  # fmt: skip
    assert [r.stock_id for r in 줄] == [pp.random_order([1, 3], "R1|KR|1")[0]]


def test_핵심_H_백테스트_관문도_거래일_잣대() -> None:
    import inspect

    from batch.services import core_strategy

    assert "ref_floor=rg.ref_floor(self.market.country, today)" in inspect.getsource(core_strategy)


def test_원가_평가_종목이_없으면_하루_손실을_본다(monkeypatch: pytest.MonkeyPatch) -> None:
    """원가 평가 종목이 없는 평소 날 — 진짜 −3% 넘는 하락은 막는다."""
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    c = Client(accounts=("B",))
    _입금(c, "2026-10-01")
    긴 = lambda d: dataclasses.replace(_신호(), horizon="long", as_of_date=d, close_date=d)  # noqa: E731
    _시세(c, "2026-10-13", 10_000, 10_000)
    _돌림(c, monkeypatch, "2026-10-13", "2026-10-14", "2026-10-13T23:30:00+00:00", [긴("2026-10-13")], 설정)
    _시세(c, "2026-10-14", 9_500, 9_500)
    _돌림(c, monkeypatch, "2026-10-14", "2026-10-15", "2026-10-14T23:30:00+00:00", [], 설정)
    _시세(c, "2026-10-15", 13_500, 13_500)
    _돌림(c, monkeypatch, "2026-10-15", "2026-10-16", "2026-10-15T23:30:00+00:00", [], 설정)
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (1, '2026-10-16', 9000, 9000, 9000, 9000, 1000, 'KRW', 't', 't')")  # fmt: skip
    s3 = dataclasses.replace(_신호(), stock_id=3, ticker="000660", name="종목3", horizon="long",
                             as_of_date="2026-10-16", close_date="2026-10-16")  # fmt: skip
    기록 = _돌림(c, monkeypatch, "2026-10-16", "2026-10-19", "2026-10-18T23:30:00+00:00", [s3], 설정)
    a = 기록["accounts"]["B"]
    assert "day_loss_basis" not in a
    assert c.q("SELECT kind, reason FROM paper_decisions WHERE decided_on='2026-10-16'")[0][0] == "blocked"


def test_하루_손실_경계는_넘게() -> None:
    """문서 "3% **넘게** 빠졌으면" — 정확히 −3.0% 는 막지 않는다(25.1632, 감사 [권고])."""
    def 문맥(r: float) -> rg.Context:
        return rg.Context(equity_krw=1e6, day_return_pct=r, cash_available_krw=1e6, holding_krw={}, sector_of={},
                          max_stock_pct=100, max_sector_pct=100, today="2026-10-21")  # fmt: skip

    후보 = [rg.Candidate(1, "buy", 1000.0, "2026-10-20")]
    assert rg.gate(후보, 문맥(rg.DAILY_LOSS_HALT_PCT))[1] == []
    assert rg.gate(후보, 문맥(rg.DAILY_LOSS_HALT_PCT - 0.01))[1][0][1][0][0] == "daily_loss"
