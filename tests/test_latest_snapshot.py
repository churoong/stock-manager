"""나라별 최신 유니버스 스냅샷 테스트.

2026-09-17 발견: metrics·scores·signals·financials·backtest 가 나라 구분 없이
전체 MAX(snapshot_date) 를 썼다. 국내 09-16, 미국 09-15 면 미국 편입 종목이
통째로 사라진다. 실제 마이그레이션을 적용한 SQLite 에 돌려 고정한다.
"""

from __future__ import annotations

from batch.jobs import metrics, scores, signals
from tests.test_report_picks import SqliteClient


def seeded() -> SqliteClient:
    client = SqliteClient()
    c = client.conn
    c.executescript(
        """
        INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
        VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't');
        INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)
        VALUES (2, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't');

        -- 국내는 하루 늦게 찍혔다. 전체 MAX 는 09-16 이라 미국 09-15 가 빠진다
        INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)
        VALUES ('2026-09-15', 1, 1, 'KRW', 't'),
               ('2026-09-16', 1, 1, 'KRW', 't'),
               ('2026-09-15', 2, 1, 'USD', 't');
        """
    )
    return client


def test_점수_대상에_미국이_남는다() -> None:
    client = seeded()
    assert [r["ticker"] for r in scores.load_universe(client, "US", "2026-09-16")] == ["AAPL"]  # type: ignore[arg-type]
    assert [r["ticker"] for r in scores.load_universe(client, "KR", "2026-09-16")] == ["005930"]  # type: ignore[arg-type]


def test_기준일_뒤의_스냅샷은_안_쓴다() -> None:
    """**그때는 알 수 없던 편입 여부와 시가총액**을 쓰지 않는다 (docs/infra.md 25.97)."""
    client = seeded()

    # 09-15 기준으로 보면 국내도 09-15 스냅샷이어야 한다 (09-16 은 아직 없던 것이다)
    rs = client.conn.execute(
        "SELECT u.snapshot_date FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
        " WHERE s.country = 'KR' AND u.snapshot_date <= '2026-09-15'"
    ).fetchall()
    assert [r[0] for r in rs] == ["2026-09-15"], "붙인 자료가 이 시험의 전제와 다르다"

    assert [r["ticker"] for r in scores.load_universe(client, "KR", "2026-09-15")] == ["005930"]  # type: ignore[arg-type]


def test_스냅샷보다_앞선_기준일이면_비어_있다() -> None:
    """**없는 것을 지어내지 않는다.** 모르는 때의 유니버스는 빈 것이다."""
    client = seeded()

    assert scores.load_universe(client, "KR", "2026-09-14") == []  # type: ignore[arg-type]


def test_신호_후보에_미국이_남는다() -> None:
    client = seeded()
    rows = signals.load_candidates(client, "US", "2026-09-16")  # type: ignore[arg-type]
    assert [r["ticker"] for r in rows] == ["AAPL"]


def test_성과지표_대상은_나라마다_최신_스냅샷() -> None:
    client = seeded()
    targets = metrics.target_stocks(client)  # type: ignore[arg-type]
    assert sorted(t[1] for t in targets) == ["005930", "AAPL"]
    # 국내가 두 스냅샷에 모두 있어도 한 번만 나와야 한다
    assert len(targets) == 2
