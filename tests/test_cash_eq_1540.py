"""관문 지수 대비의 현금 환산 참고 칸 (docs/desk.md 6장 "현금 환산", docs/factors.md 12.34 31회차 P3,
docs/infra.md 25.1540) — 손으로 낸 값. 통과 판정에는 쓰지 않는다."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.jobs import desk as job
from batch.services import desk
from tests.test_paper_job_1445 import Client

날 = ["2026-01-01", "2026-01-02", "2026-01-03"]
#: 시간가중 지수 +10% 뒤 제자리, KOSPI 그림자 +5% 뒤 제자리(입금 없음)
TWR = [(날[0], 1.0), (날[1], 1.1), (날[2], 1.1)]
지수 = [(날[0], 1000.0, 2000.0, 1000.0), (날[1], 1050.0, 2000.0, 1000.0), (날[2], 1050.0, 2000.0, 1000.0)]
#: 첫날 현금 절반, 둘째 날 0
현금 = [(날[0], 500.0, 1000.0), (날[1], 0.0, 1100.0), (날[2], 0.0, 1100.0)]


def test_손계산() -> None:
    # 1단계: 0.10 + 0.5 × 0.05 = 0.125, 2단계: 0 + 0 × 0 = 0 → +12.5%, 평균 현금 25%
    pct, 비중 = desk.cash_equivalent_return(TWR, 현금, 지수, 날[0], ["KOSPI"])
    assert pct == pytest.approx(12.5) and 비중 == pytest.approx(25.0)
    # 두 지수 평균 — S&P 그림자는 제자리라 0.05 / 2
    pct2, _ = desk.cash_equivalent_return(TWR, 현금, 지수, 날[0], ["KOSPI", "S&P500"])
    assert pct2 == pytest.approx((1 + 0.10 + 0.5 * 0.025) * 1.0 * 100 - 100)


def test_입금은_그날_지수로_산_몫이라_빠진다() -> None:
    # 둘째 날 입금 1000 — 그림자도 1000 늘었다. 보정 안 하면 r_idx 가 +105%
    입금 = [(날[0], 1000.0, 2000.0, 1000.0), (날[1], 2050.0, 2000.0, 2000.0)]
    pct, _ = desk.cash_equivalent_return(TWR[:2], 현금[:2], 입금, 날[0], ["KOSPI"])
    assert pct == pytest.approx(12.5)


def test_모르면_None() -> None:
    assert desk.cash_equivalent_return(TWR, 현금[1:], 지수, 날[0], ["KOSPI"]) == (None, None)  # 첫날 현금 없음
    assert desk.cash_equivalent_return(TWR, 현금, 지수[:2], 날[0], ["KOSPI"]) == (None, None)  # 그림자 빈 날
    assert desk.cash_equivalent_return(TWR, 현금, 지수, 날[0], []) == (None, None)


def test_관문_통과에는_쓰지_않는다() -> None:
    from datetime import date, timedelta

    days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(130)]
    h = [(d, 1 + 0.001 * i) for i, d in enumerate(days)]  # 약 +12.9%
    r = [(d, 1.0) for d in days]
    # 지수 +12% — 원래 지수 대비는 +0.9%p(통과), 현금 90% 가 지수를 냈다면 크게 앞서고 부호는 같다.
    # 지수 +14% 면 원래는 탈락, 환산은 통과 — 통과 칸은 원래 값만 본다
    for 끝, ok in ((1120.0, True), (1140.0, False)):
        idx = [(d, 1000.0 + (끝 - 1000.0) * i / 129, None, 1000.0) for i, d in enumerate(days)]
        cash = [(d, 900.0, 1000.0) for d in days]
        g = desk.gate({"H": h, "R1": r, "R2": r}, "H", recon_bad_days=0, batch_failed=0, kis=None, index=idx, cash=cash)
        칸 = {i["key"]: i for i in g["items"]}["vs_index"]
        assert 칸["ok"] is ok and 칸["cash_eq"]["avg_cash_pct"] == pytest.approx(90.0)
        assert 칸["cash_eq"]["vs_index"] > 칸["now"] > -5


def test_운용_요약에_실린다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for as_of, twr, kospi, cash in (("2026-10-15", 1.0, 1000.0, 400), ("2026-10-16", 1.02, 1010.0, 400)):
        c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
                       " twr_index, positions_json, recon_ok, updated_at, kospi_krw) VALUES ('B', ?, 1000, ?, 600, 1000, 0, ?,"
                       " '[]', 1, 't', ?)", [as_of, cash, twr, kospi])  # fmt: skip
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    eq = {i["key"]: i for i in s["gate"]["items"]}["vs_index"]["cash_eq"]
    # +2% + 0.4 × 1% = +2.4%, 지수 +1% → +1.4%p, 현금 40%
    assert eq["return_pct"] == pytest.approx(2.4) and eq["vs_index"] == pytest.approx(1.4)
    assert eq["avg_cash_pct"] == pytest.approx(40.0)
