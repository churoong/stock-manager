"""모의 투자 하루 단계 (docs/paper.md, docs/infra.md 25.1445) — 실제 마이그레이션을 올린 메모리 SQLite 에서.

주문 → 다음 장 시세로 체결 → 같은 날 다시 돌아도 두 번 주문·체결하지 않음 → 손절 매도 → 다음 장 시가 체결,
그리고 실제 `trades`·`positions`·`sell_flags` 는 그대로인지.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
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
    def __init__(self, accounts: tuple[str, ...] | None = ("A", "B"), charters: bool = False) -> None:
        self.conn = sqlite3.connect(":memory:")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.conn.executescript(path.read_text(encoding="utf-8"))
        if not charters:
            # 판 1 규칙을 보는 시험 — 0077 헌장(판 2 투자사, docs/company.md) 전의 계좌로 되돌린다
            for aid, rule in (("A", "all"), ("C", "core"), ("D", "time"), ("E", "core")):
                self.conn.execute("UPDATE paper_accounts SET charter_json = NULL, charter_since = NULL, rule_version = 1,"
                                  " sell_rule = ? WHERE id = ?", [rule, aid])  # fmt: skip
        if accounts is not None:
            # 이 파일의 시험은 A·B 두 계좌의 규칙을 본다 — 7계좌(25.1458)는 tests/test_paper_accounts_1458.py
            self.conn.execute(f"DELETE FROM paper_accounts WHERE id NOT IN ({','.join('?' * len(accounts))})",
                              list(accounts))  # fmt: skip
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
        as_of_date="2026-10-14", total_score=72.0, sector="전자", close=10_000, close_date="2026-10-14",
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
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
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
    assert "가상모의투자 A: 평가 1,000,000원" in 줄[0]
    assert 기록["accounts"]["A"]["orders"] == 3

    # 다음 장: 종가 9,900 — 1회차(10,000)만 찬다. 장중 저가로 닿았을 2·3회차는 놓친다(보수)
    _시세(c, "2026-10-15", 10_100, 9_900)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    체결 = c.q("SELECT account_id, quantity, price, cash_krw FROM paper_fills ORDER BY 1")
    assert [(a, q, p) for a, q, p, _c in 체결] == [("A", 4, 10_000), ("B", 4, 10_000)]
    assert 체결[0][3] == -round(40_000 * 1.00015)  # 수수료만, 지정가는 슬리피지 없음
    # 체결일 종가를 함께 적는다 — 진입 이득(25.1450). 성과 분해도 오늘 평가 행에
    assert c.q("SELECT DISTINCT day_close FROM paper_fills") == [(9_900,)]
    assert c.q("SELECT COUNT(*) FROM paper_daily WHERE as_of = '2026-10-16' AND attribution_json IS NOT NULL") == [(2,)]
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
    # 화면의 거래내역·평가손익 (25.1455) — 매도의 실현손익 = 현금 영향 − 평균 원가, 계좌 합은 평가 행에
    실현, 현금, 매수원가 = c.q(
        "SELECT s.realized_krw, s.cash_krw, -b.cash_krw FROM paper_fills s JOIN paper_fills b"
        " ON b.account_id = s.account_id AND b.side = 'buy' WHERE s.side = 'sell' AND s.account_id = 'A'")[0]
    assert 실현 == round(현금 - 매수원가) and 실현 < 0
    assert c.q("SELECT realized_krw, unrealized_krw FROM paper_daily WHERE account_id = 'A' ORDER BY as_of DESC LIMIT 1")[0] == (
        실현, 0)
    assert c.q("SELECT realized_krw FROM paper_fills WHERE side = 'buy' AND account_id = 'A'") == [(None,)]
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
    assert any("멈춘 종목 — 기업행위 경계 1개(모든 계좌)" in 글 for 글 in 줄)  # 25.1656 — 경계·이어 멈춤을 나눠 셈


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


def test_late_deposit_after_last_run_is_a_flow_not_a_gain(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """그날 마지막 실행 뒤 넣은 입금은 흐름이다 — 시간가중이 +100% 로 뛰면 안 된다 (25.1447, 교차검증 [필수] 1)."""
    c = client
    _하루(c, monkeypatch, "2026-10-14")  # 10-15 한국 날짜로 첫 기록(지수 1)
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-15', 1000000, 'deposit', '2026-10-15T14:00:00.000Z', 'k2')"  # 같은 한국 날짜, 실행 뒤
    )
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    지수, 검산 = c.q("SELECT twr_index, recon_ok FROM paper_daily WHERE account_id = 'A' AND as_of = '2026-10-16'")[0]
    assert 지수 == pytest.approx(1.0) and 검산 == 1


def test_manual_run_after_close_orders_for_the_next_session(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """장이 끝난 뒤의 손 실행은 오늘 장 값이 모두 주문보다 앞선다 — 다음 장으로 (25.1447, 교차검증 [필수] 3)."""
    c = client
    다음 = cal.next_session("KR", date(2026, 10, 14))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    job.run_step(c, market="KR", trade_date="2026-10-14", late=True, signal_rows=[_신호()], settings=설정,
                 price_series={}, series_unknown={}, allocate=True, now=datetime(2026, 10, 15, 8, tzinfo=UTC),
                 after_close=True)  # fmt: skip
    assert c.q("SELECT DISTINCT fill_session, fill_basis FROM paper_orders") == [("2026-10-16", "open")]


def test_session_closed_helper() -> None:
    from batch.jobs import daily

    assert daily._장_끝남("KR", "2020-01-02") is True
    assert daily._장_끝남("KR", "2099-01-02") is False


def test_session_open_helper_and_paper_late_uses_now() -> None:
    """25.1682 — 개장 직전에 시작한 실행이 가상모의투자 단계에 닿을 때 개장 뒤면 종가 기준(시가로 차지 않게)."""
    import inspect

    from batch.jobs import daily

    assert daily._장_열림("KR", "2020-01-02") is True
    assert daily._장_열림("KR", "2099-01-02") is False
    assert daily._장_열림("KR", "2026-10-03") is False  # 휴장(개천절) — 개장 시각 없음
    assert daily._장_열림("KR", "아님") is False
    글 = inspect.getsource(daily.run)
    assert "or _장_열림(market, decision.session_date)" in 글


def test_real_portfolio_comparison_line(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 포트폴리오와 같은 기간 비교 — 실제 표는 읽기만 (2026-10-15 사용자 지시, 25.1448)."""
    c = client
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14")  # 실제 기록이 없으면 줄이 없다
    assert not any("실제 투자와 비교" in x for x in 줄) and "real_compare" not in 기록
    c.conn.executemany(
        "INSERT INTO portfolio_values (date, value_krw, net_flow_krw, dividends_krw, twr_index, created_at)"
        " VALUES (?, 1, 0, 0, ?, 't')", [("2026-10-01", 1.0), ("2026-10-14", 1.05), ("2026-10-16", 1.10)],
    )  # fmt: skip
    _시세(c, "2026-10-15", 10_100, 9_900)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    비교 = [x for x in 줄 if x.startswith("실제 투자와 비교")]
    assert len(비교) == 1 and "실제 +" in 비교[0]
    assert 기록["real_compare"]["start"] == "2026-10-14"
    assert c.q("SELECT COUNT(*) FROM portfolio_values") == [(3,)]  # 읽기만 했다



