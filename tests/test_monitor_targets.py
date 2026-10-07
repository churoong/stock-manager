"""장중 감시 준비 배치 테스트 (docs/intraday.md). 실제 마이그레이션을 적용한 메모리 SQLite 에 돌린다."""

from __future__ import annotations

import json
from datetime import date

import pytest

from batch.jobs import monitor_targets as job
from tests.test_portfolio_job import MemClient


@pytest.fixture(autouse=True)
def _신호_기준일은_오늘_장(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """이 파일의 고정 신호 날짜(2026-09-16 등)를 "직전 거래일" 로 본다 — 신선도 검사(25.542)는 따로 본다."""
    if "신선도" not in request.node.name:
        monkeypatch.setattr(job, "signal_date_current", lambda *_a: True)


def test_세션은_휴장일을_빼고_UTC_로() -> None:
    # 2026-09-24~26 은 추석 연휴다 [확인필요: exchange_calendars 반영 여부는 라이브러리 버전에 따른다]
    rows = job.session_rows("KR", date(2026, 9, 14), days=6)
    dates = [r[0] for r in rows]
    assert "2026-09-19" not in dates and "2026-09-20" not in dates  # 주말
    first = rows[0]
    assert first[0] == "2026-09-14"
    assert first[1].startswith("2026-09-14T00:00:00")  # 09:00 KST
    assert first[2].startswith("2026-09-14T06:30:00")  # 15:30 KST


def test_수능일은_한_시간_늦게_열고_닫는다() -> None:
    """라이브러리는 수능일을 09:00~15:30 으로 준다 — 마지막 한 시간을 놓치지 않게 (docs/infra.md 25.350)."""
    rows = {r[0]: r for r in job.session_rows("KR", date(2026, 11, 18), days=2)}
    assert rows["2026-11-19"][1].startswith("2026-11-19T01:00:00")  # 10:00 KST
    assert rows["2026-11-19"][2].startswith("2026-11-19T07:30:00")  # 16:30 KST
    assert "수능일" in rows["2026-11-19"][3]
    # 이웃한 날은 그대로
    assert rows["2026-11-18"][1].startswith("2026-11-18T00:00:00")


def test_보유_목표·손절가는_매도_플래그와_같은_문턱() -> None:
    assert job.holding_levels(100.0, "short", None) == pytest.approx((110.0, 93.0))
    assert job.holding_levels(100.0, None, {"long": {"target_pct": 20.0, "stop_pct": -10.0}}) == pytest.approx(
        (120.0, 90.0)
    )


def test_보유와_신호를_합쳐_감시_대상을_만든다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid, ticker, symbol in ((1, "005930", "005930.KS"), (2, "000660", "000660.KS")):
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at,"
            " yahoo_symbol, dart_corp_code) VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 'active', 't', 't', ?, ?)",
            [sid, ticker, f"종목{sid}", symbol, f"0000{sid}"],
        )
        # 20일 평균은 거래가 있은 최근 20일이 다 있어야 낸다 (25.635) — 08-28~09-16 스무 날, 100·200 번갈아
        for i in range(20):
            c.execute(
                "INSERT INTO prices (stock_id, date, close, volume, currency, source, fetched_at)"
                " VALUES (?, date('2026-08-28', ?), ?, ?, 'KRW', 't', 't')",
                [sid, f"+{i} days", 1001 if i == 19 else 1000, 100 if i % 2 == 0 else 200],
            )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, updated_at) VALUES (1, 10, 'KRW', 1000, 1, 10000, 10000, '2026-01-02', 'mid', 't')"
    )
    for sid, horizon, low, high in ((1, "long", 950, 990), (2, "short", 980, 1000), (2, "mid", 970, 995)):
        c.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
            " tranche_plan, target_price, stop_price, size_reduction, rationale_text, rationale_data, calc_version,"
            " created_at) VALUES (?, '2026-09-16', ?, 'x', ?, ?, 'KRW', '[]', 1200, 900, 1, 'x', '{}', 1, 't')",
            [sid, horizon, low, high],
        )

    assert job.run("KR") == 0
    rows = {
        r[0]: r
        for r in c.execute(
            "SELECT stock_id, reasons, buy_zone_low, buy_zone_high, target_price, stop_price, prev_close,"
            " avg_volume_20d, dart_corp_code FROM monitor_targets ORDER BY stock_id"
        ).fetchall()
    }
    held = rows[1]
    assert json.loads(held[1]) == ["holding", "signal:long"]
    # 보유 종목의 목표·손절은 평균단가 기준(중기 +25%/−15%), 신호 값으로 덮지 않는다
    assert held[4] == pytest.approx(1250.0) and held[5] == pytest.approx(850.0)
    assert held[6] == 1001 and held[7] == pytest.approx(150.0)
    assert held[8] == "00001"  # 보유 국내 종목만 공시 확인
    signal_only = rows[2]
    assert json.loads(signal_only[1]) == ["signal:short", "signal:mid"]
    assert (signal_only[2], signal_only[3]) == (970, 1000)  # 겹치는 구간은 합친다
    assert (signal_only[4], signal_only[5], signal_only[8]) == (1200, 900, None)
    assert c.execute("SELECT COUNT(*) FROM market_sessions WHERE market = 'KR'").fetchone()[0] > 0


