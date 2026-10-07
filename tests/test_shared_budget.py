"""원화 풀은 하나다 — 다른 나라 리포트의 2부 배분을 여력에서 뺀다 (docs/infra.md 25.508, 감사)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from batch.jobs import daily
from batch.services import report_picks as rp
from batch.services import reports
from tests.test_report_picks import SqliteClient, row


def _리포트(
    c: SqliteClient, market: str, hours_ago: float, allocs: list[tuple[int, float, str]], trade_date: str | None = None
) -> None:
    at = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    for sid, _, _ in allocs:
        c.conn.execute(
            "INSERT OR IGNORE INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'X', ?, 'X', 'active', 't', 't')",
            [sid, f"T{sid}", market],
        )
    cur = c.conn.execute(
        "INSERT INTO daily_reports (market, trade_date, status, generated_at, summary_text, warnings_json)"
        " VALUES (?, ?, 'success', ?, 't', '[]')",
        [market, trade_date or at[:10] + f"-{hours_ago}", at],
    )
    for rank, (sid, amount, currency) in enumerate(allocs, 1):
        c.conn.execute(
            "INSERT INTO report_items (report_id, section, stock_id, rank, payload_json) VALUES (?, ?, ?, ?, ?)",
            [cur.lastrowid, reports.SECTION_BUY, sid, rank, json.dumps({"amount": amount, "currency": currency})],
        )


def test_국내_리포트는_어젯밤_미국_배분을_원화로_뺀다() -> None:
    c = SqliteClient()
    c.conn.execute(
        "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, 1400, 't', 't')",
        [datetime.now(UTC).date().isoformat()],  # 7일 넘은 환율은 쓰지 않는다(fx.usable)
    )
    _리포트(c, "US", 10, [(7, 1000.0, "USD"), (8, 500.0, "USD")])
    _리포트(c, "US", 34, [(9, 99999.0, "USD")])  # 그 전날 것은 잡지 않는다
    보유 = [rp.Holding(stock_id=8, ticker="B", name="B", sector=None, value=1.0)]
    합, 말 = daily._other_report_reserved(c, "KR", "KRW", None, 보유)  # type: ignore[arg-type]
    # 보유 종목의 추가 배분도 뺀다 (25.515, 교차검증)
    assert 합 == 2_100_000.0 and 말 is not None and "미국 리포트 2부 배분" in 말 and "만큼 여력을 줄였습니다" in 말


def test_월요일_국내는_금요일_밤_미국을_잡는다() -> None:
    """20시간만 보면 약 59시간 전 금요일 밤 미국 리포트를 못 잡았다 (docs/infra.md 25.515, 교차검증)."""
    c = SqliteClient()
    _리포트(c, "KR", 72, [])  # 금요일 아침 국내
    _리포트(c, "US", 59, [(1, 100.0, "USD")])  # 금요일 밤 미국 — 국내 앞 리포트 뒤
    _리포트(c, "US", 96, [(2, 999.0, "USD")])  # 그 전 것은 잡지 않는다
    합, _ = daily._other_report_reserved(c, "KR", "USD", 1400.0, [])  # type: ignore[arg-type]
    assert 합 == 100.0


def test_월요일_force_재실행도_금요일_밤_미국을_잡는다() -> None:
    """오늘 아침 같은 거래일 리포트를 "앞 리포트" 로 잡아 0 으로 셌다 (docs/infra.md 25.520, 교차검증 재현)."""
    c = SqliteClient()
    _리포트(c, "KR", 72, [], trade_date="2026-09-24")  # 금요일 아침 국내
    _리포트(c, "US", 59, [(1, 100.0, "USD")], trade_date="2026-09-25")  # 금요일 밤 미국
    _리포트(c, "KR", 2, [], trade_date="2026-09-25")  # 월요일 아침 국내(같은 거래일 — 지금 다시 돈다)
    합, _ = daily._other_report_reserved(c, "KR", "USD", 1400.0, [], "2026-09-25")  # type: ignore[arg-type]
    assert 합 == 100.0


def test_미국_리포트는_아침_국내_배분을_달러로_뺀다() -> None:
    c = SqliteClient()
    _리포트(c, "KR", 14, [(1, 1_400_000.0, "KRW")])
    합, _ = daily._other_report_reserved(c, "US", "USD", 1400.0, [])  # type: ignore[arg-type]
    assert 합 == 1000.0


def test_여력에서_빠진다() -> None:
    """여력 4,000만에 국내 3,000만 + 미국 3,000만이 둘 다 배분되던 것 (감사 재현)."""
    rows = [row(i, suggested_amount=10_000_000.0, tranche_plan=[{"step": 1, "ratio": 1.0, "price": 1000}])
            for i in range(1, 6)]  # fmt: skip
    보유 = [rp.Holding(stock_id=99, ticker="H", name="H", sector=None, value=60_000_000.0)]
    예전 = rp.build_portfolio(rows, 100_000_000, holdings=보유, max_stock_pct=100)
    지금 = rp.build_portfolio(rows, 100_000_000, holdings=보유, max_stock_pct=100, reserved=30_000_000.0)
    assert sum(a.amount for a in 예전.allocations) == 40_000_000
    assert sum(a.amount for a in 지금.allocations) == 10_000_000


def test_다른_나라_보유의_업종_합산_한계를_2부에_적는다() -> None:
    """국내 KSIC·미국 SIC 이름표가 달라 같은 산업이 합산되지 않을 수 있다 (docs/infra.md 25.509, 감사)."""
    from batch.notify import report_sections as rs

    보유 = [rp.Holding(stock_id=9, ticker="NVDA", name="엔비디아", sector="전자·전기장비", value=25_000_000.0,
                       country="US")]  # fmt: skip
    view = rp.build_portfolio([row(1, sector="전자부품·컴퓨터·통신장비")], 100_000_000, holdings=보유)
    assert view.sector_note is not None and "다른 나라 보유 1종목" in view.sector_note
    assert view.sector_note in rs.render_portfolio(view, [])
    같은나라 = [rp.Holding(stock_id=9, ticker="A", name="A", sector="x", value=1.0, country="KR")]
    assert rp.build_portfolio([row(1)], 100_000_000, holdings=같은나라).sector_note is None


def test_보유에_나라를_싣는다() -> None:
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    db.종목(1, "US")
    db.conn.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " market_value, market_value_krw, updated_at) VALUES (1, 1, 'USD', 1, 1, 1, 1, '2026-01-02', 10, 14000, 't')"
    )
    assert [h.country for h in daily.load_holdings(db, "KRW", None)] == ["US"]  # type: ignore[arg-type]


def test_환율이_없어_못_뺀_배분은_말한다() -> None:
    """환율이 없으면 `continue` 로 넘어가 여력 전체를 쓰고 아무 말이 없었다 (docs/infra.md 25.646, 감사)."""
    c = SqliteClient()
    _리포트(c, "US", 10, [(7, 20_000.0, "USD")])
    합, 말 = daily._other_report_reserved(c, "KR", "KRW", None, [])  # type: ignore[arg-type]
    assert 합 == 0.0 and 말 is not None and "환율이 없어 여력에서 빼지 못했습니다" in 말 and "예산을 넘을 수" in 말


def test_환산했으면_환율을_적는다() -> None:
    c = SqliteClient()
    _리포트(c, "US", 10, [(7, 1000.0, "USD")])
    _합, 말 = daily._other_report_reserved(c, "KR", "KRW", 1400.0, [])  # type: ignore[arg-type]
    assert 말 is not None and "환율 1,400.00" in 말


def test_배분을_못_읽으면_말한다() -> None:
    class 깨진:
        def execute(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("네트워크")

    합, 말 = daily._other_report_reserved(깨진(), "KR", "KRW", None, [])  # type: ignore[arg-type]
    assert 합 == 0.0 and 말 is not None and "읽지 못해" in 말