def test_decision_ledger_records_what_was_not_done(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """결정 원장 — 산 것·못 산 것(까닭)·안 찬 회차·판 것·플래그를 따르지 않고 든 것 (docs/paper.md 9장, 25.1449)."""
    from batch.services import sell_flags as sf

    c = client
    금액없음 = report_picks.SignalRow(**{**_신호().__dict__, "stock_id": 3, "ticker": "000660", "suggested_weight_pct": None})
    다음 = cal.next_session("KR", date(2026, 10, 14))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=[_신호(), 금액없음], settings=설정,
                 price_series={}, series_unknown={}, allocate=True, now=datetime(2026, 10, 15, tzinfo=UTC))  # fmt: skip
    결정 = c.q("SELECT account_id, stock_id, kind, reason, ref_close, ref_date FROM paper_decisions ORDER BY 1, 2")
    assert ("A", 1, "buy", "2부 배분", 10_000, "2026-10-14") in 결정
    assert any(r[:3] == ("A", 3, "skip") for r in 결정)  # 2부에서 빠진 후보도 까닭과 함께
    _시세(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    안찬 = c.q("SELECT account_id, decided_on, ref_close FROM paper_decisions WHERE kind = 'missed' ORDER BY 1")
    assert 안찬 == [("A", "2026-10-15", 9_900), ("B", "2026-10-15", 9_900)]  # 2·3회차는 한 줄로
    # 감성급락만 선 날 — A 는 팔고, B 는 규칙상 들고 간다(그 결과를 잰다)
    monkeypatch.setattr(sf, "evaluate", lambda h, _t, _r=None: [sf.Flag(h.stock_id, "yellow", "감성급락", "x", [])])
    _시세(c, "2026-10-16", 9_900, 9_950)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    판정 = c.q("SELECT account_id, kind, reason FROM paper_decisions WHERE decided_on = '2026-10-16' ORDER BY 1")
    assert 판정 == [("A", "sell", "감성급락"), ("B", "hold_flag", "감성급락")]
    # 같은 날 다시 돌아도 원장은 그대로
    n = c.q("SELECT COUNT(*) FROM paper_decisions")
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT COUNT(*) FROM paper_decisions") == n


def test_decision_outcomes_fill_from_prices_and_index(client: Client) -> None:
    """성과 채우기 — 기준 뒤 5·20 거래일 종가와 같은 날짜 KOSPI, 60 은 아직 (25.1449)."""
    c = client
    c.conn.execute(
        "INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
        " created_at) VALUES ('B', 'KR', '2026-09-01', 1, 'hold_flag', '감성급락', 10000, '2026-09-01', 't')"
    )
    날들 = [d for d in (date(2026, 9, 1) + timedelta(days=i) for i in range(1, 50)) if d.weekday() < 5][:30]
    for i, d in enumerate(날들, 1):
        _시세(c, d.isoformat(), 10_000 + i * 100, 10_000 + i * 100)
        c.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', ?, ?, 't', 't')",
                       [d.isoformat(), 2_000 + i * 10])  # fmt: skip
    c.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', '2026-09-01', 2000, 't', 't')")
    기록 = job.fill_outcomes(c, "KR", "2026-10-15", "now")
    assert 기록["filled"] == 1
    r5, r20, r60, b5, b20 = c.q("SELECT ret_5d, ret_20d, ret_60d, bench_5d, bench_20d FROM paper_decisions")[0]
    assert r5 == pytest.approx(5.0) and r20 == pytest.approx(20.0) and r60 is None
    assert b5 == pytest.approx(2.5) and b20 == pytest.approx(10.0)
    # 시세가 끝내 모자라면 기한 뒤 까닭을 적고 그만 본다
    기록 = job.fill_outcomes(c, "KR", "2027-04-01", "now")
    assert 기록["gave_up"] == 1
    assert "거래일뿐" in c.q("SELECT outcome_note FROM paper_decisions")[0][0]
    # 뒤 실행이 이미 채운 창을 지우지 않는다
    assert c.q("SELECT ret_5d, ret_20d, bench_5d FROM paper_decisions")[0] == (
        pytest.approx(5.0), pytest.approx(20.0), pytest.approx(2.5))