def test_묵은_신호는_지금_가격_단위로_옮긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """신호가 묵은 채 5:1 분할이 나면 옛 손절선이 거짓 손절 알림을 낸다 (docs/infra.md 25.218)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (3, '000003', 'KOSPI', 'KR', '종목3', 'KRW', 'active', 't', 't', '000003.KS')"
    )
    # 신호 날(9/16) 원래 종가 1000. 그 뒤 분할로 수정주가가 다시 조정되어 그날이 200 이 됐다
    c.execute(
        "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
        " VALUES (3, '2026-09-16', 1000, 200, 'KRW', 't', 't'), (3, '2026-09-17', 205, 205, 'KRW', 't', 't')"
    )
    for 기준 in ('{"ref_close": 1000}',):
        c.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
            " tranche_plan, target_price, stop_price, size_reduction, rationale_text, rationale_data, calc_version,"
            " created_at) VALUES (3, '2026-09-16', 'mid', 'x', 950, 1000, 'KRW', '[]', 1200, 900, 1, 'x', ?, 1, 't')",
            [기준],
        )

    assert job.run("KR") == 0
    row = c.execute(
        "SELECT buy_zone_low, buy_zone_high, target_price, stop_price FROM monitor_targets WHERE stock_id = 3"
    ).fetchone()

    assert row == pytest.approx((190.0, 200.0, 240.0, 180.0))


class Test뉴스_후보:
    """뉴스 수집 후보를 배치가 미리 고른다 (docs/infra.md 24절).

    웹이 1분마다 scores 를 훑던 것을 여기로 옮겼다. 그 질의가 읽기 한도 사고의 원인 하나였다.
    """

    def _client(self):
        from batch.core import db
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        c = mem.conn
        for sid, score in ((1, 90.0), (2, 70.0), (3, None)):
            c.execute(
                "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
                [sid, f"00000{sid}"],
            )
            c.execute(
                "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                " weights_json, calc_version, created_at) VALUES (?, '2026-09-17', ?, '{}', 0, '{}', 1, 't')",
                [sid, score],
            )
        # 미국 종목은 국내 후보에 섞이지 않아야 한다
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (9, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
        )
        c.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
            " weights_json, calc_version, created_at) VALUES (9, '2026-09-17', 99, '{}', 0, '{}', 1, 't')"
        )
        return mem

    def test_점수_높은_순서로_고르고_점수_없는_종목은_뺀다(self) -> None:
        mem = self._client()
        rows = job.news_target_rows(mem, "KR", [], "t")  # type: ignore[arg-type]
        assert [(r[1], r[2], r[3]) for r in rows] == [(1, 1, "score"), (2, 2, "score")]

    def test_감시_대상은_점수가_없어도_넣는다(self) -> None:
        mem = self._client()
        monitor = [("KR", 3, "005930.KS")]
        rows = job.news_target_rows(mem, "KR", monitor, "t")  # type: ignore[arg-type]
        assert (3, None, "monitor") in [(r[1], r[2], r[3]) for r in rows]

    def test_같은_종목을_두_번_넣지_않는다(self) -> None:
        mem = self._client()
        rows = job.news_target_rows(mem, "KR", [("KR", 1, "x")], "t")  # type: ignore[arg-type]
        assert [r[1] for r in rows].count(1) == 1

    def test_설정으로_상위_N_을_줄인다(self) -> None:
        from batch.core import db

        mem = self._client()
        db.set_setting(mem, "sentiment_target_top_n", 1)  # type: ignore[arg-type]
        rows = job.news_target_rows(mem, "KR", [], "t")  # type: ignore[arg-type]
        assert [r[1] for r in rows] == [1]

    def test_0_은_점수_상위를_고르지_않는다__웹과_같은_뜻(self) -> None:
        """웹 뉴스 크론은 0 을 '수집 끔' 으로 읽는다. 배치가 0 을 200 으로 바꿔 읽으면 두 곳이 다르다 (25.356)."""
        from batch.core import db

        mem = self._client()
        db.set_setting(mem, "sentiment_target_top_n", 0)  # type: ignore[arg-type]
        assert job.news_target_rows(mem, "KR", [], "t") == []  # type: ignore[arg-type]

    def test_범위_밖이면_기본값으로(self) -> None:
        from batch.core import db

        mem = self._client()
        db.set_setting(mem, "sentiment_target_top_n", 5000)  # type: ignore[arg-type]
        원래 = job.NEWS_TOP_N
        try:
            job.NEWS_TOP_N = 1
            rows = job.news_target_rows(mem, "KR", [], "t")  # type: ignore[arg-type]
        finally:
            job.NEWS_TOP_N = 원래
        assert [r[1] for r in rows] == [1]

    def test_정수가_아니면_기본값으로(self) -> None:
        """0.5 를 int() 로 0(수집 끔)으로 읽어 웹(켬)과 갈렸다 (docs/infra.md 25.629, 감사)."""
        from batch.core import db

        mem = self._client()
        db.set_setting(mem, "sentiment_target_top_n", 0.5)  # type: ignore[arg-type]
        원래 = job.NEWS_TOP_N
        try:
            job.NEWS_TOP_N = 1
            rows = job.news_target_rows(mem, "KR", [], "t")  # type: ignore[arg-type]
        finally:
            job.NEWS_TOP_N = 원래
        assert [r[1] for r in rows] == [1]


class Test달력을_비운_채로_덮어쓰지_않는다:
    """**지우고 못 채우면 장중 감시가 조용히 멈춘다** (docs/infra.md 25.189).

    `run()` 의 첫 문장이 오늘 이후 세션을 **지우고** 다시 넣는다. 만들지 못했는데도
    그 지우기가 돌면 `market_sessions` 가 빈 채로 남고, 장중 경로는 세션을 못 찾아
    시세를 아예 안 받는다(25.104). 25.166(미국 유니버스를 하루치 실패로 통째로
    비운 일)과 같은 모양이다.
    """

    @staticmethod
    def _달력이_있는_DB(monkeypatch: pytest.MonkeyPatch) -> MemClient:
        from batch.core import db

        mem = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        db.apply_migrations(mem)  # type: ignore[arg-type]
        for day in ("2026-09-24", "2026-09-25", "2026-09-28"):
            mem.conn.execute(
                "INSERT INTO market_sessions (market, date, open_utc, close_utc, source)"
                " VALUES ('KR', ?, ?, ?, '예전 실행')",
                [day, f"{day}T00:00:00+00:00", f"{day}T06:30:00+00:00"],
            )
        return mem

    @staticmethod
    def _남은_세션(mem: MemClient) -> list[str]:
        return [
            str(r[0])
            for r in mem.conn.execute(
                "SELECT date FROM market_sessions WHERE market = 'KR' ORDER BY date"
            ).fetchall()
        ]

    def test_한_줄도_못_만들면_멈춘다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mem = self._달력이_있는_DB(monkeypatch)
        monkeypatch.setattr(job, "session_rows", lambda *a, **k: [])

        with pytest.raises(RuntimeError, match="한 줄도 만들지 못했습니다"):
            job.run("KR")

        assert self._남은_세션(mem) == ["2026-09-24", "2026-09-25", "2026-09-28"], (
            "만들지도 못했으면서 있던 달력을 지웠다 — 장중 감시가 멈춘다"
        )

    def test_왜_멈췄는지_말한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._달력이_있는_DB(monkeypatch)
        monkeypatch.setattr(job, "session_rows", lambda *a, **k: [])

        with pytest.raises(RuntimeError) as 터진것:
            job.run("KR")

        글 = str(터진것.value)
        assert "exchange_calendars" in 글, "어느 쪽이 고장 났는지 말해야 한다"
        assert "장중 감시" in 글, "무엇을 잃는지 말해야 한다"

    def test_멀쩡하면_그대로_다시_채운다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**막는 쪽만 보면 막기만 하는 코드도 통과한다.** 정상 경로가 사는지 함께 본다."""
        mem = self._달력이_있는_DB(monkeypatch)
        monkeypatch.setattr(job, "session_rows", lambda *a, **k: [
            ("2026-10-05", "2026-10-05T00:00:00+00:00", "2026-10-05T06:30:00+00:00", "시험"),
        ])
        monkeypatch.setattr(job.cal, "local_today", lambda _m: date(2026, 9, 25))

        assert job.run("KR") == 0

        # 9/25 이후를 지우고 새로 넣는다. 그 앞(9/24)은 남는다
        assert self._남은_세션(mem) == ["2026-09-24", "2026-10-05"]


