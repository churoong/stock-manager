"""지난 배당 수집 뒤 새로 편입된 국내 종목의 배당을 매주 받는다 (docs/infra.md 25.1823).

배당은 4·5월에만 그때 유니버스를 받아, 그 뒤 편입 종목은 다음 4월까지 배당수익률이 결측이었다 — 밸류 팩터가 3지표로
재정규화되어 같은 재무의 기존 종목과 점수가 갈렸다.
"""

from __future__ import annotations

from pathlib import Path

from batch.core import db
from batch.jobs import dividends as job
from tests.test_metrics_beta import MemClient

ROOT = Path(__file__).resolve().parent.parent


def _장부() -> MemClient:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    c = m.conn
    for sid in (1, 2, 3, 4):
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, dart_corp_code, source, fetched_at)"
            " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', ?, 't', 't')",
            [sid, f"00000{sid}", f"C{sid}"],
        )

    def 편입(날: str, sid: int) -> None:
        c.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, ?, 1, 'KRW', 't')",
            [날, sid],
        )

    for sid in (1, 2):
        편입("2026-05-01", sid)  # 5월 배당 수집 때 편입
    for sid in (1, 2, 3, 4):
        편입("2026-10-23", sid)  # 3·4 는 그 뒤 편입
    return m


def test_지난_수집_뒤_편입된_종목만_고른다() -> None:
    m = _장부()
    m.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at) VALUES ('dividends', 'KR', 'success', ?)",
        ["2026-05-05T18:41:00+00:00"],
    )
    assert [sid for sid, _ in job.missing_targets(m)] == [3, 4]  # type: ignore[arg-type]
    # 새 편입 실행이 성공하면 다음 주에는 대상이 없다(무배당 종목을 매주 다시 부르지 않는다)
    m.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at)"
        " VALUES ('dividends_missing', 'KR', 'success', '2026-10-25T16:40:00+00:00')"
    )
    assert job.missing_targets(m) == []  # type: ignore[arg-type]
    # 실패한 실행은 세지 않는다
    m.conn.execute("UPDATE batch_runs SET status = 'failed' WHERE job_name = 'dividends_missing'")
    assert [sid for sid, _ in job.missing_targets(m)] == [3, 4]  # type: ignore[arg-type]


def test_지난_수집_기록이_없으면_행이_없는_종목() -> None:
    m = _장부()
    m.conn.execute(
        "INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no, as_of_date, source, fetched_at)"
        " VALUES (1, 2025, 2025, 'r', '2026-03-10', 't', 't')"
    )
    assert [sid for sid, _ in job.missing_targets(m)] == [2, 3, 4]  # type: ignore[arg-type]


def test_재무_수집_뒤_매주_돈다() -> None:
    글 = (ROOT / ".github" / "workflows" / "financials.yml").read_text(encoding="utf-8")
    재무 = 글.index("python -m batch.jobs.financials --years")
    배당 = 글.index("python -m batch.jobs.dividends --only-missing")
    assert 재무 < 배당
    단계 = 글[글.rindex("- name:", 0, 배당) : 배당]
    assert "if: ${{ !inputs.year && !cancelled() }}" in 단계
    assert job.JOB_NAME_MISSING in job.LAST_RUN_SQL and job.JOB_NAME in job.LAST_RUN_SQL


def _스냅샷_더(m: MemClient) -> None:
    for sid in (1, 2):
        m.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES ('2026-10-16', ?, 1, 'KRW', 't')",
            [sid],
        )


def test_유니버스가_배당_수집_뒤에_늦게_돌아도_그_주_편입을_놓치지_않는다() -> None:
    """25.1824 (25.1823 교차검증 [권고]) — 기준은 지난 실행이 실제로 본 스냅샷이다. 시각의 날짜와 견주면 늦게 쓴
    10-23(금) 스냅샷이 10-25(일) 실행 '이전' 으로 보여 3·4 가 다음 4월까지 빠졌다"""
    m = _장부()
    _스냅샷_더(m)
    m.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at, step_log)"
        " VALUES ('dividends_missing', 'KR', 'success', '2026-10-25T16:40:00+00:00', ?)",
        ['{"companies": 0, "snapshot": "2026-10-16"}'],
    )
    assert [sid for sid, _ in job.missing_targets(m)] == [3, 4]  # type: ignore[arg-type]


def test_일부_실패도_기준이고_실패한_회사는_다시_받는다() -> None:
    m = _장부()
    m.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at, step_log)"
        " VALUES ('dividends_missing', 'KR', 'partial', '2026-10-25T16:40:00+00:00', ?)",
        ['{"snapshot": "2026-10-23", "retry_ids": [4]}'],
    )
    assert [sid for sid, _ in job.missing_targets(m)] == [4]  # type: ignore[arg-type]


def test_실행은_새_편입만_부르고_본_스냅샷과_실패를_남긴다(monkeypatch) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    m = _장부()
    m.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at) VALUES ('dividends', 'KR', 'success', ?)",
        ["2026-05-05T18:41:00+00:00"],
    )
    부른: list[str] = []

    def 가짜(corp: str, year: int) -> SimpleNamespace:
        부른.append(corp)
        ok = corp != "C4"
        return SimpleNamespace(ok=ok, error=None if ok else "시간 초과", limit_state="ok", attempts=1, data=[])

    monkeypatch.setattr(job, "TursoClient", lambda: m)
    monkeypatch.setattr(job.dd, "fetch_alot_matter", 가짜)
    monkeypatch.setattr(job, "MIN_INTERVAL", 0)
    assert job.run(latest=2025, only_missing=True) == 0
    assert set(부른) == {"C3", "C4"}  # 기존 편입 1·2 는 부르지 않는다
    이름, 상태, 기록 = m.conn.execute("SELECT job_name, status, step_log FROM batch_runs ORDER BY id DESC").fetchone()
    import json

    기록 = json.loads(기록)
    assert (이름, 상태) == (job.JOB_NAME_MISSING, "partial")
    assert 기록["snapshot"] == "2026-10-23" and 기록["retry_ids"] == [4]
    # 다음 실행은 실패한 4 만 — 무배당(자료 없음) 3 은 다시 부르지 않는다
    부른.clear()
    assert job.run(latest=2025, only_missing=True) == 1  # 다시 받은 4 가 또 전부 실패 — 실패로 닫는다(25.319)
    assert set(부른) == {"C4"}