def test_halt_switch_blocks_new_orders_and_records_why(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """멈춤 스위치 — 새 주문을 내지 않고 결정 원장에 까닭을 남긴다 (docs/paper.md 11장, 25.1451)."""
    c = client
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('paper_halt', '{\"on\": true}', 't')")
    _, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert c.q("SELECT COUNT(*) FROM paper_orders") == [(0,)]
    assert 기록["halted"] is True and 기록["accounts"]["A"]["risk_blocked"] == 3
    assert c.q("SELECT DISTINCT kind, reason FROM paper_decisions") == [("blocked", "위험 점검: 멈춤 스위치가 켜져 있음")]


def test_outcome_queue_skips_rows_waiting_only_for_60d_and_notes_missing_ref(client: Client) -> None:
    """성과 대기열 (25.1453, 교차검증 [필수] 1) — 60 만 기다리는 행은 자리를 먹지 않고, 기준가 없는 행은 바로 까닭을 적는다."""
    c = client
    ins = ("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
           " ret_5d, ret_20d, created_at) VALUES ('A', 'KR', ?, 1, ?, 'x', ?, ?, ?, ?, 't')")  # fmt: skip
    c.conn.execute(ins, ["2026-09-01", "skip", 10_000, "2026-09-01", 1.0, 2.0])  # 5·20 은 찼고 60 은 아직 때가 아님
    c.conn.execute(ins, ["2026-09-02", "buy", None, None, None, None])  # 기준가 없음
    c.conn.execute(ins, ["2026-10-01", "sell", 10_000, "2026-10-01", None, None])  # 5 가 찰 때
    for i, d in enumerate(["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"], 1):
        _시세(c, d, 10_000 + i * 100, 10_000 + i * 100)
    기록 = job.fill_outcomes(c, "KR", "2026-10-15", "now")
    assert 기록 == {"pending_read": 2, "filled": 1, "gave_up": 1}
    assert c.q("SELECT outcome_note FROM paper_decisions WHERE decided_on = '2026-09-02'") == [("기준 종가 없음",)]
    # 같은 날 다시 — 이미 채운 창만 다시 계산되면 쓰지 않는다
    assert job.fill_outcomes(c, "KR", "2026-10-15", "now")["filled"] == 0


def test_deposit_cancel_day_is_not_a_daily_loss(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """입금 취소일을 −50% 손실로 읽어 매수를 막지 않는다 — 하루 수익은 시간가중으로 (25.1453, 교차검증 [필수] 2)."""
    c = client
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:01+00:00', 'k2')")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-14", signals=False)  # 200만으로 첫 기록
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, cancels_id, created_at, request_key)"
                   " VALUES ('2026-10-15', -1000000, 'cancel', 2, '2026-10-15T09:00:00.000Z', 'k3')")  # fmt: skip
    _, 기록 = _하루(c, monkeypatch, "2026-10-15")
    assert 기록["accounts"]["A"]["risk_blocked"] == 0
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'A' AND decided_on = '2026-10-15'") == [(3,)]


def test_gate_sees_candidate_sectors(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """후보 종목의 업종도 관문에 — 보유만 채우면 새 종목의 섹터 상한이 꺼졌다 (25.1453, 교차검증 [필수] 3)."""
    from batch.services import risk_gate as rg

    본 = []
    원래 = rg.gate
    monkeypatch.setattr(rg, "gate", lambda 후보, 문맥: (본.append(문맥), 원래(후보, 문맥))[1])
    _하루(client, monkeypatch, "2026-10-14")
    assert any(문맥.sector_of.get(1) == "전자" for 문맥 in 본)