class Test기간이_여럿인_신호:
    """docs/infra.md 25.287 — 떨어진 구간을 하나로 합치면 어느 신호에도 없는 가격에서 알림이 나간다."""

    def test_겹치는_구간만_합친다(self) -> None:
        assert job.merge_zones([(980, 1000), (970, 995)], 1001) == (970, 1000)

    def test_떨어진_구간은_전일_종가에_가까운_쪽(self) -> None:
        assert job.merge_zones([(100, 105), (90, 95)], 107) == (100, 105)
        assert job.merge_zones([(100, 105), (90, 95)], 93) == (90, 95)

    def test_전일_종가를_모르면_높은_쪽(self) -> None:
        assert job.merge_zones([(100, 105), (90, 95)], None) == (100, 105)

    def test_빈_목록(self) -> None:
        assert job.merge_zones([], 100) == (None, None)


def test_신호만_있는_종목의_목표·손절은_먼저_닿는_선(monkeypatch: pytest.MonkeyPatch) -> None:
    """단기 목표 1100·손절 930, 장기 목표 1500·손절 750 → 목표 1100, 손절 930 (docs/infra.md 25.287)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (4, '000004', 'KOSPI', 'KR', '종목4', 'KRW', 'active', 't', 't', '000004.KS')"
    )
    c.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
        " VALUES (4, '2026-09-16', 1000, 'KRW', 't', 't')"
    )
    # 장기를 먼저 넣어도 순서에 따라 결과가 바뀌면 안 된다
    for horizon, target, stop in (("long", 1500, 750), ("short", 1100, 930)):
        c.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
            " tranche_plan, target_price, stop_price, size_reduction, rationale_text, rationale_data, calc_version,"
            " created_at) VALUES (4, '2026-09-16', ?, 'x', 950, 1000, 'KRW', '[]', ?, ?, 1, 'x', '{}', 1, 't')",
            [horizon, target, stop],
        )
    assert job.run("KR") == 0
    row = c.execute("SELECT target_price, stop_price FROM monitor_targets WHERE stock_id = 4").fetchone()
    assert row == pytest.approx((1100.0, 930.0))


def test_오늘_신호_0건이면_어제_신호를_지켜보지_않는다() -> None:
    """9/16 에 계산했는데 걸린 신호가 없다(판정표만). 9/15 신호로 매수 구간 알림을 내면 안 된다 (docs/infra.md 25.407)."""
    from batch.core import db

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (2, '000660', 'KOSPI', 'KR', '종목2', 'KRW', 'active', 't', 't', '000660.KS')"
    )
    c.execute(
        "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
        " tranche_plan, target_price, stop_price, size_reduction, rationale_text, rationale_data, calc_version,"
        " created_at) VALUES (2, '2026-09-15', 'short', 'x', 980, 1000, 'KRW', '[]', 1200, 900, 1, 'x', '{}', 1, 't')"
    )
    c.execute(
        "INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
        " calc_version, created_at) VALUES (2, '2026-09-16', 'short', 0, 2, '[]', 1, 't')"
    )

    targets = job.build_targets(mem, "KR", "2026-09-17T00:00:00Z")  # type: ignore[arg-type]
    assert targets == []


def test_신호는_가장_새_판만_읽는다() -> None:
    """판을 올린 날 옛 판 행이 남아 한 기간이 두 번 실리고 옛 목표·손절이 섞였다 (docs/infra.md 25.410)."""
    from batch.core import db
    from batch.jobs import daily

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (2, '000660', 'KOSPI', 'KR', '종목2', 'KRW', 'active', 't', 't', '000660.KS')"
    )
    for 판, 목표, 금액 in ((1, 1100, 5_000_000), (2, 1200, 2_000_000)):
        c.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
            " tranche_plan, target_price, stop_price, size_reduction, suggested_amount, rationale_text,"
            " rationale_data, calc_version, created_at)"
            " VALUES (2, '2026-09-16', 'short', 'x', 980, 1000, 'KRW', '[]', ?, 900, 1, ?, 'x', '{}', ?, 't')",
            [목표, 금액, 판],
        )

    targets = job.build_targets(mem, "KR", "2026-09-17T00:00:00Z")  # type: ignore[arg-type]
    assert len(targets) == 1
    assert json.loads(next(v for v in targets[0] if isinstance(v, str) and v.startswith("["))) == ["signal:short"]

    rows, as_of = daily.load_signal_rows(mem, "KR", upto="2026-09-17")  # type: ignore[arg-type]
    assert as_of == "2026-09-16"
    assert [r.suggested_amount for r in rows] == [2_000_000]


def test_제외된_종목의_신호는_감시하지_않는다() -> None:
    """추천 카드·리포트와 같은 상태 조건 (docs/infra.md 25.802·25.805, 교차검증)."""
    from batch.core import db

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (2, '000660', 'KOSPI', 'KR', '종목2', 'KRW', 'excluded', 't', 't', '000660.KS')"
    )
    c.execute(
        "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
        " tranche_plan, target_price, stop_price, size_reduction, suggested_amount, rationale_text,"
        " rationale_data, calc_version, created_at)"
        " VALUES (2, '2026-09-16', 'short', 'x', 980, 1000, 'KRW', '[]', 1100, 900, 1, 1, 'x', '{}', 1, 't')"
    )
    assert job.build_targets(mem, "KR", "2026-09-17T00:00:00Z") == []  # type: ignore[arg-type]


def test_새_판에서_사라진_기간은_옛_판으로_살아나지_않는다() -> None:
    """판 1 에 단기+중기, 판 2 에 단기만 — 25.410 은 (종목, 기간)마다 최신 판을 골라 판 1 의 중기가 남았다
    (docs/infra.md 25.423, 교차검증)."""
    from batch.core import db
    from batch.jobs import daily
    from batch.jobs import signals as signals_job

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (2, '000660', 'KOSPI', 'KR', '종목2', 'KRW', 'active', 't', 't', '000660.KS')"
    )
    for 판, 기간 in ((1, "short"), (1, "mid"), (2, "short")):
        c.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
            " tranche_plan, target_price, stop_price, size_reduction, suggested_amount, rationale_text,"
            " rationale_data, calc_version, created_at)"
            " VALUES (2, '2026-09-16', ?, 'x', 980, 1000, 'KRW', '[]', 1200, 900, 1, 1000000, 'x', '{}', ?, 't')",
            [기간, 판],
        )
    targets = job.build_targets(mem, "KR", "2026-09-17T00:00:00Z")  # type: ignore[arg-type]
    assert json.loads(next(v for v in targets[0] if isinstance(v, str) and v.startswith("["))) == ["signal:short"]
    rows, _ = daily.load_signal_rows(mem, "KR", upto="2026-09-17")  # type: ignore[arg-type]
    assert [r.horizon for r in rows] == ["short"]

    # 다시 계산할 때는 판과 상관없이 그 나라·그 날 신호를 모두 지운다
    mem.batch([signals_job.clear_statement("KR", "2026-09-16")])
    assert c.execute("SELECT COUNT(*) FROM signals").fetchone() == (0,)


def test_판_하위질의는_바깥_행을_가리키지_않는다() -> None:
    """상관 하위질의라 신호 행마다 그 나라 종목 표를 다시 훑어 읽는 행이 수백 배가 됐다 (docs/infra.md 25.428)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for p in ("batch/jobs/daily.py", "batch/jobs/monitor_targets.py", "web/lib/recommend.ts"):
        assert "s3.country = s.country" not in (root / p).read_text(encoding="utf-8"), p


