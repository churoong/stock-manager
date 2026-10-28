"""가상모의투자 스윙 계좌 S — 엔진 연결 (docs/paper.md 16.3.1, docs/infra.md 25.1871).

10월엔 계좌 행이 없다(마이그레이션은 11월). 시험이 행을 직접 넣어 본다: 단기 신호만·든 종목은 안 삼, `swing_close_v1`
(종가가 진입 시가 대비 −3%·+5%·10거래일)로 장 전 시장가 매도, 그리고 **S 가 있어도 다른 계좌의 행은 한 글자도 같다**.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import shadow as sh
from tests.test_paper_job_1445 import Client, _시세, _신호, 설정


def _계좌(accounts: tuple[str, ...], s: bool) -> Client:
    c = Client(accounts=accounts)
    if s:
        c.conn.execute(
            "INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)"
            " VALUES ('S', '가상 S (스윙)', 'swing_close', 1, '2026-10-14T00:00:00+00:00', 'open', 10, 'short')"
        )
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')"
    )
    return c


@pytest.fixture(autouse=True)
def _고정(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())


def _중기() -> report_picks.SignalRow:
    return dataclasses.replace(_신호(), stock_id=3, ticker="000660", name="종목3", horizon="mid", signal_type="중기")


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, signals: bool = True) -> dict:
    from datetime import datetime

    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    _줄, 기록 = job.run_step(
        c, market="KR", trade_date=trade_date, late=False, signal_rows=[_신호(), _중기()] if signals else [],
        settings=설정, price_series={}, series_unknown={}, allocate=signals,
        now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip
    return 기록


def _시세3(c: Client, day: str, close: float) -> None:
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (3, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')",
        [day, close, close, close, close],
    )


def _날들(c: Client, monkeypatch: pytest.MonkeyPatch, 종가2: float) -> None:
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_000, 10_000)
    _시세3(c, "2026-10-15", 10_000)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    _시세(c, "2026-10-16", 10_050, 종가2)
    _시세3(c, "2026-10-16", 10_000)
    _하루(c, monkeypatch, "2026-10-16", signals=False)


def test_S_는_단기_신호만_시가_시장가로_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), s=True)
    _하루(c, monkeypatch, "2026-10-14")
    주문 = c.q("SELECT stock_id, side, order_type, tranche FROM paper_orders WHERE account_id = 'S'")
    assert 주문 == [(1, "buy", "market", 1)]  # 중기 신호(종목 3)는 사지 않는다, 한 번에 시가 시장가
    assert {r[0] for r in c.q("SELECT stock_id FROM paper_orders WHERE account_id = 'B'")} == {1, 3}


def test_S_는_종가가_손절선_이하면_다음_장_시가에_판다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), s=True)
    _날들(c, monkeypatch, 9_700)  # 진입 시가 10,000 → 10-16 종가 9,700 = −3% (등호 포함)
    assert c.q("SELECT price FROM paper_fills WHERE account_id = 'S' AND side = 'buy'") == [(10_000,)]
    매도 = c.q("SELECT stock_id, reason, fill_session, rationale_json FROM paper_orders"
               " WHERE account_id = 'S' AND side = 'sell'")  # fmt: skip
    assert [(s, r, f) for s, r, f, _j in 매도] == [(1, "스윙손절", "2026-10-19")]
    근거 = json.loads(매도[0][3])
    assert 근거["sell_rule"] == "swing_close" and "swing_close_v1" in 근거["flags"][0]["rationale"]
    # 결정 원장에 판 것으로 — 같은 까닭 코드
    assert c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'S' AND kind = 'sell'") == [
        ("sell", "스윙손절")]
    # B(core, 단기 손절 −7%)는 들고 간다 — 엔진 플래그와 갈리는 자리
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B' AND side = 'sell'") == [(0,)]


def test_S_는_선_안이면_들고_가고_든_종목은_더_사지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), s=True)
    _날들(c, monkeypatch, 9_701)
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'S' AND side = 'sell'") == [(0,)]
    # 입금을 더해 목표 비중이 커져도(다른 계좌는 목표까지 더 산다) 같은 단기 신호가 다시 와도 든 종목이라 사지 않는다
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-16', 1000000, 'deposit', '2026-10-16T00:00:00+00:00', 'k2')"
    )
    _하루(c, monkeypatch, "2026-10-19")
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'S' AND side = 'buy'") == [(1,)]


def test_S_가_있어도_다른_계좌의_행은_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.3.1 '기존 계좌 무변화 장치 ④' — S 는 id 순서 맨 뒤라 앞 계좌가 보는 값이 바뀌지 않는다"""
    표들 = ("paper_orders", "paper_fills", "paper_decisions", "paper_daily")
    결과 = []
    for s in (False, True):
        c = _계좌(("A", "B", "C", "D", "E", "G", "H", "R1", "R2"), s=s)
        _날들(c, monkeypatch, 9_700)
        # 행 번호(id)는 S 행이 끼어 밀린다 — 번호를 뺀 내용으로 견준다
        결과.append({t: sorted(r[1:] for r in c.q(f"SELECT * FROM {t} WHERE account_id != 'S'")) for t in 표들})
        if s:
            assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'S'")[0][0] >= 2
    for t in 표들:
        assert 결과[0][t] == 결과[1][t], t


def test_판정은_그림자와_같은_함수() -> None:
    """엔진 판정(`_스윙넘음`)이 `shadow.close_exit` 를 그대로 쓴다 — 그림자 집계와 판정이 갈리지 않게"""
    class C:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            class R:
                rows = [("2026-10-15", 10_000.0), ("2026-10-16", 10_500.0)]
            assert sql == job.SWING_CLOSES_SQL and args[-1] == sh.SWING_CLOSE.max_sessions
            return R()

    행 = {"stock_id": 1, "lots": [("2026-10-15", 4.0, 10_000.0)]}
    팖 = job._스윙넘음(C(), 행, date(2026, 10, 19))
    assert 팖 is not None and 팖["reason_code"] == pp.SWING_CODES["target"] == "스윙목표"
    assert job._스윙넘음(C(), {"stock_id": 1, "lots": []}, date(2026, 10, 19)) is None


def test_스윙_사유_코드는_손절과_이름이_다르다() -> None:
    # `손절` 이면 "손절 보유의 남은 회차 거둠" 길을 탄다(paper.md 16.3.1)
    assert pp.SELL_RULES[pp.SWING_RULE] == frozenset(pp.SWING_CODES.values())
    assert not pp.SELL_RULES[pp.SWING_RULE] & {"손절", "목표도달", "기간초과"}


def test_모르는_매도_규칙의_계좌는_그_계좌만_건너뛴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.3.1 '코드가 먼저, 행은 나중' — 순서가 뒤집혀도 다른 계좌(H 와 그 뒤 KIS 베끼기)는 돈다"""
    c = _계좌(("B",), s=False)
    c.conn.execute(
        "INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)"
        " VALUES ('Z', '모름', 'zzz', 1, '2026-10-14T00:00:00+00:00', 'open', 10, 'short')"
    )
    기록 = _하루(c, monkeypatch, "2026-10-14")
    assert 기록["accounts"]["Z"] == {"error": "모르는 매도 규칙 zzz"}
    assert 기록["accounts"]["B"]["orders"] >= 1
