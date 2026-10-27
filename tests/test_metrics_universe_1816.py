"""점수 작업의 성과 지표 묵음 경고는 유니버스 종목만 센다 (docs/infra.md 25.1816).

2026-10-27 국내 일일 배치가 "성과 지표 43/980종목이 21일 넘게 묵었습니다(가장 오래된 2026-09-16)" 를 남겼다 — 성과 지표 작업이
전날 손 실행·예약 실행 둘 다 성공한 뒤였다. 지표 작업은 최신 스냅샷 편입 종목만 다시 내고(`metrics.target_stocks`), 점수 작업은
나라 전체 종목의 지표를 읽어 유니버스에서 빠진 종목의 지난 행까지 셌다.
"""

import inspect

from batch.jobs import scores as job
from tests.test_scores_job import _sqlite_client


def _지표(client, sid: int, as_of: str) -> None:
    client.conn.execute(
        "INSERT INTO performance_metrics (stock_id, as_of_date, window, mdd, data_points, calc_version, created_at)"
        " VALUES (?, ?, '1Y', -0.2, 250, 1, 't')",
        [sid, as_of],
    )


def test_유니버스에서_빠진_종목의_묵은_지표는_세지_않는다() -> None:
    client = _sqlite_client()
    client.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (3, 'C', 'KOSDAQ', 'KR', 'KRW', 'active', 't', 't')"
    )
    _지표(client, 1, "2026-10-24")  # 유니버스 종목 — 지난 주말 지표
    _지표(client, 3, "2026-09-16")  # 9월에 유니버스에서 빠져 지표 작업이 더 다시 내지 않는다
    전체 = job.load_metrics(client, "KR", "2026-10-26")  # type: ignore[arg-type]
    assert job.stale_metrics(전체, "2026-10-26") == {"count": 1, "of": 2, "oldest": "2026-09-16"}  # 예전 — 거짓 경고
    유니버스 = job.load_metrics(client, "KR", "2026-10-26", [1])  # type: ignore[arg-type]
    assert set(유니버스) == {1}
    assert job.stale_metrics(유니버스, "2026-10-26") is None
    # 유니버스 종목이 정말 묵으면 그대로 경고한다
    client.conn.execute("DELETE FROM performance_metrics WHERE stock_id = 1")
    _지표(client, 1, "2026-09-20")
    묵음 = job.stale_metrics(job.load_metrics(client, "KR", "2026-10-26", [1]), "2026-10-26")  # type: ignore[arg-type]
    assert 묵음 == {"count": 1, "of": 1, "oldest": "2026-09-20"}


def test_점수_작업은_유니버스_종목의_지표만_읽는다() -> None:
    글 = inspect.getsource(job.run)
    assert 'load_metrics(client, country, as_of, [int(r["stock_id"]) for r in universe])' in 글
    assert "load_metrics(client, country, as_of)\n" not in 글