def test_기업행위_뒤에는_보유_목표·손절과_거래량을_대지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """1:5 분할 뒤 19,800원 시세에 "손절선 93,000원 터치" 가 나갔다 (docs/infra.md 25.533, 감사 재현)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 'active', 't', 't', '005930.KS')"
    )
    for day, close, pct, vol in (("2026-09-15", 100_000, 0.0, 1_000_000), ("2026-09-16", 19_800, -1.0, 4_000_000)):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, change_pct, volume, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, ?, 'KRW', 't', 't')",
            [day, close, pct, vol],
        )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, updated_at) VALUES (1, 10, 'KRW', 100000, 1, 1000000, 1000000, '2026-01-02', 'short', 't')"
    )
    assert job.run("KR") == 0
    reasons, 목표, 손절, 거래량 = c.execute(
        "SELECT reasons, target_price, stop_price, avg_volume_20d FROM monitor_targets WHERE stock_id = 1"
    ).fetchone()
    assert json.loads(reasons) == ["holding", "action_guard"]
    assert 목표 is None and 손절 is None and 거래량 is None
    assert job.recent_action(mem, 1, "KR") is True  # type: ignore[arg-type]


def test_신선도__어제_신호면_신호_종목을_감시에_넣지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """목록은 오늘 만들었는데 내용이 어제 추천이면 웹 신선도 판정(25.534)을 통과했다 (docs/infra.md 25.542, 교차검증)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "expected_signal_date", lambda market, now=None: "2026-09-17")
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (2, '000660', 'KOSPI', 'KR', '나', 'KRW', 'active', 't', 't', '000660.KS')"
    )
    c.execute(
        "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
        " tranche_plan, target_price, stop_price, size_reduction, rationale_text, rationale_data, calc_version,"
        " created_at) VALUES (2, '2026-09-16', 'short', 'x', 980, 1000, 'KRW', '[]', 1200, 900, 1, 'x', '{}', 1, 't')"
    )
    assert job.run("KR") == 0
    assert c.execute("SELECT COUNT(*) FROM monitor_targets WHERE stock_id = 2").fetchone()[0] == 0
    assert job.signal_date_current("2026-09-17", "KR") and not job.signal_date_current("2026-09-16", "KR")


