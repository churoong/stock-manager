"""그날 시세가 시장엔 있는데 한 종목 행만 빠졌을 때 — 바로 정지로 보지 않고 기다린다 (docs/paper.md 2.3,
docs/infra.md 25.1713, 체결 판정 감사 [필수])."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _시세, _신호, _하루, client, 설정  # noqa: F401 — 픽스처


def _시세3(c: Client, day: str, open_: float, close: float, volume: int = 1_000) -> None:
    c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                   " VALUES (3, ?, ?, ?, ?, ?, ?, 'KRW', 't', 't')",
                   [day, open_, max(open_, close), min(open_, close), close, volume])  # fmt: skip


def _3번_주문(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    신호3 = report_picks.SignalRow(**{**_신호().__dict__, "stock_id": 3, "ticker": "000660"})
    monkeypatch.setattr(cal, "local_today", lambda _m=None: cal.next_session("KR", date(2026, 10, 14)))
    job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=[신호3], settings=설정,
                 price_series={}, series_unknown={}, allocate=True,
                 now=datetime.fromisoformat("2026-10-15T00:30:00+00:00"))  # fmt: skip


def test_빠진_행은_기다렸다가_메워지면_그_행으로_판정(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """10-15 시장(1번)은 받았는데 3번 행만 빠졌다 — 예전엔 회차가 모두 "정지" 로 만료. 다음 날 메워진 행(종가 9,500)
    으로 세 회차가 찬다"""
    c = client
    _3번_주문(c, monkeypatch)
    _시세(c, "2026-10-15", 10_100, 9_900)
    _, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert c.q("SELECT DISTINCT status FROM paper_orders WHERE stock_id = 3") == [("open",)]
    assert 기록["waiting_bar"] >= 1
    _시세3(c, "2026-10-15", 10_000, 9_500)
    _시세(c, "2026-10-16", 9_900, 9_900)
    _시세3(c, "2026-10-16", 9_500, 9_500)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT COUNT(*) FROM paper_fills WHERE stock_id = 3 AND fill_date = '2026-10-15'")[0][0] > 0


def test_더_뒤_행이_생기면_그날은_정지로(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """10-15 행은 끝내 없고 10-16 행이 들어왔다(거래 재개) — 그날은 정지로 판정(예전과 같은 결과)"""
    c = client
    _3번_주문(c, monkeypatch)
    _시세(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    _시세(c, "2026-10-16", 9_900, 9_900)
    _시세3(c, "2026-10-16", 9_500, 9_500)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    상태 = c.q("SELECT DISTINCT status, close_note FROM paper_orders WHERE stock_id = 3 AND fill_session = '2026-10-15'")
    assert 상태 and all(s == "expired" and "정지" in n for s, n in 상태)


def test_기다릴_날이_지나면_정지로(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """3번 행이 그 뒤로도 하나도 안 들어온다 — 10-15 에서 두 장 뒤(10-19) 실행부터는 정지로 본다"""
    c = client
    _3번_주문(c, monkeypatch)
    for d in ("2026-10-15", "2026-10-16"):
        _시세(c, d, 10_000, 10_000)
        _하루(c, monkeypatch, d, signals=False)
    assert c.q("SELECT DISTINCT status FROM paper_orders WHERE stock_id = 3 AND fill_session = '2026-10-15'") == [
        ("open",)]
    _시세(c, "2026-10-19", 10_000, 10_000)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    assert c.q("SELECT DISTINCT status FROM paper_orders WHERE stock_id = 3 AND fill_session = '2026-10-15'") == [
        ("expired",)]


def _손실행(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, today: str, *, signals: bool = True) -> None:
    monkeypatch.setattr(cal, "local_today", lambda _m=None: date.fromisoformat(today))
    job.run_step(c, market="KR", trade_date=trade_date, late=True, signal_rows=[_신호()] if signals else [],
                 settings=설정, price_series={}, series_unknown={}, allocate=signals,
                 now=datetime.fromisoformat(f"{today}T08:00:00+00:00"), after_close=True)  # fmt: skip


def test_장_끝난_뒤_실행은_지나간_장의_회차를_거두지_않는다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """25.1713 (체결 판정 감사 [권고]) — 10-16 장 끝난 뒤 손 실행이 10-19 회차를 냈고, 10-19 아침 단계는 건너뜀. 10-19 장
    끝난 뒤 손 실행(trade_date 10-16)에서 손절이 서면 이미 끝난 10-19 장의 회차는 그대로 둔다(소급 취소 금지, 25.1592).
    예전 `fill_session > trade_date` 는 그 회차를 "아직 오지 않은 장" 으로 거뒀다"""
    c = client
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    _손실행(c, monkeypatch, "2026-10-15", "2026-10-16")
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE side = 'buy' AND fill_session = '2026-10-19'")[0][0] > 0
    _시세(c, "2026-10-16", 9_100, 9_000)  # 10-16 종가 −10% → 손절
    _손실행(c, monkeypatch, "2026-10-16", "2026-10-19", signals=False)
    assert c.q("SELECT DISTINCT status FROM paper_orders WHERE side = 'buy' AND fill_session = '2026-10-19'") == [
        ("open",)]


def test_장_열린_뒤_실행은_진행_중인_장의_회차를_거두지_않는다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """장 열린 뒤(종가 기준) 실행 — 진행 중인 10-19 장의 회차는 KIS 에도 이미 나갔다. 그 뒤 장만 거둔다"""
    c = client
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    _손실행(c, monkeypatch, "2026-10-15", "2026-10-16")
    _시세(c, "2026-10-16", 9_100, 9_000)
    _하루(c, monkeypatch, "2026-10-16", late=True, signals=False)  # 10-19 장중(종가 기준) 실행
    assert c.q("SELECT DISTINCT status FROM paper_orders WHERE side = 'buy' AND fill_session = '2026-10-19'") == [
        ("open",)]


def test_종가_0_행은_정지() -> None:
    from batch.services import paper as pp

    assert pp.halted(pp.Bar("2026-10-15", None, 0.0, None))
    assert not pp.halted(pp.Bar("2026-10-15", None, 100.0, None))
