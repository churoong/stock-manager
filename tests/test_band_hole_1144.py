"""밸류에이션 밴드가 수정주가 가운데 빈 행에 원 종가를 쓰던 것 (docs/infra.md 25.1144).

1:10 분할 전 구간(원 종가 1,000 · 수정 100) 한가운데 `adj_close` 가 빈 행이 하나 끼면
`COALESCE(adj_close, close)` 가 1,000 을 넣어 그날 PBR 이 10배로 튀고 밴드 상단이 틀어졌다.
"""

from __future__ import annotations

from batch.core import db
from batch.jobs import signals as sj
from batch.jobs import valuation_bands as vbj
from batch.services import adjust as adj
from tests.test_portfolio_job import MemClient

날짜 = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"]
# (close, adj_close): 분할 전 셋째 날이 빈 행, 마지막 날은 분할 뒤
행 = [(1000, 100), (1000, 100), (1000, None), (1000, 100), (100, None)]


def _mem() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    for d, (c, a) in zip(날짜, 행, strict=True):
        mem.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'KRW', 't', 't')",
            [d, c, a],
        )
    return mem


def test_배치_밴드는_빈_행을_직전_계수로_메운다() -> None:
    가격 = vbj.load_prices(_mem(), [1], "2026-09-08")  # type: ignore[arg-type]
    assert [p for _, p in 가격[1]] == [100.0, 100.0, 100.0, 100.0, 100.0]


def test_신호_밴드도_같은_가격을_넘긴다() -> None:
    mem = _mem()
    mem.conn.execute(
        "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
        " receipt_no, currency, unit, total_equity, source, fetched_at)"
        " VALUES (1, 2025, ?, 'A', 1, '2026-03-01', 'r1', 'KRW', 'KRW', 50000, 't', 't')",
        [sj.ANNUAL_REPORT_CODE],
    )
    넘긴것: list = []
    원래 = sj.sg.pbr_series
    sj.sg.pbr_series = lambda prices, equities, shares: (넘긴것.append(prices), 원래(prices, equities, shares))[1]  # type: ignore[assignment]
    try:
        sj.load_band(mem, 1, "2026-09-08", 1_000)  # type: ignore[arg-type]
    finally:
        sj.sg.pbr_series = 원래  # type: ignore[assignment]
    assert [p for _, p in 넘긴것[0]] == [100.0, 100.0, 100.0, 100.0, 100.0]


def test_미국은_원_종가를_그대로_쓴다() -> None:
    줄 = [{"close": 10, "adj_close": 9}, {"close": 11, "adj_close": None}]
    assert adj.split_only_prices(줄, "US") == [10.0, 11.0]


def test_달력_창이_두_시장_모두_BAND_DAYS_거래일을_덮는다() -> None:
    """1100 일은 국내 거래일 732~735 라 신호 밴드(750 행)보다 짧았다 (docs/infra.md 25.1146)."""
    from datetime import date, timedelta

    import pandas as pd

    from batch.core import calendar as cal
    from batch.services import valuation_band as vb

    for 시장 in ("KR", "US"):
        달력 = cal.exchange_calendar(시장)
        for 기준 in ("2025-10-10", "2026-02-20", "2026-10-12"):
            끝 = date.fromisoformat(기준)
            시작 = 끝 - timedelta(days=vbj.LOOKBACK_CALENDAR_DAYS)
            # 질의는 `date > since` — 시작일은 빠진다
            세션 = 달력.sessions_in_range(pd.Timestamp(시작 + timedelta(days=1)), pd.Timestamp(끝))
            assert len(세션) >= vb.BAND_DAYS, (시장, 기준, len(세션))


def test_신호_가격_계열도_빈_행을_메우고_창_첫머리까지_메운다() -> None:
    """신호 가격 계열(이동평균·이격도 재료)도 `COALESCE` 로 원 종가를 넣었다 (docs/infra.md 25.1156)."""
    mem = _mem()  # 09-01·02 100, 09-03 빈 행(1000), 09-04 100, 09-07 분할 뒤 100
    가격 = sj.load_recent_prices_for(mem, [1], "2026-09-08", 4)  # type: ignore[arg-type]
    assert [(d, p) for d, p, _ in 가격[1]] == [
        ("2026-09-02", 100.0), ("2026-09-03", 100.0), ("2026-09-04", 100.0), ("2026-09-07", 100.0),
    ]
    # 창 첫 행이 빈 행이어도(앞 한 행을 더 읽어) 메운다
    가격 = sj.load_recent_prices_for(mem, [1], "2026-09-08", 3)  # type: ignore[arg-type]
    assert [p for _, p, _ in 가격[1]] == [100.0, 100.0, 100.0]


def test_참고_분석_계열도_수정주가_빈_행을_메운다() -> None:
    """유니버스 밖 참고 분석의 시세 질의에 `adj_close` 칸이 없어 `series_from_rows` 가 메우지 못했다 (25.1164)."""
    from batch.jobs import analyze_extra as ax
    from batch.jobs import scores

    mem = _mem()
    행 = mem.execute(ax.EXTRA_SERIES_SQL, ["[1]", "2026-09-01", "2026-09-08"]).dicts()
    _날, 종가, _값 = scores.series_from_rows(행, 5)[1]
    assert 종가 == [100.0, 100.0, 100.0, 100.0, 100.0]


def test_신호_성적표_종가도_빈_행을_메운다() -> None:
    """60일 창 최고가가 빈 행의 원 종가로 +900% 튀어 "목표가 도달" 이 거짓으로 찍혔다 (25.1165)."""
    from batch.jobs import signal_outcomes as so

    원: dict = {}
    종가 = so.load_closes(_mem(), [1], "2026-09-01", 원)  # type: ignore[arg-type]
    assert [p for _, p in 종가[1]] == [100.0, 100.0, 100.0, 100.0, 100.0]
    assert 원[1]["2026-09-03"] == 1000.0  # 원 종가는 단위 맞추기용으로 그대로


def test_2부_상관용_시세도_빈_행을_메운다() -> None:
    """빈 행 하나가 +900%·−90% 이틀치 수익률이 되어 60일 상관을 혼자 끌었다 (25.1166)."""
    from batch.jobs import daily

    가격 = daily.load_price_series(_mem(), [1], "2026-09-08")  # type: ignore[arg-type]
    assert list(가격[1].values()) == [100.0, 100.0, 100.0, 100.0, 100.0]


def test_스트레스_시험_바구니_시세도_빈_행을_메운다() -> None:
    """위기 구간 바구니 수익률이 빈 행의 원 종가로 틀어졌다 (25.1167)."""
    from batch.jobs import stress

    가격 = stress.load_prices(_mem(), [1], "2026-09-01")  # type: ignore[arg-type]
    assert list(가격[1].values()) == [100.0, 100.0, 100.0, 100.0, 100.0]


def test_포트폴리오_평가_종가도_빈_행을_메운다() -> None:
    """보유 평가 계열이 빈 행의 원 종가(10배)로 하루 튀어 TWR·포트폴리오 MDD 가 틀어졌다 (docs/infra.md 25.1188)."""
    from batch.jobs import portfolio as pj

    종가 = pj.load_closes(_mem(), [1], "2026-09-01")  # type: ignore[arg-type]
    assert list(종가[1].values()) == [100.0, 100.0, 100.0, 100.0, 100.0]