def test_신선도__다음_장_기준으로_직전_거래일을_낸다() -> None:
    """저녁에 만든 목록은 다음 장을 위한 것 — 한 장 묵은 신호가 통과했다 (docs/infra.md 25.547, 교차검증)."""
    from datetime import UTC, datetime

    # 미국 월요일 08:27 ET(장 전) → 그날 장의 직전 거래일은 금요일
    assert job.expected_signal_date("US", datetime(2026, 9, 28, 12, 27, tzinfo=UTC)) == "2026-09-25"
    # 국내 월요일 22:00 KST(장 뒤) → 다음 장(화)의 직전 거래일은 월요일 — 금요일 신호는 통과하지 않는다
    assert job.expected_signal_date("KR", datetime(2026, 9, 28, 13, 0, tzinfo=UTC)) == "2026-09-28"


def test_신선도__준_시각으로만_고른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 시계로 훑기 시작해 `now` 를 준 테스트가 다음 날부터 깨졌다 (docs/infra.md 25.548, 교차검증)."""
    from datetime import UTC, date, datetime

    monkeypatch.setattr(job.cal, "local_today", lambda market: date(2026, 10, 5))
    assert job.expected_signal_date("US", datetime(2026, 9, 28, 12, 27, tzinfo=UTC)) == "2026-09-25"


@pytest.mark.parametrize(("거래일", "기대"), [(20, 100.0), (19, None)])
def test_20일_평균은_거래가_있은_날만_센다(monkeypatch: pytest.MonkeyPatch, 거래일: int, 기대: float | None) -> None:
    """정지일(거래량 0)이 평균을 반으로 깎아 재개 뒤 "20일 평균 N배" 가 거짓으로 났다 (docs/infra.md 25.635, 감사)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 'active', 't', 't', '005930.KS')"
    )
    for i in range(30):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, volume, currency, source, fetched_at)"
            " VALUES (1, date('2026-08-18', ?), 1000, ?, 'KRW', 't', 't')",
            [f"+{i} days", 100 if i < 거래일 else 0],  # 뒤쪽은 거래정지
        )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, updated_at) VALUES (1, 10, 'KRW', 1000, 1, 10000, 10000, '2026-01-02', 'mid', 't')"
    )
    assert job.run("KR") == 0
    (거래량,) = c.execute("SELECT avg_volume_20d FROM monitor_targets WHERE stock_id = 1").fetchone()
    assert 거래량 == 기대


