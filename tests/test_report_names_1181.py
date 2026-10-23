"""미국 리포트 2부·"배분에서 빠진 종목" 에 이름 꼬리가 남던 것 (docs/infra.md 25.1181, 감사 재현)."""

from __future__ import annotations

from batch.services import report_picks as rp


def _row(i: int, name: str, **kw) -> rp.SignalRow:  # noqa: ANN003
    base = dict(stock_id=i, ticker=f"T{i}", name=name, market="NASDAQ", horizon="mid", signal_type="실적 모멘텀",
                currency="USD", buy_zone_low=90.0, buy_zone_high=100.0, suggested_weight_pct=5.0,
                suggested_amount=1000.0, size_reduction=1.0,
                tranche_plan=[{"step": 1, "ratio": 1.0, "price": 100, "amount": 1000}], rationale_text="매출 +18.2%",
                as_of_date="2026-10-08", total_score=80.0 - i, close=100.0, close_date="2026-10-08",
                criteria=[{"name": "x", "label": "매출", "display": "18.2%", "value": 18.2, "threshold": ">0",
                           "source": "sec", "passed": True}])  # fmt: skip
    base.update(kw)
    return rp.SignalRow(**base)  # type: ignore[arg-type]


def test_2부와_제외_줄에도_이름_꼬리를_뗀다() -> None:
    rows = [_row(1, "Kennametal Inc. Common Stock"),
            _row(2, "Acme Holding Ltd - Ordinary Shares", suggested_amount=None, suggested_weight_pct=0.0,
                 size_reduction=0.0)]  # fmt: skip
    글 = rp.compose(rows, 20000.0, "2026-10-08", currency="USD").text
    assert "Common Stock" not in 글 and "Ordinary Shares" not in 글
    # 25.1740 부터 1부·2부·제외 줄 모두 운용 보고와 같은 짧은 이름 + (티커) — 법인 꼬리("Inc."·"Holding Ltd")도 뗀다
    assert "Kennametal(T1)" in 글 and "Acme(T2)" in 글 and "Inc." not in 글


def test_보내지_못한_리포트는_추천_이력에_세지_않는다() -> None:
    """발송·예비 실행이 모두 실패한 날의 리포트가 다음 날 "2번째 추천 (연속 2일)" 로 셌다 (docs/infra.md 25.1182)."""
    from batch.core import db
    from batch.jobs import daily
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
              " VALUES (5, '000005', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip
    for rid, day, sent in ((1, "2026-10-06", "2026-10-06T23:30:00Z"), (2, "2026-10-07", None)):
        c.execute("INSERT INTO daily_reports (id, market, trade_date, status, generated_at, summary_text, warnings_json,"
                  " sent_at) VALUES (?, 'KR', ?, 'success', 't', 'x', '[]', ?)", [rid, day, sent])  # fmt: skip
        c.execute("INSERT INTO report_items (report_id, section, rank, stock_id, payload_json)"
                  " VALUES (?, 'recommend', 1, 5, '{\"close\": 100}')", [rid])  # fmt: skip
    경고: list[str] = []
    이력 = daily._pick_history(mem, "KR", "2026-10-08", 경고)  # type: ignore[arg-type]
    assert 이력 is not None and not 경고
    assert 이력[5].times == 1 and 이력[5].first_date == "2026-10-06"
