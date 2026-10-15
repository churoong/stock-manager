"""모의 투자 하루 단계 (docs/paper.md, docs/infra.md 25.1445) — 실제 마이그레이션을 올린 메모리 SQLite 에서.

주문 → 다음 장 시세로 체결 → 같은 날 다시 돌아도 두 번 주문·체결하지 않음 → 손절 매도 → 다음 장 시가 체결,
그리고 실제 `trades`·`positions`·`sell_flags` 는 그대로인지.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import report_picks

ROOT = Path(__file__).resolve().parent.parent


class _Res:
    def __init__(self, cur: sqlite3.Cursor) -> None:
        self.columns = [d[0] for d in cur.description] if cur.description else []
        self.rows = [tuple(r) for r in cur.fetchall()] if cur.description else []

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r, strict=True)) for r in self.rows]

    def scalar(self) -> Any:
        return self.rows[0][0] if self.rows else None


class Client:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.conn.executescript(path.read_text(encoding="utf-8"))
        for sid, ticker, country, cur in ((1, "005930", "KR", "KRW"), (2, "AAPL", "US", "USD"), (3, "000660", "KR", "KRW")):
            self.conn.execute(
                "INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source,"
                " fetched_at, asset_type) VALUES (?, ?, 'M', ?, ?, '전자', ?, 'active', 't', 't', 'stock')",
                [sid, ticker, country, f"종목{sid}", cur],
            )

    def execute(self, sql: str, args: list[Any] | None = None) -> _Res:
        return _Res(self.conn.execute(sql, args or []))

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list:
        for sql, args in statements:
            self.conn.execute(sql, args)
        return []

    def q(self, sql: str, args: list[Any] | None = None) -> list[tuple]:
        return self.conn.execute(sql, args or []).fetchall()


def _신호() -> report_picks.SignalRow:
    return report_picks.SignalRow(
        stock_id=1, ticker="005930", name="종목1", market="KOSPI", horizon="short", signal_type="단기", currency="KRW",
        buy_zone_low=9_600, buy_zone_high=10_000, suggested_weight_pct=10.0, suggested_amount=None, size_reduction=1.0,
        tranche_plan=[{"step": 1, "ratio": 0.4, "price": 10_000, "amount": None},
                      {"step": 2, "ratio": 0.3, "price": 9_800, "amount": None},
                      {"step": 3, "ratio": 0.3, "price": 9_600, "amount": None}],
        as_of_date="2026-10-14", total_score=72.0, sector="전자",
    )  # fmt: skip


설정 = {"max_weight_per_sector": 30.0, "max_weight_per_stock": 10.0, "min_order_amount": 0}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Client:
    c = Client()
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')"
    )
    # 사용자 실제 총액은 0 — 모의 계좌가 그것에 묶이지 않는지 (검증 [필수] 1)
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    return c


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, *, late: bool = False,
         signals: bool = True) -> tuple[list[str], dict]:  # fmt: skip
    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    return job.run_step(
        c, market="KR", trade_date=trade_date, late=late, signal_rows=[_신호()] if signals else [], settings=설정,
        price_series={}, series_unknown={}, allocate=signals, now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip


def _시세(c: Client, day: str, open_: float, close: float, volume: int = 1_000) -> None:
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (1, ?, ?, ?, ?, ?, ?, 'KRW', 't', 't')",
        [day, open_, max(open_, close), min(open_, close), close, volume],
    )


def test_order_fill_rerun_and_stop_loss(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    c = client
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    주문 = c.q("SELECT account_id, tranche, limit_price, quantity, fill_session FROM paper_orders ORDER BY 1, 2")
    # 평가 총액 100만 × 비중 10% = 10만 → 40/30/30 → 4주 · 3주 · 3주 (정수 주)
    assert 주문 == [("A", 1, 10_000, 4, "2026-10-15"), ("A", 2, 9_800, 3, "2026-10-15"), ("A", 3, 9_600, 3, "2026-10-15"),
                  ("B", 1, 10_000, 4, "2026-10-15"), ("B", 2, 9_800, 3, "2026-10-15"),
                  ("B", 3, 9_600, 3, "2026-10-15")]  # fmt: skip
    assert "모의 투자 A: 평가 1,000,000원" in 줄[0]
    assert 기록["accounts"]["A"]["orders"] == 3

    # 다음 장: 종가 9,900 — 1회차(10,000)만 찬다. 장중 저가로 닿았을 2·3회차는 놓친다(보수)
    _시세(c, "2026-10-15", 10_100, 9_900)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    체결 = c.q("SELECT account_id, quantity, price, cash_krw FROM paper_fills ORDER BY 1")
    assert [(a, q, p) for a, q, p, _c in 체결] == [("A", 4, 10_000), ("B", 4, 10_000)]
    assert 체결[0][3] == -round(40_000 * 1.00015)  # 수수료만, 지정가는 슬리피지 없음
    상태 = c.q("SELECT status, COUNT(*) FROM paper_orders GROUP BY status ORDER BY 1")
    assert 상태 == [("expired", 4), ("filled", 2)]
    일 = c.q("SELECT account_id, invested_krw, cash_krw, recon_ok FROM paper_daily WHERE as_of = '2026-10-16'")
    assert sorted(일) == [("A", 1_000_000, 1_000_000 + 체결[0][3], 1), ("B", 1_000_000, 1_000_000 + 체결[1][3], 1)]

    # 같은 날 다시 — 체결·주문이 두 번 생기지 않는다 (docs/paper.md 2.4)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert c.q("SELECT COUNT(*) FROM paper_fills") == [(2,)]

    # 9,000 으로 −10% — 단기 손절(−7%) → A·B 둘 다 판다(손절은 B 의 규칙에도 있다). 다음 장 시가 시장가
    _시세(c, "2026-10-16", 9_100, 9_000)
    _, 기록 = _하루(c, monkeypatch, "2026-10-16", signals=False)
    매도 = c.q("SELECT account_id, quantity, reason, fill_basis FROM paper_orders WHERE side = 'sell' ORDER BY 1")
    assert 매도 == [("A", 4, "손절", "open"), ("B", 4, "손절", "open")]
    _시세(c, "2026-10-19", 8_800, 9_200)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    판것 = c.q("SELECT account_id, price, slippage FROM paper_fills WHERE side = 'sell' ORDER BY 1")
    assert [(a, p) for a, p, _s in 판것] == [("A", 8_800), ("B", 8_800)]
    assert 판것[0][2] == pytest.approx(8_800 * 4 * 0.001)  # 시장가 매도는 슬리피지

    # 실제 장부는 그대로
    for 표 in ("trades", "positions", "sell_flags"):
        assert c.q(f"SELECT COUNT(*) FROM {표}") == [(0,)]  # noqa: S608 — 고정된 표 이름


def test_halted_day_does_not_fill(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    c = client
    _하루(c, monkeypatch, "2026-10-14")
    # 정지: 거래량 0, 종가는 전일 값 그대로 — 지정가 매수가 거짓으로 차면 안 된다 (검증 [필수] 5)
    _시세(c, "2026-10-15", 0, 9_500, volume=0)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert c.q("SELECT COUNT(*) FROM paper_fills") == [(0,)]
    assert c.q("SELECT DISTINCT status FROM paper_orders") == [("expired",)]


def test_no_deposit_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client()
    줄, 기록 = job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=[_신호()], settings=설정,
                          price_series={}, series_unknown={}, allocate=True, now=datetime(2026, 10, 15, tzinfo=UTC))  # fmt: skip
    assert 줄 == [] and 기록 == {"skipped": "입금 없음"}
    assert c.q("SELECT COUNT(*) FROM paper_orders") == [(0,)]


def test_rescale_ignores_user_total() -> None:
    """신호 금액은 사용자 총액 × 비중이다 — 모의는 제 평가 총액으로 다시 만든다 (검증 [필수] 1)."""
    r = job.rescale_rows([_신호()], 2_000_000, 0)[0]
    assert r.suggested_amount == 200_000
    assert [t["amount"] for t in r.tranche_plan] == [80_000, 60_000, 60_000]
    assert job.rescale_rows([_신호()], 2_000_000, 300_000)[0].suggested_amount is None


def test_late_run_sells_at_close_and_frozen_stock_stops(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    c = client
    _하루(c, monkeypatch, "2026-10-14")
    _하루(c, monkeypatch, "2026-10-14")  # 같은 날 다시 — 주문이 두 배가 되지 않는다
    assert c.q("SELECT COUNT(*) FROM paper_orders") == [(6,)]
    _시세(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    _시세(c, "2026-10-16", 9_100, 9_000)
    # 장 열린 뒤 만든 리포트 — 매도는 시가가 아니라 종가로 (검증 [필수] 4)
    _하루(c, monkeypatch, "2026-10-16", late=True, signals=False)
    assert c.q("SELECT DISTINCT fill_basis FROM paper_orders WHERE side = 'sell'") == [("close",)]
    # 그 뒤 분할 의심(기업행위 경계) — 매도 주문은 취소, 평가는 원가로 (검증 [필수] 6)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: {1})
    _시세(c, "2026-10-19", 2_200, 2_300)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-19", signals=False)
    assert c.q("SELECT COUNT(*) FROM paper_fills WHERE side = 'sell'") == [(0,)]
    assert c.q("SELECT DISTINCT close_note FROM paper_orders WHERE side = 'sell'") == [
        ("기업행위 경계 — 멈춤 (docs/paper.md 2.3)",)]
    평가 = c.q("SELECT holdings_krw, unknown_json FROM paper_daily WHERE account_id = 'A' AND as_of = '2026-10-20'")
    assert 평가[0][0] == round(40_000 * 1.00015) and "기업행위" in 평가[0][1]
    assert any("멈춘 종목 1개" in 글 for 글 in 줄)


def test_rerun_with_other_picks_does_not_add_orders(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """먼저 쓴 쪽이 이긴다 — 재실행이 다른 종목을 고르더라도 그 계좌·시장·거래일 주문을 더 내지 않는다 (docs/paper.md 2.4)."""
    c = client
    _하루(c, monkeypatch, "2026-10-14")
    다른 = report_picks.SignalRow(**{**_신호().__dict__, "stock_id": 3, "ticker": "000660"})
    다음 = cal.next_session("KR", date(2026, 10, 14))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=[다른], settings=설정, price_series={},
                 series_unknown={}, allocate=True, now=datetime(2026, 10, 15, 1, tzinfo=UTC))  # fmt: skip
    assert c.q("SELECT DISTINCT stock_id FROM paper_orders") == [(1,)]


def test_market_day_not_collected_keeps_orders_open(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """그 시장 시세를 아직 못 받은 날은 정지가 아니다 — 매수를 취소하지 않고 기다린다 (docs/paper.md 2.3)."""
    c = client
    _하루(c, monkeypatch, "2026-10-14")
    _하루(c, monkeypatch, "2026-10-15", signals=False)  # 10-15 시세가 하나도 없다
    assert c.q("SELECT DISTINCT status FROM paper_orders") == [("open",)]


def test_paper_holdings_join_news_targets(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """모의 보유도 뉴스를 모은다 — 감성급락 판정이 수집 공백에 오염되지 않게 (docs/paper.md 7장, 검증 [필수] 8)."""
    from batch.jobs import monitor_targets

    c = client
    _하루(c, monkeypatch, "2026-10-14")
    assert all(r[3] != "paper" for r in monitor_targets.news_target_rows(c, "KR", [], "t"))  # type: ignore[arg-type]
    _시세(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    후보 = monitor_targets.news_target_rows(c, "KR", [], "t")  # type: ignore[arg-type]
    assert [r for r in 후보 if r[3] == "paper"] == [("KR", 1, None, "paper", "t")]  # A·B 둘 다 들어도 한 번
    assert monitor_targets.news_target_rows(c, "US", [], "t") == []  # type: ignore[arg-type]
