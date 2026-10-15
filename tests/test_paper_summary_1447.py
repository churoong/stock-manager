"""모의 계좌 주간·월간 요약 (docs/paper.md 6장, docs/infra.md 25.1447)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.jobs import paper as job
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client, _시세, _하루

비용 = pp.Costs(0.015, 0.015, 0.15, 0.10)


def _행(aid: str, as_of: str, invested: float, equity: float, twr: float, positions: list[dict] | None = None,
       kospi: float | None = None) -> dict:  # fmt: skip
    return {"account_id": aid, "as_of": as_of, "invested_krw": invested, "equity_krw": equity, "pnl_krw": equity - invested,
            "twr_index": twr, "kospi_krw": kospi, "sp500_krw": None, "positions_json": json.dumps(positions or [])}  # fmt: skip


def _체결(oid: int, side: str, day: str, price: float, qty: int = 10, sid: int = 1) -> pp.Fill:
    o = pp.Order(id=oid, account_id="A", stock_id=sid, side=side, order_type="limit" if side == "buy" else "market",
                 limit_price=price, quantity=qty, currency="KRW", fill_session=day, horizon="short")  # fmt: skip
    return pp.make_fill(o, price, day, 1.0, 비용)


한도 = {"max_stock_pct": 10.0, "max_sector_pct": 30.0, "stop_pct": {"short": -7.0, "mid": -15.0, "long": -25.0}}


def test_weekly_lines_compare_same_money_benchmarks_and_twins() -> None:
    daily = {
        "A": [_행("A", "2026-10-02", 1_000_000, 1_000_000, 1.0), _행("A", "2026-10-09", 1_000_000, 1_050_000, 1.05, kospi=1_020_000)],
        "B": [_행("B", "2026-10-02", 1_000_000, 1_000_000, 1.0), _행("B", "2026-10-09", 1_000_000, 1_030_000, 1.03)],
    }  # fmt: skip
    줄 = pp.summary_lines(daily, {"A": [_체결(1, "buy", "2026-10-05", 10_000)]}, "2026-10-03", None, {}, {}, 한도)
    assert 줄[0] == "[모의 투자]"
    assert "A: 입금 1,000,000원 · 평가 1,050,000원 (손익 +5.0%, 이번 주 시간가중 +5.0%, 누적 +5.0%)" in 줄[1]
    assert "같은 돈 KOSPI +2.0%" in 줄[1] and "S&P500 —" in 줄[1]
    assert "이번 주 체결 매수 1·매도 0" in 줄[1]
    assert 줄[3].startswith("  A−B: +20,000원")
    assert not any("월간" in x for x in 줄)


def test_monthly_review_best_worst_and_rule_checks() -> None:
    fills = [_체결(1, "buy", "2026-09-01", 10_000), _체결(2, "sell", "2026-09-10", 12_000),
             _체결(3, "buy", "2026-09-11", 10_000, sid=2), _체결(4, "sell", "2026-09-20", 8_000, sid=2)]  # fmt: skip
    # 9-15: 종목 2 가 평가 총액의 20%(상한 10% 넘음)·원가 대비 −10%(단기 손절 −7% 아래)
    넘은날 = [{"stock_id": 2, "value_krw": 200_000, "cost_krw": 222_222, "horizon": "short"}]
    daily = {"A": [_행("A", "2026-09-15", 1_000_000, 1_000_000, 1.0, 넘은날), _행("A", "2026-10-02", 1_000_000, 990_000, 0.99)]}
    줄 = pp.summary_lines(daily, {"A": fills}, "2026-09-26", "2026-09-01", {1: "가", 2: "나"}, {2: "전자"}, 한도)
    본문 = "\n".join(줄)
    assert "[모의 투자 월간 복기] 2026-09-01 부터" in 본문
    assert "A 이익 셋: 가 +" in 본문 and "A 손실 셋: 나 −" not in 본문 and "A 손실 셋: 나 -" in 본문
    assert "종목 상한 넘은 날 1 · 섹터 상한 넘은 날 0 · 손절선 아래로 들고 간 날 1" in 본문


def test_no_paper_rows_no_section() -> None:
    assert pp.summary_lines({}, {}, "2026-10-03", None, {}, {}, 한도) == []


def test_summary_section_reads_real_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 마이그레이션 위에서 배치가 적은 평가·체결을 읽어 절을 만든다 — 월 첫 주면 월간 복기까지."""
    from tests.test_paper_job_1445 import 설정  # noqa: F401 — 같은 설정을 쓰는 하루 단계

    c = Client()
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')"
    )
    from batch.services import report_picks

    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    줄 = job.summary_section(c, datetime(2026, 10, 18, 0, 0, tzinfo=UTC))
    assert 줄[0] == "[모의 투자]" and any(x.startswith("  A: 입금 1,000,000원") for x in 줄)
    assert any("이번 주 체결 매수 1·매도 0" in x for x in 줄)
    assert not any("월간" in x for x in 줄)
    월 = job.summary_section(c, datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    assert any("[모의 투자 월간 복기] 2026-10-01 부터" in x for x in 월)
    assert any(x.startswith("[노하우 성적표 — 20 거래일 뒤") for x in 월)  # 성과 분해 (25.1450)
    assert job.summary_section(Client(), datetime(2026, 10, 18, tzinfo=UTC)) == []
    # 실제 포트폴리오가 있으면 모의 절 아래에 같은 기간 비교 한 줄 (25.1448)
    c.conn.executemany(
        "INSERT INTO portfolio_values (date, value_krw, net_flow_krw, dividends_krw, twr_index, created_at)"
        " VALUES (?, 1, 0, 0, ?, 't')", [("2026-10-10", 1.0), ("2026-10-17", 1.02)],
    )  # fmt: skip
    월 = job.summary_section(c, datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    비교 = [i for i, x in enumerate(월) if "실제 투자와 비교" in x]
    assert len(비교) == 1 and 비교[0] < next(i for i, x in enumerate(월) if x.startswith("[모의 투자 월간"))
