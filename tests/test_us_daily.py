"""미국 일일 시세 수집 테스트.

Step 1 에서 미국은 AAPL 한 종목만 받았다. 그 상태로는 20 거래일이 쌓이지
않아 미국 유니버스의 거래대금 판정이 통째로 비었고, 성과 지표도 스코어도
미국에는 낼 수 없었다. 전종목 수집으로 넓히면서 그 경로를 고정한다.

네트워크를 타지 않는다. 오프라인 픽스처만 읽는다.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from batch.core.turso import ResultSet


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch):
    """오프라인 모드로 모듈을 다시 읽는다."""
    monkeypatch.setenv("OFFLINE_MODE", "1")
    from batch import config

    importlib.reload(config)
    from batch.sources import yfinance_src

    importlib.reload(yfinance_src)
    return yfinance_src


# ----------------------------------------------------------------------
# 요청 수 계산
# ----------------------------------------------------------------------


class Test조각수:
    """호출 수는 조각 수다. 어림해 적어 두면 조각 크기를 바꿀 때 어긋난다."""

    def test_딱_떨어질_때(self) -> None:
        from batch.sources import yfinance_src as y

        assert y.chunk_count(400, 200) == 2

    def test_나머지가_있으면_한_번_더(self) -> None:
        from batch.sources import yfinance_src as y

        assert y.chunk_count(401, 200) == 3
        assert y.chunk_count(1, 200) == 1

    def test_대상이_없으면_부르지_않는다(self) -> None:
        from batch.sources import yfinance_src as y

        assert y.chunk_count(0, 200) == 0

    def test_기본값으로도_센다(self) -> None:
        from batch.sources import yfinance_src as y

        # 5,000종목이면 기본 조각 크기로 25번 부른다
        assert y.chunk_count(5000) == 25
        assert y.CHUNK_SYMBOLS == 200

    def test_조각_크기가_0이면_거부한다(self) -> None:
        from batch.sources import yfinance_src as y

        with pytest.raises(ValueError):
            y.chunk_count(100, 0)


# ----------------------------------------------------------------------
# 거래대금 추정
# ----------------------------------------------------------------------


class Test거래대금추정:
    """야후는 거래대금을 주지 않아 종가 × 거래량으로 갈음한다."""

    def test_종가_곱하기_거래량(self) -> None:
        from batch.sources import yfinance_src as y

        # 220달러 × 300만주 = 6억 6천만 달러
        assert y.estimate_turnover(220.0, 3_000_000) == 660_000_000

    def test_거래량이_없으면_값을_만들지_않는다(self) -> None:
        from batch.sources import yfinance_src as y

        assert y.estimate_turnover(220.0, None) is None

    def test_종가가_없으면_값을_만들지_않는다(self) -> None:
        from batch.sources import yfinance_src as y

        assert y.estimate_turnover(None, 3_000_000) is None

    def test_소수점은_반올림한다(self) -> None:
        from batch.sources import yfinance_src as y

        # 12.5 × 3 = 37.5 → 38. 정수 컬럼에 넣으므로 자르지 않고 반올림한다
        assert y.estimate_turnover(12.5, 3) == 38

    def test_거래량_0은_0이지_결측이_아니다(self) -> None:
        from batch.sources import yfinance_src as y

        # 거래가 없던 날과 거래량을 모르는 날은 다르다
        assert y.estimate_turnover(220.0, 0) == 0


# ----------------------------------------------------------------------
# 오프라인 일봉 수집
# ----------------------------------------------------------------------


class Test오프라인일봉:
    def test_요청한_심볼만_돌려준다(self, offline) -> None:
        result = offline.fetch_daily_bars(["AAPL"])

        assert result.ok
        assert result.from_cache
        assert {bar.ticker for bar in result.data} == {"AAPL"}

    def test_창_안의_모든_거래일을_돌려준다(self, offline) -> None:
        """하루치만 받으면 실패한 날이 영영 빈다."""
        result = offline.fetch_daily_bars(["AAPL"])

        assert sorted(bar.date for bar in result.data) == [
            "2026-09-11",
            "2026-09-14",
            "2026-09-15",
        ]

    def test_여러_종목을_한_번에_받는다(self, offline) -> None:
        result = offline.fetch_daily_bars(["AAPL", "MSFT"])

        assert {bar.ticker for bar in result.data} == {"AAPL", "MSFT"}

    def test_대상이_없으면_부르지_않는다(self, offline) -> None:
        result = offline.fetch_daily_bars([])

        assert not result.ok
        assert "심볼" in result.error

    def test_조각_크기가_0이면_거부한다(self, offline) -> None:
        with pytest.raises(ValueError):
            offline.fetch_daily_bars(["AAPL"], chunk_size=0)


# ----------------------------------------------------------------------
# 적재
# ----------------------------------------------------------------------


class FakeClient:
    """정해진 답만 돌려주는 가짜 클라이언트. 네트워크를 타지 않는다."""

    def __init__(self, symbols: dict[str, int] | None = None) -> None:
        self.symbols = symbols if symbols is not None else {}
        self.batched: list[list[tuple[str, list]]] = []
        self.executed: list[tuple[str, list]] = []

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        self.executed.append((sql, args or []))

        if "FROM stocks" in sql and "yahoo_symbol, id" in sql:
            return ResultSet(
                columns=["yahoo_symbol", "id"],
                rows=[(sym, sid) for sym, sid in self.symbols.items()],
            )
        if "FROM api_usage" in sql:
            return ResultSet(
                columns=["call_count", "limit_value", "warn_at_pct"],
                rows=[(1, None, 80)],
            )
        if "FROM prices p JOIN stocks s" in sql:
            return ResultSet(
                columns=["close", "volume", "currency", "source", "date"],
                rows=[(220.0, 3_000_000, "USD", "yfinance", "2026-09-15")],
            )
        return ResultSet(columns=[], rows=[])

    def batch(self, statements):
        self.batched.append(statements)
        return []


def sample_bars():
    from batch.sources.yfinance_src import DailyBar

    return [
        DailyBar(ticker="AAPL", date="2026-09-14", close=210.0, volume=2_000_000),
        DailyBar(ticker="AAPL", date="2026-09-15", close=220.0, volume=3_000_000),
        DailyBar(ticker="MSFT", date="2026-09-14", close=400.0, volume=500_000),
        DailyBar(ticker="ZZZZ", date="2026-09-14", close=1.0, volume=100),
    ]


class Test적재:
    def test_마스터에_없는_종목은_건너뛴다(self) -> None:
        from batch.jobs import daily

        client = FakeClient()
        stored, tickers, unsettled = daily._store_us_bars(
            client, sample_bars(), "2026-09-15", {"AAPL": 1, "MSFT": 2}, "yfinance"
        )

        # ZZZZ 는 마스터에 없다. 저장하면 어느 종목의 시세인지 알 수 없다
        assert stored == 3
        assert tickers == 2
        assert unsettled == 0

    def test_확정_거래일_이후는_저장하지_않는다(self) -> None:
        """장중에 수동 실행하면 진행 중인 가격이 넘어온다."""
        from batch.jobs import daily

        client = FakeClient()
        stored, tickers, unsettled = daily._store_us_bars(
            client, sample_bars(), "2026-09-14", {"AAPL": 1, "MSFT": 2}, "yfinance"
        )

        # 09-15 AAPL 한 행이 아직 종가가 아니다
        assert stored == 2
        assert unsettled == 1

    def test_거래대금을_추정해_함께_넣는다(self) -> None:
        from batch.jobs import daily

        client = FakeClient()
        daily._store_us_bars(
            client, sample_bars()[:1], "2026-09-15", {"AAPL": 1}, "yfinance"
        )

        _sql, args = client.batched[0][0]
        # 인덱스를 숫자로 적지 않는다. 열이 끼어들면 전부 밀린다
        from batch.core.db import price_column_index as col

        assert args[col("close")] == 210.0
        assert args[col("volume")] == 2_000_000
        assert args[col("value")] == 210.0 * 2_000_000
        assert args[col("currency")] == "USD"

    def test_거래량이_없으면_거래대금을_비운다(self) -> None:
        from batch.jobs import daily
        from batch.sources.yfinance_src import DailyBar

        client = FakeClient()
        daily._store_us_bars(
            client,
            [DailyBar(ticker="AAPL", date="2026-09-15", close=220.0, volume=None)],
            "2026-09-15",
            {"AAPL": 1},
            "yfinance",
        )

        from batch.core.db import price_column_index as col

        _sql, args = client.batched[0][0]
        assert args[col("value")] is None

    def test_수정주가를_함께_넣는다(self) -> None:
        """야후 Adj Close. 백테스트가 미조정 가격에 왜곡되는 것을 수치로 본 뒤 넣었다."""
        from batch.core.db import price_column_index as col
        from batch.jobs import daily
        from batch.sources.yfinance_src import DailyBar

        client = FakeClient()
        daily._store_us_bars(
            client,
            [DailyBar(ticker="AAPL", date="2026-09-15", close=220.0, adj_close=219.4, volume=1)],
            "2026-09-15",
            {"AAPL": 1},
            "yfinance",
        )
        _sql, args = client.batched[0][0]
        assert args[col("adj_close")] == 219.4
        assert args[col("close")] == 220.0

    def test_열_개수가_스키마와_맞는다(self) -> None:
        """값 묶음과 열 개수가 어긋나 배치가 죽은 적이 있다."""
        from batch.core import db
        from batch.jobs import daily

        client = FakeClient()
        daily._store_us_bars(
            client, sample_bars()[:1], "2026-09-15", {"AAPL": 1}, "yfinance"
        )

        _sql, args = client.batched[0][0]
        assert len(args) == db.column_count_of_prices()


class Test수집:
    def test_마스터가_비었으면_무엇을_할지_알려준다(self, offline) -> None:
        from batch.jobs import daily

        client = FakeClient(symbols={})

        with pytest.raises(RuntimeError) as exc:
            daily.collect_us_prices(client, "2026-09-15")

        assert "universe" in str(exc.value)

    def test_저장하고_리포트_행을_만든다(self, offline) -> None:
        from batch.jobs import daily

        client = FakeClient(symbols={"AAPL": 1, "MSFT": 2})
        rows, warnings, summary = daily.collect_us_prices(client, "2026-09-15")

        # 리포트 행은 저장된 값을 다시 읽어 만든다. 수집 결과와 어긋날 수 없다
        assert rows and rows[0]["close"] == 220.0
        assert "종목" in summary
        # 요약은 경고가 아니다. 섞이면 문제가 없어도 실행이 partial 로 남는다
        assert not any("저장" in w for w in warnings)

    def test_호출_수를_조각_수로_센다(self, offline) -> None:
        from batch.jobs import daily

        client = FakeClient(symbols={"AAPL": 1, "MSFT": 2})
        daily.collect_us_prices(client, "2026-09-15")

        inserts = [
            args for sql, args in client.executed if "INSERT INTO api_usage" in sql
        ]
        assert inserts, "호출 수를 세지 않았습니다"
        # 2종목이면 조각은 하나다
        assert inserts[0][2] == 1


# ----------------------------------------------------------------------
# 조각 크기 조정 (2026-09-16 추가)
# ----------------------------------------------------------------------


class Test조각크기설정:
    """조각 수가 곧 요청 수이자 시간이다.

    2026-09-16 실측에서 5,684종목을 200개씩 29조각으로 나눠 16분 40초가
    걸렸다. 야후의 요청당 심볼 상한이 공개돼 있지 않아 기본값은 실측으로
    확인된 200 을 유지하고, 환경변수로만 바꿀 수 있게 했다.
    """

    def test_기본값은_실측으로_확인된_200이다(self, monkeypatch) -> None:
        import importlib

        monkeypatch.delenv("YF_CHUNK_SYMBOLS", raising=False)
        from batch import config

        importlib.reload(config)
        from batch.sources import yfinance_src

        importlib.reload(yfinance_src)
        assert yfinance_src.CHUNK_SYMBOLS == 200

    def test_환경변수로_바꿀_수_있다(self, monkeypatch) -> None:
        """코드를 고치지 않고 한 번 재 보고 결정할 수 있어야 한다."""
        import importlib

        monkeypatch.setenv("YF_CHUNK_SYMBOLS", "500")
        from batch import config

        importlib.reload(config)
        from batch.sources import yfinance_src

        importlib.reload(yfinance_src)
        try:
            assert yfinance_src.CHUNK_SYMBOLS == 500
            # 5,684종목이면 조각이 29개에서 12개로 준다
            assert yfinance_src.chunk_count(5684) == 12
        finally:
            monkeypatch.delenv("YF_CHUNK_SYMBOLS", raising=False)
            importlib.reload(config)
            importlib.reload(yfinance_src)

    def test_빈_값이면_기본값을_쓴다(self, monkeypatch) -> None:
        import importlib

        monkeypatch.setenv("YF_CHUNK_SYMBOLS", "")
        from batch import config

        importlib.reload(config)
        from batch.sources import yfinance_src

        importlib.reload(yfinance_src)
        try:
            assert yfinance_src.CHUNK_SYMBOLS == 200
        finally:
            monkeypatch.delenv("YF_CHUNK_SYMBOLS", raising=False)
            importlib.reload(config)
            importlib.reload(yfinance_src)
