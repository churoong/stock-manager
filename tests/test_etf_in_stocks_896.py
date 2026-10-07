"""추천 ETF 의 매일 종가·매매 기록 (docs/infra.md 25.896, 2026-10-02 사용자 요청).

ETF 를 stocks 에 **잇되**(`asset_type = 'etf'`) 보통주 길(유니버스·종목 찾기·주식수·재무)에는 섞지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.jobs import daily, etf_link, portfolio, universe
from tests.test_scores_job import _sqlite_client


def _etf(c, eid: int, symbol: str, country: str, passed: int = 1, satellite: bool = False) -> None:
    c.execute(
        "INSERT INTO etfs (id, symbol, country, name, exchange, yahoo_symbol, status, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'active', 't', 't')",
        [eid, symbol, country, f"ETF {symbol}", "NYSE Arca" if country == "US" else None,
         symbol if country == "US" else f"{symbol}.KS"],
    )  # fmt: skip
    if satellite:
        c.execute(
            "INSERT INTO etf_satellite_picks (etf_id, as_of_date, sat_group, sub_group, passed, rationale_text,"
            " rationale_data, calc_version, created_at) VALUES (?, '2026-10-02', '배당', '배당', ?, 't', '{}', 1, 't')",
            [eid, passed],
        )
    else:
        c.execute(
            "INSERT INTO etf_picks (etf_id, as_of_date, passed, rationale_text, rationale_data, calc_version, created_at)"
            " VALUES (?, '2026-10-02', ?, 't', '{}', 1, 't')",
            [eid, passed],
        )


def test_통과_ETF_만_잇고_두_번_돌려도_같다() -> None:
    c = _sqlite_client()
    _etf(c, 1, "069500", "KR")
    _etf(c, 2, "091160", "KR", satellite=True)
    _etf(c, 3, "229200", "KR", passed=0)
    assert etf_link.link(c, "KR") == {"new": 2, "converted": 0, "already": 0}
    assert etf_link.link(c, "KR") == {"new": 0, "converted": 0, "already": 2}
    rows = c.execute(
        "SELECT ticker, market, asset_type, currency, yahoo_symbol FROM stocks WHERE asset_type = 'etf' ORDER BY ticker"
    ).rows
    assert rows == [("069500", "ETF", "etf", "KRW", "069500.KS"), ("091160", "ETF", "etf", "KRW", "091160.KS")]
    linked = dict(c.execute("SELECT symbol, stock_id FROM etfs").rows)
    assert linked["229200"] is None and linked["069500"] is not None


def test_옛_계산_판에서만_통과한_ETF_는_잇지_않는다() -> None:
    """판을 올려 같은 날 다시 판정하면 옛 판 행이 남는다 — 판을 안 봐 옛 규칙의 통과까지 이었다 (docs/infra.md 25.935, 감사)."""
    c = _sqlite_client()
    _etf(c, 1, "069500", "KR")  # 판 1 통과
    _etf(c, 2, "091160", "KR", satellite=True)  # 판 1 통과
    for 표, eid, 통과 in (("etf_picks", 1, 0), ("etf_picks", 2, 0), ("etf_satellite_picks", 2, 0)):
        if 표 == "etf_picks":
            c.execute(
                "INSERT INTO etf_picks (etf_id, as_of_date, passed, rationale_text, rationale_data, calc_version,"
                " created_at) VALUES (?, '2026-10-02', ?, 't', '{}', 2, 't')",
                [eid, 통과],
            )
        else:
            c.execute(
                "INSERT INTO etf_satellite_picks (etf_id, as_of_date, sat_group, sub_group, passed, rationale_text,"
                " rationale_data, calc_version, created_at) VALUES (?, '2026-10-02', '배당', '배당', ?, 't', '{}', 2, 't')",
                [eid, 통과],
            )
    assert etf_link.link(c, "KR") == {"new": 0, "converted": 0, "already": 0}


def test_미국은_같은_티커의_예전_줄을_ETF_로_바꿔_쓴다() -> None:
    """나스닥 목록에서 들어와 '보통주 아님' 으로 빠졌던 줄을 다시 살린다 — 새 줄을 만들면 같은 티커가 둘이 된다."""
    c = _sqlite_client()
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (50, 'SPY', 'NYSE Arca', 'US', 'USD', 'excluded', 't', 't')"
    )
    _etf(c, 1, "SPY", "US")
    assert etf_link.link(c, "US")["converted"] == 1
    assert c.execute("SELECT status, asset_type FROM stocks WHERE id = 50").rows == [("active", "etf")]
    assert c.execute("SELECT stock_id FROM etfs WHERE id = 1").scalar() == 50


def test_보통주_길은_ETF_를_보지_않는다() -> None:
    c = _sqlite_client()
    _etf(c, 1, "SPY", "US")
    etf_link.link(c, "US")
    sid = c.execute("SELECT id FROM stocks WHERE ticker = 'SPY'").scalar()
    c.execute("UPDATE stocks SET yahoo_symbol = 'SPY' WHERE id = ?", [sid])
    # 시세는 받고, 주식수·재무는 부르지 않는다
    assert "SPY" in daily._us_symbol_ids(c, include_etf=True)  # type: ignore[arg-type]
    assert "SPY" not in daily._us_symbol_ids(c)  # type: ignore[arg-type]
    # 나스닥 목록의 '보통주 아님' 판정이 이은 ETF 를 빼지 않는다
    for sql, args in universe.us_exclusion_statements([SimpleNamespace(symbol="SPY", is_common_stock=False)], "t"):
        c.execute(sql, args)
    assert c.execute("SELECT status FROM stocks WHERE id = ?", [sid]).scalar() == "active"


def test_국내_ETF_종가를_거래소_ETF_일별에서_저장한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.sources import krx

    c = _sqlite_client()
    _etf(c, 1, "069500", "KR")
    etf_link.link(c, "KR")
    row = SimpleNamespace(isu_cd="069500", close=35000.0, open=34800.0, high=35100.0, low=34700.0,
                          volume=1000, value=35_000_000, change_pct=0.5)  # fmt: skip
    other = SimpleNamespace(isu_cd="999999", close=1.0, open=None, high=None, low=None, volume=None, value=None,
                            change_pct=None)  # fmt: skip
    monkeypatch.setattr(krx, "fetch_etf_daily", lambda bas_dd: SimpleNamespace(
        ok=True, data=[row, other], error=None, source="krx_etf", attempts=1))  # fmt: skip
    assert daily._store_kr_etf_day(c, "20261002", "2026-10-02") == []  # type: ignore[arg-type]
    assert c.execute("SELECT date, close, volume FROM prices").rows == [("2026-10-02", 35000.0, 1000)]


def test_이은_ETF_가_없으면_거래소를_부르지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.sources import krx

    monkeypatch.setattr(krx, "fetch_etf_daily", lambda bas_dd: pytest.fail("부르면 안 된다"))
    assert daily._store_kr_etf_day(_sqlite_client(), "20261002", "2026-10-02") == []  # type: ignore[arg-type]


def test_국내_ETF_매도는_세금을_비우면_0_이다() -> None:
    """ETF 는 증권거래세가 없다. 저장된 값은 그대로 두고 계산할 때만 0 으로 읽는다(trades 는 사용자 입력값만)."""
    assert portfolio._tax({"tax": None, "side": "sell", "asset_type": "etf", "currency": "KRW"}) == 0.0
    assert portfolio._tax({"tax": None, "side": "sell", "asset_type": "stock", "currency": "KRW"}) is None
    assert portfolio._tax({"tax": 12.0, "side": "sell", "asset_type": "etf", "currency": "KRW"}) == 12.0


def test_국내_수정주가는_이은_ETF_를_조정하지_않는다() -> None:
    """ETF 는 구멍 메우기 길이 없어 하루 빠지면 가짜 기업행위로 과거 전체가 틀어졌다 (docs/infra.md 25.916, 감사)."""
    from batch.jobs import adjust_kr

    c = _sqlite_client()
    _etf(c, 1, "069500", "KR")
    etf_link.link(c, "KR")
    etf_id = c.execute("SELECT id FROM stocks WHERE ticker = '069500'").scalar()
    c.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, '2026-10-02', 1, 'KRW', 't', 't')",
        [etf_id],
    )
    assert etf_id not in adjust_kr.load_stock_ids(c, None)  # type: ignore[arg-type]
    assert adjust_kr.load_stock_ids(c, "069500") == [etf_id]  # 손으로 고르면 한다  # type: ignore[arg-type]
