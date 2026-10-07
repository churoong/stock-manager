"""미국 수정주가 어긋남 감지·대기열·재수집 예산 (docs/adjust.md 8장)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from batch.jobs import daily
from batch.jobs import refresh_us_adjusted as refresh
from batch.services import adjust_drift as drift
from batch.sources import yfinance_src
from batch.sources.yfinance_src import DailyBar
from tests.test_report_picks import SqliteClient

ROOT = Path(__file__).resolve().parent.parent
S = drift.Sample


class Test감지:
    def test_비율이_같으면_아무것도_없다(self) -> None:
        stored = {1: S("2026-09-01", 100.0, 98.0)}
        incoming = {1: S("2026-09-01", 100.0, 98.0)}
        assert drift.detect(stored, incoming) == []

    def test_배당이_끼면_비율이_바뀐다(self) -> None:
        # 저장: 98/100 = 0.98. 새로: 96.5/100 = 0.965 → 1.5% 차이
        d = drift.detect({1: S("2026-09-01", 100.0, 98.0)}, {1: S("2026-09-01", 100.0, 96.5)})
        assert len(d) == 1 and d[0].stock_id == 1 and d[0].ratio_before == 0.98 and d[0].ratio_after == 0.965

    def test_반올림_잡음은_넘긴다(self) -> None:
        assert drift.detect({1: S("d", 100.0, 98.0)}, {1: S("d", 100.0, 98.2)}) == []  # 0.2%

    def test_수정종가가_없거나_날짜가_다르면_판단하지_않는다(self) -> None:
        assert drift.detect({1: S("d", 100.0, None)}, {1: S("d", 100.0, 90.0)}) == []
        assert drift.detect({1: S("d1", 100.0, 98.0)}, {1: S("d2", 100.0, 90.0)}) == []
        assert drift.detect({}, {1: S("d", 100.0, 90.0)}) == []

    def test_분할은_비율이_같아도_종가로_잡는다(self) -> None:
        """4:1 분할 — 옛 날짜의 종가·수정종가가 함께 ¼ 이 된다. 비율은 그대로다 (docs/infra.md 25.498)."""
        d = drift.detect({1: S("2026-09-01", 400.0, 392.0)}, {1: S("2026-09-01", 100.0, 98.0)})
        assert len(d) == 1 and d[0].ratio_before == pytest.approx(0.98) and d[0].ratio_after == pytest.approx(0.98)
        # 저장 행에 수정종가가 없어도 종가로 잡는다 — 대기열 칸은 NOT NULL 이라 새 비율을 적는다
        d = drift.detect({1: S("2026-09-01", 400.0, None)}, {1: S("2026-09-01", 100.0, 98.0)})
        assert len(d) == 1 and d[0].ratio_before == pytest.approx(0.98)


class Test대기열:
    def _client(self) -> SqliteClient:
        c = SqliteClient()
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
            " VALUES (1, 'AAA', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'AAA'), (2, 'BBB', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'BBB')"
        )
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, '2026-09-01', 100, 98, 'USD', 't', 't'), (2, '2026-09-01', 50, 49, 'USD', 't', 't')"
        )
        return c

    def test_가장_오래된_날짜_하나로_찾아_적는다(self) -> None:
        client = self._client()
        bars = [
            DailyBar("AAA", "2026-09-01", 100.0, adj_close=96.5),  # 어긋남
            DailyBar("AAA", "2026-09-10", 101.0, adj_close=101.0),
            DailyBar("BBB", "2026-09-01", 50.0, adj_close=49.0),  # 같음
            DailyBar("BBB", "2026-09-10", 51.0, adj_close=51.0),  # trade_date 당일은 비교하지 않는다
        ]
        found = daily.find_drift(client, bars, "2026-09-10", {"AAA": 1, "BBB": 2})  # type: ignore[arg-type]
        assert [d.stock_id for d in found] == [1]
        assert daily.queue_drift(client, found, "now") == 1  # type: ignore[arg-type]
        row = client.conn.execute("SELECT stock_id, sample_date, done_at FROM adjust_refresh_queue").fetchone()
        assert row == (1, "2026-09-01", None)
        # 다시 감지돼도 한 행. 끝난 것은 다시 대기 상태가 된다
        client.conn.execute("UPDATE adjust_refresh_queue SET done_at = 't'")
        daily.queue_drift(client, found, "later")  # type: ignore[arg-type]
        assert client.conn.execute("SELECT COUNT(*), MAX(done_at) FROM adjust_refresh_queue").fetchone() == (1, None)
        assert refresh.pending(client) == [(1, "AAA")]  # type: ignore[arg-type]

    def test_감지가_실패해도_리포트에_말한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """감지 자체가 실패하면 로그에만 남았다 (docs/infra.md 25.555, 교차검증)."""
        client = self._client()
        monkeypatch.setattr(daily, "find_drift", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("읽기 실패")))
        말: list[str] = []
        daily._store_us_bars(client, [DailyBar("AAA", "2026-09-10", 101.0, adj_close=101.0)], "2026-09-10",  # type: ignore[arg-type]
                             {"AAA": 1}, "yfinance", notes=말)  # fmt: skip
        assert len(말) == 1 and "감지 실패" in 말[0]

    def test_분할_기록을_대기열보다_먼저_쓴다(self) -> None:
        """대기열만 쓰이고 끊기면 토요일 재수집이 감지를 지워 분할 기록이 영영 빠졌다 (docs/infra.md 25.553, 교차검증)."""
        client = self._client()
        보낸: list[list] = []
        client.batch = lambda stmts: 보낸.append(list(stmts)) or []  # type: ignore[method-assign]
        daily.queue_drift(client, [drift.Drift(1, "2026-09-01", 0.98, 0.98)], "now")  # type: ignore[arg-type]
        assert "settings" in 보낸[0][0][0] and "adjust_refresh_queue" in 보낸[0][1][0]

    def test_대기열을_못_적으면_그_종목_시세를_미룬다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """적지 못한 채 저장하면 표본이 조정 뒤 값으로 덮여 다음 날 다시 감지하지 못했다 (docs/infra.md 25.548)."""
        client = self._client()

        def 실패(*a: object, **k: object) -> int:
            raise RuntimeError("파이프라인 끊김")

        monkeypatch.setattr(daily, "queue_drift", 실패)
        bars = [
            DailyBar("AAA", "2026-09-01", 100.0, adj_close=96.5),  # 어긋남 → 미룸
            DailyBar("AAA", "2026-09-10", 101.0, adj_close=101.0),
            DailyBar("BBB", "2026-09-10", 51.0, adj_close=51.0),
        ]
        말: list[str] = []
        daily._store_us_bars(client, bars, "2026-09-10", {"AAA": 1, "BBB": 2}, "yfinance", notes=말)  # type: ignore[arg-type]
        assert len(말) == 1 and "1종목" in 말[0]  # 리포트에 싣는다 (25.553)
        저장 = client.conn.execute("SELECT stock_id, date, adj_close FROM prices ORDER BY stock_id, date").fetchall()
        assert (1, "2026-09-01", 98.0) in 저장 and (1, "2026-09-10", 101.0) not in 저장
        assert (2, "2026-09-10", 51.0) in 저장

    def test_분할된_종목을_대기열에_올린다(self) -> None:
        """수정종가가 없는 옛 행도 읽어 종가로 견준다 (25.498)."""
        client = self._client()
        client.conn.execute("UPDATE prices SET adj_close = NULL WHERE stock_id = 2")
        bars = [
            DailyBar("AAA", "2026-09-01", 100.0, adj_close=98.0),  # 같음
            DailyBar("BBB", "2026-09-01", 25.0, adj_close=24.5),  # 2:1 분할
        ]
        found = daily.find_drift(client, bars, "2026-09-10", {"AAA": 1, "BBB": 2})  # type: ignore[arg-type]
        assert [d.stock_id for d in found] == [2]


class Test예산:
    def test_예비를_빼고_종목_수를_정한다(self) -> None:
        # 남은 200만 − 예비 100만 = 100만 / 1,300 = 769종목
        assert refresh.affordable(2_000_000, 50) == 50
        assert refresh.affordable(2_000_000, 1000) == 769
        assert refresh.affordable(1_000_000, 10) == 0
        assert refresh.affordable(500_000, 10) == 0


class Test밀린_것을_말한다:
    """**어긋난 줄 알면서 아무 데도 말하지 않았다** (docs/infra.md 25.165).

    감지 사실은 `log.info` 하나에만 남았다. 우리는 Actions 로그를 못 읽는다(25.17).
    그 사이 그 종목의 옛 `adj_close` 는 지금 기준과 어긋나 있고, 모멘텀·성과지표·
    백테스트가 그 값을 읽는다.
    """

    def _대기(self, 감지일: str) -> SqliteClient:
        c = SqliteClient()
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
            " VALUES (1, 'AAA', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'AAA')"
        )
        c.conn.execute(
            "INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
            " VALUES (1, ?, '2026-09-01', 0.98, 0.96)",
            (감지일,),
        )
        return c

    def test_대기열이_비면_조용하다(self) -> None:
        """**빈 것이 정상이다.** 늘 한 줄을 띄우면 그 줄을 아무도 안 읽는다."""
        c = SqliteClient()
        assert refresh.pending_summary(c) == (0, None)  # type: ignore[arg-type]
        assert refresh.pending_note(c, date(2026, 9, 23)) is None  # type: ignore[arg-type]

    def test_한_주_안쪽은_경고가_아니다(self) -> None:
        """월요일에 감지해 토요일에 비우는 것이 정상 흐름이다."""
        c = self._대기("2026-09-20T00:00:00+00:00")
        assert refresh.pending_summary(c)[0] == 1  # type: ignore[arg-type]
        assert refresh.pending_note(c, date(2026, 9, 23)) is None  # type: ignore[arg-type]

    def test_예정된_재수집을_두_번_지나치면_말한다(self) -> None:
        지난날 = (date(2026, 9, 23) - timedelta(days=refresh.MAX_PENDING_DAYS + 1)).isoformat()
        c = self._대기(f"{지난날}T00:00:00+00:00")

        말 = refresh.pending_note(c, date(2026, 9, 23))  # type: ignore[arg-type]
        assert 말 is not None
        assert "1종목" in 말
        assert f"{refresh.MAX_PENDING_DAYS + 1}일 전" in 말
        assert "어긋나 있고" in 말, "왜 나쁜지를 말해야 한다"

    def test_경계는_말하지_않는다(self) -> None:
        딱 = (date(2026, 9, 23) - timedelta(days=refresh.MAX_PENDING_DAYS)).isoformat()
        c = self._대기(f"{딱}T00:00:00+00:00")
        assert refresh.pending_note(c, date(2026, 9, 23)) is None  # type: ignore[arg-type]

    def test_끝난_것은_세지_않는다(self) -> None:
        c = self._대기("2026-08-01T00:00:00+00:00")
        c.conn.execute("UPDATE adjust_refresh_queue SET done_at = 't'")
        assert refresh.pending_summary(c) == (0, None)  # type: ignore[arg-type]

    def test_표가_없으면_조용하다(self) -> None:
        """마이그레이션 전 DB 는 정상이다 (docs/infra.md 25.164)."""

        class 터짐:
            def execute(self, *_a, **_k):
                raise RuntimeError("no such table: adjust_refresh_queue")

        assert refresh.pending_summary(터짐()) == (0, None)  # type: ignore[arg-type]

    def test_다른_실패는_삼키지_않는다(self) -> None:
        class 터짐:
            def execute(self, *_a, **_k):
                raise RuntimeError("blocked: upgrade your plan")

        with pytest.raises(RuntimeError, match="blocked"):
            refresh.pending_summary(터짐())  # type: ignore[arg-type]

    def test_일일_배치가_그_말을_리포트에_싣는다(self) -> None:
        """`log.info` 가 아니라 **사용자가 읽는 자리**에 나와야 한다."""
        글 = (ROOT / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")

        assert "pending_note(" in 글
        assert "warnings.append(밀림)" in 글

    def test_웹과_같은_문턱을_쓴다(self) -> None:
        글 = (ROOT / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
        assert f"ADJUST_QUEUE_STALE_DAYS = {refresh.MAX_PENDING_DAYS}" in 글, (
            "두 곳이 다른 수를 쓰면 화면과 텔레그램이 서로 다른 말을 한다"
        )


class Test분할_재수집:
    """교차검증 반박 셋 (docs/infra.md 25.503)."""

    def _client(self) -> SqliteClient:
        c = Test대기열()._client()
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol)"
            " VALUES (3, 'CCC', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'CCC')"
        )
        return c

    def test_분할은_두_비율을_같게_적는다(self) -> None:
        d = drift.detect({1: S("d", 400.0, 380.0)}, {1: S("d", 100.0, 98.0)})  # 분할과 배당이 함께
        assert d[0].ratio_before == d[0].ratio_after

    def test_분할이_배당보다_먼저다(self) -> None:
        c = self._client()
        c.conn.execute(
            "INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
            " VALUES (1, '2026-09-01', 'd', 0.98, 0.965), (2, '2026-09-02', 'd', 0.97, 0.95),"
            "        (3, '2026-09-20', 'd', 0.99, 0.99)"
        )
        assert [sid for sid, _ in refresh.pending(c)] == [3, 1, 2]  # type: ignore[arg-type]

    def test_가장_오래된_저장_행까지_받는다(self) -> None:
        c = self._client()
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (3, '2020-01-02', 10, 9, 'USD', 't', 't')"
        )
        오늘 = date(2026, 9, 25)
        assert refresh.lookback_for(c, [3], 오늘) == (오늘 - date(2020, 1, 2)).days + 5  # type: ignore[arg-type]
        assert refresh.lookback_for(c, [1], 오늘) == refresh.LOOKBACK_DAYS  # type: ignore[arg-type]
        assert refresh.lookback_for(c, [], 오늘) == refresh.LOOKBACK_DAYS  # type: ignore[arg-type]
        assert refresh.rows_per_stock(2465) > refresh.ROWS_PER_STOCK

    def test_재수집이_끝난_뒤에도_분할_표시와_감지일을_지킨다(self) -> None:
        """done 뒤 첫 배당락이 표시·감지일을 지워 us_shares 가 시총을 부풀렸다 (docs/infra.md 25.531, 교차검증 재현)."""
        from batch.jobs import us_shares

        c = self._client()
        daily.queue_drift(c, [drift.Drift(3, "d", 0.99, 0.99)], "2026-09-10")  # type: ignore[arg-type]  # 분할
        c.conn.execute("UPDATE adjust_refresh_queue SET done_at = '2026-09-12'")  # 토요일 재수집 끝
        daily.queue_drift(c, [drift.Drift(3, "d2", 0.99, 0.97)], "2026-09-20")  # type: ignore[arg-type]  # 배당
        assert us_shares.split_detections(c) == {3: "2026-09-10"}  # type: ignore[arg-type]
        # 새로 분할이 감지되면 그 날로
        c.conn.execute("UPDATE adjust_refresh_queue SET done_at = '2026-09-26'")
        daily.queue_drift(c, [drift.Drift(3, "d3", 0.5, 0.5)], "2026-10-01")  # type: ignore[arg-type]
        assert us_shares.split_detections(c) == {3: "2026-10-01"}  # type: ignore[arg-type]

    def test_대기_중인_분할_표시는_배당_감지가_덮지_않는다(self) -> None:
        """분할 감지 뒤 배당락이 오면 분할 우선이 사라졌다 (docs/infra.md 25.506, 교차검증 재현)."""
        c = self._client()
        c.conn.execute(
            "INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
            " VALUES (1, '2026-09-01', 'd', 0.98, 0.965)"
        )
        daily.queue_drift(c, [drift.Drift(3, "d", 0.99, 0.99)], "2026-09-02")  # type: ignore[arg-type]
        daily.queue_drift(c, [drift.Drift(3, "d2", 0.99, 0.98)], "2026-09-03")  # type: ignore[arg-type]  # 배당
        assert [sid for sid, _ in refresh.pending(c)] == [3, 1]  # type: ignore[arg-type]

    def test_종목마다_자기_구간만_넣는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """한 조각을 가장 긴 길이로 받아도 넣는 것은 그 종목 자기 구간뿐 — 없던 옛 행을 새로 쓰지 않는다 (25.506)."""
        c = self._client()
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (3, '2020-01-02', 10, 9, 'USD', 't', 't')"
        )
        c.conn.execute(
            "INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
            " VALUES (1, 'x', 'd', 0.9, 0.8), (3, 'x', 'd', 0.9, 0.8)"
        )
        c.close = lambda: None  # type: ignore[attr-defined]
        monkeypatch.setattr(refresh, "TursoClient", lambda: c)
        monkeypatch.setattr(refresh.db, "apply_migrations", lambda client: None)
        monkeypatch.setattr(refresh.db, "remaining_write_budget", lambda client: 10**9)
        monkeypatch.setattr(refresh.cal, "previous_session", lambda market: date(2026, 9, 25))
        길이: list[int] = []

        def 야후(symbols: list[str], lookback_days: int = 10, **_: object) -> yfinance_src.FetchResult:
            길이.append(lookback_days)
            return yfinance_src.FetchResult(ok=True, source="yfinance", data=[
                DailyBar(s, d, 50.0, adj_close=49.0) for s in symbols for d in ("2019-06-03", "2024-01-02")
            ])  # fmt: skip

        monkeypatch.setattr(refresh.yfinance_src, "fetch_daily_bars", 야후)
        assert refresh.run() == 0
        assert 길이 == [(date(2026, 9, 25) - date(2020, 1, 2)).days + 5]
        # AAA(1)는 자기 구간(1,300일)만 — 2019 행을 새로 쓰지 않는다. CCC(3)는 2020 까지라 2019 도 쓰지 않는다
        assert c.conn.execute("SELECT COUNT(*) FROM prices WHERE date = '2019-06-03'").fetchone()[0] == 0
        assert c.conn.execute("SELECT COUNT(*) FROM prices WHERE date = '2024-01-02'").fetchone()[0] == 2

    def test_여유_5일은_넣지_않는다(self) -> None:
        """받기는 가장 오래된 행보다 5일 앞부터지만 넣기는 그 행부터 — 재수집마다 5일씩 늘지 않는다 (25.516)."""
        오늘 = date(2026, 9, 25)
        assert refresh._넣을_시작(오늘, (오늘 - date(2020, 1, 2)).days + 5) == "2020-01-02"
        assert refresh._넣을_시작(오늘, refresh.LOOKBACK_DAYS) == (오늘 - timedelta(days=refresh.LOOKBACK_DAYS)).isoformat()
