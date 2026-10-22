"""리포트 발송이 실패해도 KIS모의투자 베끼기·운용 요약은 돈다 (docs/infra.md 25.1715, 일일 배치 감사 [필수] 1)."""

from __future__ import annotations

import pytest

from batch.jobs import broker_mirror, daily, desk, paper
from tests.test_report_store_fail_1180 import _준비


def _kis_desk(monkeypatch: pytest.MonkeyPatch, 가상기록: dict | None = None) -> dict[str, list]:
    부름: dict[str, list] = {"paper": [], "kis": [], "hold": [], "desk": [], "finish": []}

    def 가상(client, market, warnings, **kw):  # noqa: ANN001, ANN003, ANN202
        부름["paper"].append(kw.get("trade_date"))
        return ["🧪 가상모의투자 H: 주문 3건"], 가상기록 or {"accounts": {"H": {"orders": 3}}, "frozen": [],
                                                       "frozen_by_account": {"H": []}}  # fmt: skip

    def kis(*_a, **k):  # noqa: ANN002, ANN003, ANN202
        부름["kis"].append(k.get("slot"))
        부름["hold"].append(k.get("hold_send"))
        return {"sent": {}}

    monkeypatch.setattr(paper, "run_step_locked", 가상)
    monkeypatch.setattr(broker_mirror, "run", kis)
    monkeypatch.setattr(desk, "write", lambda *a, **k: 부름["desk"].append(1) or {})
    monkeypatch.setattr(desk, "report_head", lambda *a, **k: [])
    monkeypatch.setattr(broker_mirror, "challenger_account", lambda c: "C")
    monkeypatch.setattr(daily, "_notify_failure", lambda *a, **k: True)
    monkeypatch.setattr(daily.db, "finish_batch_run", lambda c, rid, **k: 부름["finish"].append(k.get("status")))
    return 부름


def test_발송_실패여도_KIS_와_운용요약이_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    _준비(monkeypatch, 0)
    부름 = _kis_desk(monkeypatch)

    def 실패(text, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        from batch.notify import telegram

        raise telegram.TelegramError("429 retry_after 3600")

    monkeypatch.setattr(daily.telegram, "send", 실패)
    assert daily.run("KR") == 1
    assert 부름["paper"] == ["2026-10-08"] and len(부름["kis"]) == 2 and 부름["desk"] == [1]
    assert 부름["finish"] == ["failed"]


def test_발송_직전_DB_오류여도_KIS_와_운용요약이_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    _준비(monkeypatch, 0)
    부름 = _kis_desk(monkeypatch)
    n = [0]

    def sent_exists(*a, **k):  # noqa: ANN002, ANN003, ANN202
        n[0] += 1
        if n[0] >= 2:
            raise RuntimeError("Hrana: stream expired")
        return False

    monkeypatch.setattr(daily.reports, "sent_exists", sent_exists)
    monkeypatch.setattr(daily.db, "fail_open_runs", lambda t: None)
    with pytest.raises(RuntimeError, match="stream expired"):
        daily.run("KR")
    assert 부름["paper"] and len(부름["kis"]) == 2 and 부름["desk"] == [1]


def test_정상_발송이면_한_번만(monkeypatch: pytest.MonkeyPatch) -> None:
    _준비(monkeypatch, 0)
    부름 = _kis_desk(monkeypatch)
    assert daily.run("KR") == 0
    assert len(부름["kis"]) == 2 and 부름["desk"] == [1] and 부름["hold"][0] is None


def test_가상_단계가_잠금으로_건너뛰면_KIS_는_내지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """일일 배치 감사 [필수] 2 ② — 그날 체결 판정이 안 돌아 장부가 KIS 보다 뒤처졌다. 맞추기·잔고만, 주문·정리 없음"""
    _준비(monkeypatch, 0)
    부름 = _kis_desk(monkeypatch, {"skipped": "다른 시장의 가상모의투자 단계가 도는 중(잠금)"})
    assert daily.run("KR") == 0
    assert 부름["hold"][0] == "가상모의투자 단계를 건너뜀(잠금) — 오늘 베끼지 않음"


def test_dry_run_이면_KIS_를_돌리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """가상 단계가 안 도는 dry-run 에서 맞추기 뒤 정리가 가상 장부와 무관하게 나갈 수 있었다(25.1715)"""
    _준비(monkeypatch, 0)
    부름 = _kis_desk(monkeypatch)
    daily.run("KR", dry_run=True)
    assert 부름["kis"] == []


def test_시장_전체가_빠진_장의_매도는_오늘_다음_장으로(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1715 (평가 감사 [권고] 2) — 10-15 국내 시세가 끝내 없고 10-16 은 받았다. 10-19 실행(trade_date 10-16)이
    10-15 매도를 미루면 10-19 로 — 예전 `next_session(10-15)`=10-16 이면 오늘보다 앞서 KIS 베끼기가 내지 못했고 이미
    지난 10-16 시가로 팔았다"""
    from datetime import date, datetime

    from batch.core import calendar as cal
    from tests.test_paper_job_1445 import Client, 설정

    c = Client(accounts=("A",))
    monkeypatch.setattr(paper, "frozen_ids", lambda *_a, **_k: set())
    x = c.conn.execute
    x("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
      " VALUES ('2026-10-13', 1000000, 'deposit', '2026-10-13T00:00:00+00:00', 'k1')")
    x("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
      " decided_on, fill_session, fill_basis, tranche, reason, rule_version, created_at, status) VALUES (1, 'A', 'KR', 1,"
      " 'buy', 'limit', 10000, 10, 'KRW', '2026-10-13', '2026-10-14', 'open', 1, 't', 1, 't', 'filled')")
    x("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
      " fee, tax, slippage, cash_krw, created_at) VALUES (1, 'A', 1, 'buy', '2026-10-14', 10000, 10, 'KRW', 1, 0, 0,"
      " 0, -100000, '2026-10-14T23:40:00+00:00')")
    x("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
      " decided_on, fill_session, fill_basis, tranche, reason, rule_version, created_at) VALUES (2, 'A', 'KR', 1,"
      " 'sell', 'market', NULL, 10, 'KRW', '2026-10-14', '2026-10-15', 'open', 0, '손절', 1, 't')")
    for d, v in (("2026-10-14", 10000), ("2026-10-16", 8000)):
        x("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
          " VALUES (1, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [d, v, v, v, v])
    monkeypatch.setattr(cal, "local_today", lambda m=None: date(2026, 10, 19))
    paper.run_step(c, market="KR", trade_date="2026-10-16", late=False, signal_rows=[], settings=설정,
                   price_series={}, series_unknown={}, allocate=False,
                   now=datetime.fromisoformat("2026-10-18T23:40:00+00:00"))  # fmt: skip
    assert c.q("SELECT status, fill_session FROM paper_orders WHERE id = 2") == [("open", "2026-10-19")]
