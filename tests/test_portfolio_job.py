"""포트폴리오 재계산 배치를 실제 마이그레이션을 적용한 메모리 SQLite 에 끝까지 돌린다. 네트워크를 타지 않는다."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from batch.core.turso import ResultSet
from batch.jobs import portfolio as job


class MemClient:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in statements]

    def close(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MemClient:
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    from batch.core import db

    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, sector)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't', '전자부품·컴퓨터·통신장비')"
    )
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at, sector)"
        " VALUES (2, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't', '산업기계·컴퓨터')"
    )
    for day, kr, us, fx in (
        ("2026-09-14", 70_000, 200.0, 1380.0),
        ("2026-09-15", 72_000, 205.0, 1390.0),
        ("2026-09-16", 75_000, 210.0, 1400.0),
    ):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (1, ?, ?, 'KRW', 't', 't')",
            [day, kr],
        )
        c.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (2, ?, ?, 'USD', 't', 't')",
            [day, us],
        )
        c.execute(
            "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 't', 't')", [day, fx]
        )

    def trade(tid, sid, side, day, price, qty, cur, fx, fee=0.0, tax=0.0):
        c.execute(
            "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
            " fee, tax, horizon, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, 'long', 't', ?)",
            [tid, sid, side, day, price, qty, cur, fx, fee, tax, f"2026-09-17T00:00:0{tid}"],
        )

    trade(1, 1, "buy", "2026-09-14", 70_000, 10, "KRW", 1.0)
    trade(2, 1, "sell", "2026-09-15", 72_000, 4, "KRW", 1.0)
    trade(3, 2, "buy", "2026-09-14", 200.0, 5, "USD", 1380.0)
    return mem


def test_끝까지_돌면_보유_체결묶음_평가_요약이_생긴다(client: MemClient) -> None:
    assert job.run() == 0
    c = client.conn

    lots = c.execute("SELECT stock_id, quantity, realized_pnl_krw FROM trade_lots").fetchall()
    assert lots == [(1, 4.0, pytest.approx(8_000.0))]

    positions = dict(
        (sid, (qty, mv))
        for sid, qty, mv in c.execute("SELECT stock_id, quantity, market_value_krw FROM positions").fetchall()
    )
    assert positions[1] == (6.0, pytest.approx(6 * 75_000))
    assert positions[2] == (5.0, pytest.approx(5 * 210.0 * 1400.0))

    summary = c.execute("SELECT totals_json, allocation_json, trades_version FROM portfolio_summary").fetchone()
    totals = json.loads(summary[0])
    usd = 5 * 210.0
    assert totals["unrealized_fx_pnl_krw"] == pytest.approx(usd * (1400.0 - 1380.0))
    # 미국 (1,050−1,000달러)×매수환율 1,380 + 국내 (75,000−70,000)×6주
    assert totals["unrealized_price_pnl_krw"] == pytest.approx((usd - 1000.0) * 1380.0 + 6 * 5_000)
    assert totals["realized_pnl_krw"] == pytest.approx(8_000.0)
    # 평가에 쓴 종가 날짜를 요약에 남긴다 — 계산한 날이 아니다 (docs/infra.md 25.331)
    assert totals["price_date_min"] == "2026-09-16" and totals["price_date_max"] == "2026-09-16"
    alloc = json.loads(summary[1])
    assert set(alloc["by_sector"]) == {"전자부품·컴퓨터·통신장비", "산업기계·컴퓨터"}
    assert summary[2] == job.trades_version(client)  # type: ignore[arg-type]

    values = c.execute("SELECT date FROM portfolio_values ORDER BY date").fetchall()
    assert [v[0] for v in values][:3] == ["2026-09-14", "2026-09-15", "2026-09-16"]


def test_다시_돌려도_같은_결과(client: MemClient) -> None:
    job.run()
    first = client.conn.execute("SELECT * FROM positions ORDER BY stock_id").fetchall()
    job.run()
    second = client.conn.execute("SELECT * FROM positions ORDER BY stock_id").fetchall()
    assert [row[:-1] for row in first] == [row[:-1] for row in second]  # updated_at 제외
    assert client.conn.execute("SELECT COUNT(*) FROM trade_lots").fetchone()[0] == 1


class Test안_넣은_비용률을_말한다:
    """**세금을 안 넣으면 조용히 0 이 됐다** (docs/infra.md 25.192).

    `services/portfolio.fee_and_tax` 는 비율이 없으면 `pct or 0.0` 으로 0 을 쓴다.
    25.171 이 "모르면 0 이 아니라 None" 을 설정 읽기에 넣었는데, **쓰는 쪽에서 다시
    0 이 된다.** 그 자체는 어쩔 수 없다 — 실현손익을 안 내면 화면이 통째로 빈다.
    그래서 `cost_rates` 가 **무엇을 모르는지 말하는 것**으로 갈음한다.

    그런데 말해 주는 곳이 **수수료만** 보고 있었다. 증권거래세(국내 0.18%)를 안 넣으면
    국내 매도 세금이 0 이 되고, 실현손익이 그만큼 좋게 나오는데 **아무 말도 없었다.**
    """

    수수료_넣음 = {"kr_buy_pct": 0.015, "kr_sell_pct": 0.015, "us_buy_pct": 0.01, "us_sell_pct": 0.01}

    def test_전부_비면_둘_다_말한다(self) -> None:
        from batch.jobs.portfolio import cost_rates

        _, 경고 = cost_rates({}, {})

        assert len(경고) == 2, 경고
        assert any("수수료율" in 줄 for 줄 in 경고)
        assert any("증권거래세" in 줄 for 줄 in 경고)

    def test_수수료만_넣으면_세금을_말한다(self) -> None:
        """**이것이 빠져 있었다.**"""
        from batch.jobs.portfolio import cost_rates

        _, 경고 = cost_rates(self.수수료_넣음, {})

        assert [줄 for 줄 in 경고 if "증권거래세" in 줄], f"세금을 안 넣었는데 아무 말이 없다: {경고}"

    def test_지금_구간에_세금을_비운_국내_매도가_없으면_세금은_조용하다(self) -> None:
        """지난 매도는 법정 표로 계산된다 — 쓰지 않는 설정을 비웠다고 경고하지 않는다 (docs/infra.md 25.795, 교차검증)."""
        from batch.jobs.portfolio import cost_rates
        from batch.services import backtest as bt_
        from batch.services import portfolio as pf_

        시행일 = bt_.KR_SELL_TAX_SCHEDULE[-1][0]
        지난 = [pf_.Trade(1, 1, "sell", "2025-06-02", 100, 1, "KRW", 1.0, fee=0)]
        _, 경고 = cost_rates(self.수수료_넣음, {}, 지난)
        assert not [줄 for 줄 in 경고 if "증권거래세" in 줄], 경고
        올해 = [pf_.Trade(2, 1, "sell", 시행일, 100, 1, "KRW", 1.0, fee=0)]
        _, 경고 = cost_rates(self.수수료_넣음, {}, 올해)
        말 = [줄 for 줄 in 경고 if "증권거래세" in 줄]
        assert 말 and f"{시행일} 이후" in 말[0], 경고
        # 세금을 입력한 매도는 설정을 쓰지 않는다
        입력 = [pf_.Trade(3, 1, "sell", 시행일, 100, 1, "KRW", 1.0, fee=0, tax=0.2)]
        assert not [줄 for 줄 in cost_rates(self.수수료_넣음, {}, 입력)[1] if "증권거래세" in 줄]

    def test_둘_다_넣으면_조용하다(self) -> None:
        """막기만 하는 코드도 통과하면 안 된다. 거짓 경보가 쌓이면 사람이 경고를 안 읽는다."""
        from batch.jobs.portfolio import cost_rates

        _, 경고 = cost_rates(self.수수료_넣음, {"kr_transaction_pct": 0.18})

        assert 경고 == []

    def test_미국_매매세는_없는_것이지_모르는_것이_아니다(self) -> None:
        """`USD.sell_tax_pct` 는 일부러 None 이다. 이것까지 세면 **늘** 경고가 뜬다."""
        from batch.jobs.portfolio import cost_rates

        rates, 경고 = cost_rates(self.수수료_넣음, {"kr_transaction_pct": 0.18})

        assert rates["USD"].sell_tax_pct is None
        assert 경고 == [], "미국 매매세 없음을 '모른다' 로 세고 있다"

    def test_어느_쪽으로_틀리는지_말한다(self) -> None:
        """"0 으로 계산했다" 만으로는 사용자가 방향을 모른다."""
        from batch.jobs.portfolio import cost_rates

        _, 경고 = cost_rates({}, {})

        for 줄 in 경고:
            assert "좋게 나옵니다" in 줄, f"실현손익이 어느 쪽으로 틀리는지 안 적혀 있다: {줄}"

    def test_판정이_한_곳에_있다(self) -> None:
        """부르는 쪽이 제 눈으로 다시 보면 둘이 갈라진다 (25.174·25.180)."""
        import inspect

        from batch.jobs import portfolio as job

        원본 = inspect.getsource(job.run)

        assert "buy_fee_pct is None" not in 원본, (
            "부르는 쪽이 비용률을 다시 살핀다 — `cost_rates` 가 돌려주는 경고만 쓴다"
        )


def test_보유_평가는_0_이하_종가를_값이_없는_날로_본다() -> None:
    """0 원 종가로 평가하면 평가액 0 → 수익률 −100% → 손절 플래그가 거짓으로 뜬다 (docs/infra.md 25.203)."""
    from batch.core import db

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    for day, close in (("2026-09-14", 100.0), ("2026-09-15", 0.0), ("2026-09-16", 101.0)):
        mem.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (1, ?, ?, 'KRW', 't', 't')",
            [day, close],
        )

    closes = job.load_closes(mem, [1], "2026-09-01")  # type: ignore[arg-type]

    assert closes == {1: {"2026-09-14": 100.0, "2026-09-16": 101.0}}


def test_미국_보유_평가는_배당을_뺀_종가로_한다() -> None:
    """**야후 Adj Close 는 배당까지 반영한다** (docs/infra.md 25.212).

    그 계열로 평가하면 TWR 이 받은 배당을 또 더해 배당이 두 번 들어간다. 야후 `Close` 는 분할만 반영한 값이다.
    국내 수정주가는 분할·감자만 담으므로 그대로 쓴다.
    """
    from batch.core import db

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'NASDAQ', 'US', 'USD', 'active', 't', 't'), (2, 'B', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    for sid in (1, 2):
        mem.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (?, '2026-09-14', 100, 98, 'X', 't', 't')",
            [sid],
        )

    closes = job.load_closes(mem, [1, 2], "2026-09-01")  # type: ignore[arg-type]

    assert closes[1] == {"2026-09-14": 100.0}  # 미국: 배당을 뺀 Close
    assert closes[2] == {"2026-09-14": 98.0}  # 국내: 수정주가


def test_밸류에이션_밴드도_분할만_반영한_가격을_쓴다() -> None:
    """과거 시총 = 가격 × 지금 주식수. 미국 Adj Close 는 배당까지 담아 과거 PBR 을 낮춘다 (docs/infra.md 25.213).

    밴드 가격을 읽는 두 자리(`jobs/valuation_bands`·`jobs/signals.load_band`)가 같은 식을 쓰는지 함께 본다.
    """
    from batch.core import db
    from batch.jobs import signals as sj
    from batch.jobs import valuation_bands as vbj

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'NASDAQ', 'US', 'USD', 'active', 't', 't'), (2, 'B', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    for sid in (1, 2):
        mem.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (?, '2026-09-14', 100, 98, 'X', 't', 't')",
            [sid],
        )

    가격 = vbj.load_prices(mem, [1, 2], "2026-09-16")  # type: ignore[arg-type]
    assert 가격 == {1: [("2026-09-14", 100.0)], 2: [("2026-09-14", 98.0)]}

    # load_band 는 실제로 PBR 계열에 어떤 가격을 넘기는가 — 글자가 아니라 넘긴 값을 본다 (25.199 의 교훈)
    mem.conn.execute(
        "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
        " receipt_no, currency, unit, total_equity, source, fetched_at)"
        " VALUES (1, 2025, ?, 'A', 1, '2026-03-01', 'r1', 'USD', 'USD', 50000, 't', 't')",
        [sj.ANNUAL_REPORT_CODE],
    )
    넘긴것: list = []
    원래 = sj.sg.pbr_series
    sj.sg.pbr_series = lambda prices, equities, shares: (넘긴것.append(prices), 원래(prices, equities, shares))[1]  # type: ignore[assignment]
    try:
        sj.load_band(mem, 1, "2026-09-16", 1_000)  # type: ignore[arg-type]
    finally:
        sj.sg.pbr_series = 원래  # type: ignore[assignment]
    assert 넘긴것 == [[("2026-09-14", 100.0)]]


class Test업종_상한은_설정을_따른다:
    """화면이 30 을 글자로 박아 "상한을 넘는 업종" 을 골랐다 (docs/infra.md 25.217)."""

    def _업종(self, client: MemClient) -> dict:
        job.run()
        return json.loads(client.conn.execute("SELECT allocation_json FROM portfolio_summary").fetchone()[0])

    def test_미끼__기본_30_이면_77퍼센트_업종이_넘는다(self, client: MemClient) -> None:
        alloc = self._업종(client)
        assert alloc["max_sector_pct"] == 30.0
        assert alloc["sectors_over_cap"] == ["산업기계·컴퓨터"]

    def test_설정을_80_으로_올리면_넘는_업종이_없다(self, client: MemClient) -> None:
        client.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('max_weight_per_sector', '80', 't')"
        )
        alloc = self._업종(client)
        assert alloc["max_sector_pct"] == 80.0 and alloc["sectors_over_cap"] == []

    def test_업종_없음_묶음은_업종이_아니다(self) -> None:
        from batch.services import portfolio as pf

        p = pf.Position(stock_id=1, quantity=1, currency="KRW", avg_price=1, avg_fx=1, cost=1, cost_krw=1,
                        first_buy_date="2026-01-01", horizon=None)  # fmt: skip
        p.market_value_krw = 100.0
        alloc = pf.allocation([p], {1: None}, 30.0)
        assert alloc["by_sector"] == {pf.NO_SECTOR: 100.0} and alloc["sectors_over_cap"] == []

    def test_상한은_총_투자가능금액에_대어_본다(self) -> None:
        """docs/infra.md 25.238 — 보유 합으로 나누면 화면과 리포트 2부가 다른 말을 했다."""
        from batch.services import portfolio as pf

        def 보유(sid: int, 값: float) -> pf.Position:
            p = pf.Position(stock_id=sid, quantity=1, currency="KRW", avg_price=1, avg_fx=1, cost=1, cost_krw=1,
                            first_buy_date="2026-01-01", horizon=None)  # fmt: skip
            p.market_value_krw = 값
            return p

        # 투자가능 1억 중 5천만 보유, 반도체 2천만 = 보유의 40% · 투자가능의 20%
        보유들 = [보유(1, 20_000_000), 보유(2, 30_000_000)]
        업종 = {1: "반도체", 2: "은행"}
        alloc = pf.allocation(보유들, 업종, 30.0, total_investable_krw=100_000_000)
        assert alloc["by_sector"]["반도체"] == 40.0  # 구성은 보유 합 대비 그대로
        assert alloc["sectors_over_cap"] == []  # 리포트 2부도 여력 1천만을 본다
        assert alloc["cap_base_krw"] == 100_000_000

        # 투자가능 설정이 없으면 보유 합으로 — 예전 동작
        assert set(pf.allocation(보유들, 업종, 30.0)["sectors_over_cap"]) == {"은행", "반도체"}

        # 보유가 설정보다 커졌으면 보유 합으로 나눈다 (낡은 설정으로 나누면 100% 를 넘는다)
        assert pf.allocation(보유들, 업종, 30.0, total_investable_krw=10_000_000)["cap_base_krw"] == 50_000_000


def test_무위험수익률_0_은_모름이_아니라_0퍼센트다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """종목 성과는 0 을 그대로 쓰는데 포트폴리오만 `if rf` 로 None 을 만들어 샤프가 비었다 (docs/infra.md 25.298)."""
    client.conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES ('risk_free_manual', '{\"kr_pct\": 0}', 't')"
    )
    받은: list[object] = []
    진짜 = job.m.compute

    def 엿보기(*args, **kw):
        받은.append(kw.get("risk_free_annual"))
        return 진짜(*args, **kw)

    monkeypatch.setattr(job.m, "compute", 엿보기)
    assert job.run() == 0
    assert 받은 == [0.0]


def test_평가_못_한_종목은_원가도_따로_센다(client: MemClient) -> None:
    """평가액은 평가된 종목만 더하는데 원가는 전부 더해 둘이 안 맞았다 (docs/infra.md 25.330)."""
    # 미국 종목의 시세를 지운다 — 평가 못 한다
    client.conn.execute("DELETE FROM prices WHERE stock_id = 2")
    assert job.run() == 0
    totals = json.loads(client.conn.execute("SELECT totals_json FROM portfolio_summary").fetchone()[0])
    assert totals["unvalued_count"] == 1
    assert totals["valued_cost_krw"] == pytest.approx(6 * 70_000)  # 국내만
    assert totals["unvalued_cost_krw"] == pytest.approx(5 * 200.0 * 1380.0)
    assert totals["valued_cost_krw"] + totals["unvalued_cost_krw"] == pytest.approx(totals["cost_krw"])


def test_성과지표는_주말_행을_세지_않는다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """토요일 날짜의 매매가 수익률 0 인 행을 더해 표본을 부풀렸다 (docs/infra.md 25.332)."""
    client.conn.execute(
        "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " fee, tax, horizon, created_at, updated_at) VALUES (9, 1, 'buy', '2026-09-19', 75000, 1, 'KRW', 1, 'manual',"
        " 0, 0, 'long', 't', 't')"
    )  # 2026-09-19 은 토요일
    받은: list[list] = []
    진짜 = job.m.compute

    def 엿보기(window, points, **kw):
        받은.append([p.date for p in points])
        return 진짜(window, points, **kw)

    monkeypatch.setattr(job.m, "compute", 엿보기)
    assert job.run() == 0
    assert 받은 and all(d.weekday() < 5 for d in 받은[0])


def test_쓰다_끊기면_요약이_계산_중으로_남는다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """나눠 쓰다 가운데서 끊기면 반쪽 표가 옛 지문으로 "최신" 이었다 (docs/infra.md 25.644, 감사)."""
    assert job.run() == 0
    진짜 = client.batch

    def 끊기는(statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        앞 = [s for s in statements if not s[0].startswith("INSERT INTO portfolio_summary")]
        진짜(앞)
        raise RuntimeError("쓰기 한도")

    monkeypatch.setattr(client, "batch", 끊기는)
    from batch.core import db

    try:
        with pytest.raises(RuntimeError, match="쓰기 한도"):
            job.run()
    finally:
        db._열린_실행.clear()  # 운영에서는 daily 의 `_하위작업` 이 닫는다
    지문 = client.conn.execute("SELECT trades_version FROM portfolio_summary WHERE id = 1").fetchone()[0]
    assert 지문 == job.REBUILDING


def test_0_이하_환율은_없는_날로_본다(client: MemClient) -> None:
    """그날 미국 보유 평가액 0 → TWR −100% 에 갇혔다 (docs/infra.md 25.690, 교차검증 재현)."""
    for 날, 율 in [("2026-09-21", 1390.0), ("2026-09-22", 0.0), ("2026-09-23", -5.0), ("2026-09-24", 1400.0)]:
        client.conn.execute(
            "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 't', 't')", [날, 율]
        )
    읽음 = job.load_fx(client, "2026-09-01")["USD"]  # type: ignore[arg-type]
    assert "2026-09-22" not in 읽음 and "2026-09-23" not in 읽음
    assert 읽음["2026-09-21"] == 1390.0 and 읽음["2026-09-24"] == 1400.0


def test_오늘_처음_산_포트폴리오도_체결가로_평가한다(client: MemClient) -> None:
    """**첫 매매가 오늘이면 평가액이 0원이었다** (2026-10-02 운영, docs/infra.md 25.883).

    종가를 첫 매매일부터만 불러와 그 종목의 종가가 하나도 없었다(오늘 종가는 내일 나온다). `fresher_price` 는
    "종가 아예 없음" 으로 보고 평가를 비웠다 — 운영 출력 "005930 종가 또는 환율이 없어 평가하지 못했습니다 · 평가액 0원".
    """
    c = client.conn
    c.execute("DELETE FROM trades")
    # 9-16 이 마지막 종가. 9-17 에 처음 산다 — 9-17 종가는 아직 없다
    c.execute(
        "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " fee, tax, horizon, created_at, updated_at)"
        " VALUES (9, 1, 'buy', '2026-09-17', 72800, 1000, 'KRW', 1.0, 'manual', 0, 0, 'long', 't', 't')"
    )
    assert job.run() == 0
    mv, price_date = c.execute("SELECT market_value_krw, price_date FROM positions WHERE stock_id = 1").fetchone()
    assert mv == pytest.approx(1000 * 72_800)
    assert price_date == "2026-09-17"
    totals = json.loads(c.execute("SELECT totals_json FROM portfolio_summary").fetchone()[0])
    assert totals["unvalued_count"] == 0


def test_종가가_아예_없는_종목은_여전히_비운다(client: MemClient) -> None:
    """앞 종가를 불러와도 **정말 없는** 종목까지 체결가로 채우지 않는다 (docs/portfolio.md 5절)."""
    c = client.conn
    c.execute("DELETE FROM prices WHERE stock_id = 1")
    assert job.run() == 0
    assert c.execute("SELECT market_value_krw FROM positions WHERE stock_id = 1").fetchone()[0] is None
