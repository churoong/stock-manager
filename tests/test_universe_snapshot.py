"""유니버스 스냅샷이 **멀쩡한 답을 덮지 않는지**, 시험 실행이 정말 안 쓰는지 (docs/infra.md 25.64).

실제 마이그레이션을 올린 메모리 SQLite 에 `build_snapshot` 을 그대로 돌린다.

**왜 이 둘인가.**

1. 뒤의 모든 것이 **최신 스냅샷**을 본다(`db.latest_snapshot_sql()`). 그래서 편입 0 인
   스냅샷이 하나 써지면 그 순간부터 "유니버스가 비어 있다" 가 되어 백필이 멈추고 점수·신호가
   그날 치를 못 낸다. 2026-09-18 에 실제로 그랬다 — 시세를 넣기 전에 유니버스를 돌려
   20일 평균 거래대금을 낼 수 없었고, 전 종목이 "데이터없음" 으로 빠졌다.
   `should_skip` 이 "편입 0 은 돈 것으로 치지 않는다" 로 **다시 돌게** 해 두었지만,
   그것은 다시 시도하게 할 뿐 **덮어쓰는 것을 막지 못한다.**
2. `--dry-run` 이 마스터도 스냅샷도 그대로 쓰면서 실행 기록에만 "dryrun" 이라고 적었다.
   "확인만 해 보자" 로 부른 사람이 D1 하루 예산을 수만 행 쓰고 유니버스까지 바꿨다.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from batch.core import db
from batch.core.turso import ResultSet
from batch.jobs import universe as job

기준일 = "2026-09-18"


class MemClient:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(
            columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid
        )

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in statements]

    def close(self) -> None:
        pass


def 종목넣기(c: sqlite3.Connection, stock_id: int, ticker: str) -> None:
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source,"
        " fetched_at, listed_date, security_group, section_type, share_kind, market_cap)"
        " VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 'active', 't', 't', '2015-01-02',"
        " '주권', '', '보통주', 900000000000)",
        [stock_id, ticker, f"회사{stock_id}"],
    )


def 시세넣기(c: sqlite3.Connection, stock_id: int, 일수: int, 거래대금: int) -> None:
    """`_avg_turnover_map` 이 20 거래일을 요구한다. 그보다 적으면 '데이터없음' 이 된다."""
    from datetime import date, timedelta

    끝 = date.fromisoformat(기준일)
    for i in range(일수):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, value, currency, source, fetched_at)"
            " VALUES (?, ?, 1000, ?, 'KRW', 'krx_openapi', 't')",
            [stock_id, (끝 - timedelta(days=i)).isoformat(), 거래대금],
        )


@pytest.fixture
def client() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for i in (1, 2, 3):
        종목넣기(mem.conn, i, f"00593{i}")
    return mem


def 편입수(mem: MemClient, snapshot_date: str) -> int:
    return int(
        mem.conn.execute(
            "SELECT COUNT(*) FROM universe_members WHERE snapshot_date = ? AND included = 1",
            [snapshot_date],
        ).fetchone()[0]
    )


def test_거래대금이_있으면_편입된다(client: MemClient) -> None:
    for i in (1, 2, 3):
        시세넣기(client.conn, i, 40, 1_000_000_000)

    counts, warnings = job.build_snapshot(client, "KR", 기준일)  # type: ignore[arg-type]

    assert counts["편입"] == 3
    assert 편입수(client, 기준일) == 3


def test_시세가_모자라면_편입_0_이다(client: MemClient) -> None:
    """20 거래일에 못 미치면 판정 자체가 불가능하다 — 그것이 '데이터없음' 이다."""
    for i in (1, 2, 3):
        시세넣기(client.conn, i, 5, 1_000_000_000)

    counts, _ = job.build_snapshot(client, "KR", 기준일)  # type: ignore[arg-type]

    assert counts["편입"] == 0


class Test편입_0_이_멀쩡한_스냅샷을_덮지_않는다:
    def test_지난_스냅샷이_있으면_쓰지_않는다(self, client: MemClient) -> None:
        for i in (1, 2, 3):
            시세넣기(client.conn, i, 40, 1_000_000_000)
        job.build_snapshot(client, "KR", "2026-09-11")  # type: ignore[arg-type]
        client.conn.execute("DELETE FROM prices")  # 시세를 잃은 날을 흉내 낸다

        counts, warnings = job.build_snapshot(client, "KR", 기준일)  # type: ignore[arg-type]

        assert counts["편입"] == 0
        assert 편입수(client, 기준일) == 0, "편입 0 인 스냅샷을 새로 썼다"
        assert 편입수(client, "2026-09-11") == 3, "지난 스냅샷이 남아 있어야 한다"
        assert any("그대로 둡니다" in w for w in warnings)

    def test_뒤_단계가_보는_최신_스냅샷이_살아_있다(self, client: MemClient) -> None:
        """이것이 진짜 노리는 것이다 — `latest_snapshot_sql()` 이 지난 답을 계속 준다."""
        for i in (1, 2, 3):
            시세넣기(client.conn, i, 40, 1_000_000_000)
        job.build_snapshot(client, "KR", "2026-09-11")  # type: ignore[arg-type]
        client.conn.execute("DELETE FROM prices")
        job.build_snapshot(client, "KR", 기준일)  # type: ignore[arg-type]

        assert job.has_members(client, "KR") is True  # type: ignore[arg-type]

    def test_지난_스냅샷이_없으면_그냥_쓴다(self, client: MemClient) -> None:
        """첫 실행에서는 덮을 것이 없다. 제외 사유를 남기는 편이 낫다."""
        시세넣기(client.conn, 1, 5, 1_000_000_000)

        counts, _ = job.build_snapshot(client, "KR", 기준일)  # type: ignore[arg-type]

        assert counts["편입"] == 0
        총 = client.conn.execute(
            "SELECT COUNT(*) FROM universe_members WHERE snapshot_date = ?", [기준일]
        ).fetchone()[0]
        assert 총 == 3, "첫 실행인데 제외 사유조차 남기지 않았다"

    def test_지난_스냅샷도_편입_0_이면_그냥_쓴다(self, client: MemClient) -> None:
        # 지킬 만한 답이 없다. 새 판정을 남기는 편이 낫다
        시세넣기(client.conn, 1, 5, 1_000_000_000)
        job.build_snapshot(client, "KR", "2026-09-11")  # type: ignore[arg-type]

        job.build_snapshot(client, "KR", 기준일)  # type: ignore[arg-type]

        총 = client.conn.execute(
            "SELECT COUNT(*) FROM universe_members WHERE snapshot_date = ?", [기준일]
        ).fetchone()[0]
        assert 총 == 3


class Test시험_실행은_한_줄도_쓰지_않는다:
    def test_스냅샷을_쓰지_않는다(self, client: MemClient) -> None:
        for i in (1, 2, 3):
            시세넣기(client.conn, i, 40, 1_000_000_000)

        counts, warnings = job.build_snapshot(client, "KR", 기준일, dry_run=True)  # type: ignore[arg-type]

        assert counts["편입"] == 3, "판정은 그대로 해서 보여 준다"
        총 = client.conn.execute("SELECT COUNT(*) FROM universe_members").fetchone()[0]
        assert 총 == 0, "시험 실행인데 썼다"
        assert any("쓰지 않았습니다" in w for w in warnings)

    def test_시험_실행은_마스터도_실행_기록도_남기지_않는다(
        self, client: MemClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(job, "TursoClient", lambda: client)
        monkeypatch.setattr(job.cal, "previous_session", lambda _m: __import__("datetime").date.fromisoformat(기준일))

        def 부르면안됨(*_a: object, **_k: object) -> None:
            raise AssertionError("시험 실행이 마스터를 갱신했다 — 그 자체가 쓰기다")

        monkeypatch.setattr(job, "refresh_kr_master", 부르면안됨)
        monkeypatch.setattr(job, "refresh_kr_market_cap", 부르면안됨)
        for i in (1, 2, 3):
            시세넣기(client.conn, i, 40, 1_000_000_000)

        assert job.run("KR", dry_run=True) == 0

        남은것 = client.conn.execute("SELECT COUNT(*) FROM batch_runs").fetchone()[0]
        assert 남은것 == 0, "시험 실행이 실행 기록을 남겼다"
        assert client.conn.execute("SELECT COUNT(*) FROM universe_members").fetchone()[0] == 0


def test_시험_실행_깃발에_설명이_있다() -> None:
    """설명 없는 `--dry-run` 은 무엇을 안 하는지 알려 주지 않는다. 그래서 쓰는 줄 몰랐다."""
    from pathlib import Path

    글 = Path(job.__file__).read_text(encoding="utf-8")
    조각 = 글.split('"--dry-run"', 1)[1].split(")", 1)[0]

    assert "help=" in 조각


def test_미국_스냅샷은_못_본_검사를_말한다(client: MemClient) -> None:
    """**"관리종목 0건" 이 "없다" 로 읽히지 않게 한다** (docs/infra.md 25.129).

    미국 마스터에는 소속부·증권구분 열이 없어 관리종목·거래정지·스팩을 판정하지 못한다.
    그 사실이 어디에도 안 적히면 운영자는 제외 사유 목록을 보고 "깨끗하다" 고 읽는다.
    경고 한 줄이 `batch_runs.step_log` 와 실행 출력에 남는다.
    """
    client.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source,"
        " fetched_at, listed_date, share_kind, market_cap)"
        " VALUES (9, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't', '2015-01-02',"
        " '보통주', 3000000000000)"
    )

    _, warnings = job.build_snapshot(client, "US", 기준일)  # type: ignore[arg-type]

    안본 = [w for w in warnings if "안 본다" in w]
    assert 안본, f"못 본 검사를 말하지 않는다: {warnings}"
    assert "관리종목" in 안본[0] and "거래정지" in 안본[0]


def test_국내에는_그_목록_경고가_붙지_않는다(client: MemClient) -> None:
    """`not_checked_warning`(아예 안 보는 검사 목록)은 국내에 없다.

    **2026-09-23 정정** (docs/infra.md 25.157): 전에 이 검사의 설명은 "국내는 다 본다" 였다.
    국내 관리종목·거래정지는 **소속부 글자 하나**로 보는데 그 열에 그 글자가 오는지는
    확인된 적이 없다. 그래서 "다 본다" 는 말은 여기서 할 수 없다 — 여기서 지키는 것은
    **목록 경고가 나라를 헷갈리지 않는다**는 것뿐이다.
    """
    for i in (1, 2, 3):
        시세넣기(client.conn, i, 40, 1_000_000_000)

    _, warnings = job.build_snapshot(client, "KR", 기준일)  # type: ignore[arg-type]

    assert not [w for w in warnings if "판정하지 않습니다" in w]


def test_국내는_표시가_정말_걸리는지_스스로_묻는다(client: MemClient) -> None:
    """소속부가 비어 있으면(이 fixture 가 그렇다) 0건이 '없다' 인지 '안 본다' 인지 모른다.

    돌 때 그 사실과 **실제로 본 값**을 함께 말한다 (docs/infra.md 25.157).
    """
    for i in (1, 2, 3):
        시세넣기(client.conn, i, 40, 1_000_000_000)

    _, warnings = job.build_snapshot(client, "KR", 기준일)  # type: ignore[arg-type]

    물음 = [w for w in warnings if "관리종목·거래정지 제외가 0건" in w]
    assert 물음, f"0건인데 아무 말도 안 한다: {warnings}"
    assert "[확인필요]" in 물음[0]


class Test거래대금_창은_시장의_20거래일이다:
    """**빠진 날은 0 으로 센다** (docs/infra.md 25.211).

    적재가 종가 0 행을 버리게 되자(25.203) "그 종목의 최근 20행" 창이 더 옛날로 늘어나, 거래가 없던 날이
    평균에서 빠지고 오래 쉰 종목이 유동성 하한을 통과할 수 있게 됐다.
    """

    def _시장(self, mem: MemClient) -> None:
        # 종목 1: 30일 내내 거래 10억. 시장의 날짜를 만든다
        시세넣기(mem.conn, 1, 30, 1_000_000_000)

    def test_미끼__매일_거래한_종목은_그대로다(self, client: MemClient) -> None:
        self._시장(client)
        assert job._avg_turnover_map(client, "KR", 기준일)[1] == 1_000_000_000  # type: ignore[arg-type]

    def test_몇_종목만_가진_날은_창에_넣지_않는다(self, client: MemClient) -> None:
        """토요일 수정주가 재수집이 대기열 종목에만 금요일 행을 넣으면 나머지 전 종목이 19/20 로 깎였다
        (docs/infra.md 25.1109, 유니버스 감사 재현 — 500만~526만 달러 종목이 매주 '거래대금미달')."""
        for i in (1, 2, 3):
            시세넣기(client.conn, i, 30, 1_000_000_000)
        client.conn.execute("DELETE FROM prices WHERE stock_id IN (1, 2) AND date = ?", [기준일])  # 3 만 가진 날
        평균 = job._avg_turnover_map(client, "KR", 기준일)  # type: ignore[arg-type]
        assert 평균[1] == 1_000_000_000 and 평균[2] == 1_000_000_000 and 평균[3] == 1_000_000_000

    def test_최근_15일_쉬었으면_그만큼_깎인다(self, client: MemClient) -> None:
        from datetime import date, timedelta

        self._시장(client)
        끝 = date.fromisoformat(기준일)
        # 종목 2: 최근 15일은 행이 없다(쉼). 그 전 30일은 거래 10억
        for i in range(15, 45):
            client.conn.execute(
                "INSERT INTO prices (stock_id, date, close, value, currency, source, fetched_at)"
                " VALUES (2, ?, 1000, 1000000000, 'KRW', 'krx_openapi', 't')",
                [(끝 - timedelta(days=i)).isoformat()],
            )

        평균 = job._avg_turnover_map(client, "KR", 기준일)  # type: ignore[arg-type]

        # 시장의 20일 중 거래한 날은 5일 → 10억 × 5/20
        assert 평균[2] == 250_000_000

    def test_거래대금을_모르는_날은_0이_아니라_뺀다(self, client: MemClient) -> None:
        """야후 폴백 날은 행은 있고 거래대금이 비어 있다 (docs/infra.md 25.518, 감사 재현: 5.2억 → 4.94억 '미달')."""
        from datetime import date, timedelta

        self._시장(client)
        끝 = date.fromisoformat(기준일)
        for i in range(30):
            client.conn.execute(
                "INSERT INTO prices (stock_id, date, close, value, currency, source, fetched_at)"
                " VALUES (2, ?, 1000, ?, 'KRW', ?, 't')",
                [(끝 - timedelta(days=i)).isoformat(), None if i == 0 else 520_000_000,
                 "yfinance" if i == 0 else "krx_openapi"],
            )  # fmt: skip
        assert job._avg_turnover_map(client, "KR", 기준일)[2] == 520_000_000  # type: ignore[arg-type]


def _폐지_준비():
    from batch.core import db
    from batch.jobs import universe as job

    c = MemClient()
    db.apply_migrations(c)  # type: ignore[arg-type]
    for i in (1, 2):
        종목넣기(c.conn, i, f"00000{i}")
        시세넣기(c.conn, i, 25, 10_000_000_000)
    job.build_snapshot(c, "KR", "2026-09-18")  # type: ignore[arg-type]
    c.conn.execute("UPDATE stocks SET status = 'delisted' WHERE id = 2")
    return c, job


def _행(c, day: str):
    return c.conn.execute(
        "SELECT included, exclude_reason FROM universe_members WHERE snapshot_date = ? AND stock_id = 2", [day]
    ).fetchone()


def test_이은_ETF_는_스냅샷에_넣지_않는다() -> None:
    """ETF 가 '데이터없음' 으로 빠진 행이 생겨, 들고 있으면 매일 "유니버스에서 빠졌습니다" 플래그가 섰다 (25.906, 감사)."""
    from batch.core import db
    from batch.jobs import universe as job

    c = MemClient()
    db.apply_migrations(c)  # type: ignore[arg-type]
    종목넣기(c.conn, 1, "000001")
    시세넣기(c.conn, 1, 25, 10_000_000_000)
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, asset_type, source, fetched_at)"
        " VALUES (9, '069500', 'ETF', 'KR', 'KRW', 'active', 'etf', 't', 't')"
    )
    job.build_snapshot(c, "KR", "2026-10-02")  # type: ignore[arg-type]
    assert c.conn.execute("SELECT COUNT(*) FROM universe_members WHERE stock_id = 9").fetchone()[0] == 0
    assert c.conn.execute("SELECT COUNT(*) FROM universe_members WHERE stock_id = 1").fetchone()[0] == 1

def test_마스터에서_빠진_종목은_다음_스냅샷에_사유를_남긴다() -> None:
    """폐지된 종목이 스냅샷에서 사유 없이 사라졌다 (docs/infra.md 25.412)."""
    from batch.services import universe as uni

    c, job = _폐지_준비()
    요약, _ = job.build_snapshot(c, "KR", "2026-09-25")  # type: ignore[arg-type]
    assert 요약[uni.REASON_DELISTED] == 1
    assert _행(c, "2026-09-25") == (0, uni.REASON_DELISTED)
    # 보유하지 않은 폐지 종목은 그다음 주에는 이어 쓰지 않는다 — 폐지 종목 전부가 매주 쌓이지 않게
    job.build_snapshot(c, "KR", "2026-10-02")  # type: ignore[arg-type]
    assert _행(c, "2026-10-02") is None


def test_같은_날_다시_돌리면_옛_편입_행을_고친다() -> None:
    from batch.services import universe as uni

    c, job = _폐지_준비()
    job.build_snapshot(c, "KR", "2026-09-18")  # type: ignore[arg-type]
    assert _행(c, "2026-09-18") == (0, uni.REASON_DELISTED)


def test_보유_중이면_매주_이어_쓴다() -> None:
    from batch.services import universe as uni

    c, job = _폐지_준비()
    c.conn.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " updated_at) VALUES (2, 1, 'KRW', 1, 1, 1, 1, '2026-01-02', 't')"
    )
    job.build_snapshot(c, "KR", "2026-09-25")  # type: ignore[arg-type]
    job.build_snapshot(c, "KR", "2026-10-02")  # type: ignore[arg-type]
    assert _행(c, "2026-10-02") == (0, uni.REASON_DELISTED)


def test_미국_목록에서_사라진_종목은_상장폐지로_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """인수로 폐지된 대형주가 3~4주 편입으로 남았다 (docs/infra.md 25.519, 감사 재현)."""
    from batch.core import db
    from batch.jobs import universe as job
    from batch.sources import nasdaq_symbols as ns
    from batch.sources.yfinance_src import FetchResult

    c = MemClient()
    db.apply_migrations(c)  # type: ignore[arg-type]
    남은 = [f"S{i:02d}" for i in range(20)] + ["AAA"]  # 목록이 잘린 것으로 보지 않게 충분히(25.361 MIN_MASTER_COVERAGE)
    for i, (t, m) in enumerate([(x, "NASDAQ") for x in 남은] + [("GONE", "NASDAQ"), ("MOVE", "NASDAQ")], 1):
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, ?, 'US', 'USD', 'active', 't', 't')",
            [i, t, m],
        )
    받음 = [ns.UsSymbol(t, f"{t} Inc. Common Stock", "NASDAQ", False, False) for t in 남은]
    받음.append(ns.UsSymbol("MOVE", "Move Corp. Common Stock", "NYSE", False, False))  # 거래소 이전
    monkeypatch.setattr(ns, "fetch_symbols", lambda: FetchResult(ok=True, source="nasdaqtrader", data=받음))
    job.refresh_us_master(c)  # type: ignore[arg-type]
    상태 = {t: (m, st) for t, m, st in c.conn.execute("SELECT ticker, market, status FROM stocks").fetchall()}
    assert 상태["GONE"] == ("NASDAQ", "delisted") and 상태["AAA"] == ("NASDAQ", "active")
    # 거래소를 옮긴 종목은 새 행이 아니라 옛 행의 시장이 바뀐다 — 시세 이력이 그 행에 있다 (25.529)
    assert 상태["MOVE"] == ("NYSE", "active")
    assert c.conn.execute("SELECT COUNT(*) FROM stocks WHERE ticker = 'MOVE'").fetchone()[0] == 1



def test_이은_ETF_는_목록에서_빠져도_폐지로_적지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """ETF 가 목록에서 빠지면 `delisted` 가 되어 일일 시세(active 만)가 멈췄다 — `etf_link` 는 다시 보지 않는다 (25.902)."""
    from batch.core import db
    from batch.jobs import universe as job
    from batch.sources import nasdaq_symbols as ns
    from batch.sources.yfinance_src import FetchResult

    c = MemClient()
    db.apply_migrations(c)  # type: ignore[arg-type]
    남은 = [f"S{i:02d}" for i in range(20)]
    for i, t in enumerate(남은, 1):
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'NYSE Arca', 'US', 'USD', 'active', 't', 't')",
            [i, t],
        )
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, asset_type, source, fetched_at)"
        " VALUES (99, 'ETFX', 'NYSE Arca', 'US', 'USD', 'active', 'etf', 't', 't')"
    )
    받음 = [ns.UsSymbol(t, f"{t} Inc. Common Stock", "NYSE Arca", False, False) for t in 남은]
    monkeypatch.setattr(ns, "fetch_symbols", lambda: FetchResult(ok=True, source="nasdaqtrader", data=받음))
    job.refresh_us_master(c)  # type: ignore[arg-type]
    assert c.conn.execute("SELECT status FROM stocks WHERE id = 99").fetchone()[0] == "active"

def test_이전상장은_옛_행의_시장을_바꾼다() -> None:
    """새 행이 생겨 시세 이력이 끊기고 4주 '데이터없음'·1년 '상장1년미만' 이었다 (docs/infra.md 25.529, 감사 재현)."""
    from batch.jobs import universe as job

    기존 = [(1, "XFER", "NYSE"), (2, "BOTH", "NYSE"), (3, "BOTH", "NASDAQ"), (4, "STAY", "NYSE")]
    문장 = job.market_move_statements(기존, {"XFER": "NASDAQ", "BOTH": "NASDAQ", "STAY": "NYSE", "NEW": "NASDAQ"})
    assert 문장 == [("UPDATE stocks SET market = ? WHERE id = ?", ["NASDAQ", 1])]  # 이미 두 행이면 합치지 않는다


def test_아는_날이_하한보다_적으면_평균을_내지_않는다() -> None:
    """20일 중 하루만 알아도 그 하루로 평균을 냈다 (docs/infra.md 25.525, 교차검증)."""
    from datetime import date, timedelta

    from batch.core import db
    from batch.jobs import universe as job

    c = MemClient()
    db.apply_migrations(c)  # type: ignore[arg-type]
    종목넣기(c.conn, 1, "000001")
    종목넣기(c.conn, 2, "000002")
    시세넣기(c.conn, 1, 30, 1_000_000_000)
    끝 = date.fromisoformat(기준일)
    for i in range(30):
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, value, currency, source, fetched_at)"
            " VALUES (2, ?, 1000, ?, 'KRW', 't', 't')",
            [(끝 - timedelta(days=i)).isoformat(), 1 if i == 0 else None],
        )
    assert 2 not in job._avg_turnover_map(c, "KR", 기준일)  # type: ignore[arg-type]


def test_폐지된_행은_다른_시장의_같은_티커로_옮기지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """NYSE 에서 폐지된 옛 회사의 행이 NASDAQ 새 상장(같은 티커)에 합쳐졌다 (docs/infra.md 25.536, 교차검증)."""
    from batch.core import db
    from batch.jobs import universe as job

    c = MemClient()
    db.apply_migrations(c)  # type: ignore[arg-type]
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'REUS', 'NYSE', 'US', 'USD', 'delisted', 't', 't')"
    )
    행 = job._country_rows(c, "US")  # type: ignore[arg-type]
    assert job.market_move_statements(행, {"REUS": "NASDAQ"}) == []


def test_새_시장에_옛_행이_있으면_옮기지_않는다__유니버스가_멈추지_않게() -> None:
    """active 만 보고 막기를 판단해 UNIQUE(ticker, market) 에 걸려 배치가 실패했다 (docs/infra.md 25.541, 교차검증 재현)."""
    from batch.core import db
    from batch.jobs import universe as job

    c = MemClient()
    db.apply_migrations(c)  # type: ignore[arg-type]
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at) VALUES"
        " (1, 'BACK', 'NYSE', 'US', 'USD', 'excluded', 't', 't'), (2, 'BACK', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    문장 = job.market_move_statements(job._country_rows(c, "US"), {"BACK": "NYSE"})  # type: ignore[arg-type]
    assert 문장 == []
    for sql, args in 문장:
        c.conn.execute(sql, args)  # 실행해도 UNIQUE 에 걸리지 않는다


class Test반쪽_스냅샷_25_1110:
    """400행 문장 중간에서 끊기면 반쪽 스냅샷이 최신이 됐다 (docs/infra.md 25.1110, 유니버스 감사 재현)."""

    @staticmethod
    def _행(날짜: str, n: int) -> list[tuple]:
        return [(날짜, i, 1, None, 1, 1, 1, "KRW", "t") for i in range(1, n + 1)]

    def test_새_날짜를_쓰다_끊기면_그_날짜를_지운다(self, client: MemClient, monkeypatch) -> None:
        for i in range(1, 6):
            client.conn.execute(
                "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't') ON CONFLICT DO NOTHING",
                [100 + i, f"T{i}"],
            )
        monkeypatch.setattr(job, "UNIVERSE_ROWS_PER_STATEMENT", 2)
        행 = [(r[0], 100 + r[1], *r[2:]) for r in self._행("2026-10-09", 5)]
        진짜 = client.batch

        def 끊김(stmts):  # 첫 문장만 쓰고 끊긴다(파이프라인은 문장마다 커밋)
            진짜(stmts[:1])
            raise RuntimeError("연결 끊김")

        monkeypatch.setattr(client, "batch", 끊김)
        with pytest.raises(RuntimeError):
            job._bulk_upsert_universe(client, 행)  # type: ignore[arg-type]
        assert client.conn.execute(
            "SELECT COUNT(*) FROM universe_members WHERE snapshot_date = '2026-10-09'").fetchone()[0] == 0
        # 같은 날짜를 다시 쓰던 중이면 지우지 않는다 — 그날 스냅샷이 통째로 사라진다
        monkeypatch.setattr(client, "batch", 진짜)
        job._bulk_upsert_universe(client, 행)  # type: ignore[arg-type]
        monkeypatch.setattr(client, "batch", 끊김)
        with pytest.raises(RuntimeError):
            job._bulk_upsert_universe(client, 행)  # type: ignore[arg-type]
        assert client.conn.execute(
            "SELECT COUNT(*) FROM universe_members WHERE snapshot_date = '2026-10-09'").fetchone()[0] == 5