def test_웹_관심종목_평균도_같은_식이다() -> None:
    """배치와 웹 route 가 20일 평균을 따로 낸다 — 한쪽만 고치면 관심 종목만 거짓 알림이 난다 (25.635)."""
    from pathlib import Path

    route = (Path(__file__).parents[1] / "web/app/api/cron/intraday/route.ts").read_text(encoding="utf-8")
    for 조각 in ("volume > 0", "COUNT(*) = 20", "date(MAX(date), '-45 days')"):
        assert 조각 in route, 조각


def test_ETF_보유에는_목표_손절선을_대지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """적립 ETF 가 −25% 에 "손절선 터치" 를 받았다 — ETF 는 타이밍 무관 장기 적립이다 (docs/infra.md 25.908, 감사)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at,"
        " yahoo_symbol, asset_type) VALUES (5, '069500', 'ETF', 'KR', 'KODEX 200', 'KRW', 'active', 't', 't',"
        " '069500.KS', 'etf')"
    )
    for i in range(20):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, volume, currency, source, fetched_at)"
            " VALUES (5, date('2026-08-28', ?), 30000, 100, 'KRW', 't', 't')",
            [f"+{i} days"],
        )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, updated_at) VALUES (5, 10, 'KRW', 40000, 1, 400000, 400000, '2026-01-02', NULL, 't')"
    )
    assert job.run("KR") == 0
    r = c.execute("SELECT reasons, target_price, stop_price FROM monitor_targets WHERE stock_id = 5").fetchone()
    assert json.loads(r[0]) == ["holding"] and r[1] is None and r[2] is None


def test_ETF_보유도_기업행위_표시를_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """보유+관심 ETF 가 분할 뒤 분할 전 관심 목표가로 "도달" 알림을 받을 수 있었다 — 웹이 관심 목표가를 거르는
    표시(action_guard)를 ETF 분기가 먼저 건너뛰었다 (docs/infra.md 25.925, 감사)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at,"
        " yahoo_symbol, asset_type) VALUES (5, '069500', 'ETF', 'KR', 'KODEX 200', 'KRW', 'active', 't', 't',"
        " '069500.KS', 'etf')"
    )
    for i in range(20):
        # 마지막 날 1:2 분할 — 종가가 반이 됐는데 거래소 등락률은 0%
        종가 = 15000 if i == 19 else 30000
        c.execute(
            "INSERT INTO prices (stock_id, date, close, change_pct, volume, currency, source, fetched_at)"
            " VALUES (5, date('2026-08-28', ?), ?, 0, 100, 'KRW', 't', 't')",
            [f"+{i} days", 종가],
        )
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, updated_at) VALUES (5, 10, 'KRW', 40000, 1, 400000, 400000, '2026-01-02', NULL, 't')"
    )
    assert job.run("KR") == 0
    r = c.execute("SELECT reasons, target_price, stop_price FROM monitor_targets WHERE stock_id = 5").fetchone()
    assert json.loads(r[0]) == ["holding", "action_guard"] and r[1] is None and r[2] is None
