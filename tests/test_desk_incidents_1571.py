"""관문 "운영 무사고" — DB 한도로 건너뛴·끝맺지 못한 일일 배치도 센다 (docs/desk.md 5장, docs/infra.md 25.1571)."""

from __future__ import annotations

from batch.core import db
from batch.jobs import desk as jd
from tests.test_portfolio_job import MemClient


def test_한도_건너뜀과_끝맺지_못함은_세고_휴장_거래소_아직은_안_센다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    줄 = [("daily_kr", "failed", "x"), ("daily_us", "skipped", "DB 한도로 건너뜀: Turso 월 읽기 한도"),
          ("daily_kr", "skipped", "끝맺지 못한 기록. 6시간 뒤 정리했다"), ("daily_kr", "skipped", None),
          ("daily_kr", "skipped", "거래소가 아직 전일 시세를 내지 않았습니다"), ("daily_kr", "success", None),
          ("weekly", "skipped", "DB 한도로 건너뜀: x")]  # fmt: skip
    for i, (job, st, err) in enumerate(줄):
        mem.conn.execute("INSERT INTO batch_runs (job_name, market, started_at, status, error_text)"
                         " VALUES (?, 'KR', ?, ?, ?)", [job, f"2026-10-2{i % 9}T00:00:00+00:00", st, err])  # fmt: skip
    n = mem.execute(jd.BATCH_FAILED_SQL, ["daily_kr", "daily_us", "2026-10-01"]).rows[0][0]
    assert n == 3


def test_성과_분해_대조_대기_최신_평가_질의는_표를_훑지_않는다() -> None:
    """운용 감사 [권고] 5 (25.1571) — 결정 원장·평가 표 전체를 날마다 훑던 셋"""
    from batch.jobs import paper as jp

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for sql, args in ((jd.LATEST_SQL, []), (jp.ATTR_DECISIONS_SQL, ["H", "2026-01-01", 1]),
                      (jp.PENDING_CONTROL_SQL, ["KR", 50])):  # fmt: skip
        계획 = [r[-1] for r in mem.conn.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()]
        assert not any(x.startswith(("SCAN paper_decisions", "SCAN d")) for x in 계획), 계획
        assert not any("TEMP B-TREE" in x for x in 계획), 계획


def test_월간_복기는_못_나갔으면_14일까지_따라잡는다() -> None:
    """11-01(일) 요약이 한도로 건너뛰면 10월 복기가 통째로 사라지던 것 (25.1571, 운용 감사 [확인필요] 7)"""
    from datetime import date

    from batch.jobs import paper as jp

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    assert jp.review_due(mem, date(2026, 11, 1)) is True
    assert jp.review_due(mem, date(2026, 11, 8)) is True  # 이번 달 아직 안 실음 — 따라잡기
    assert jp.review_due(mem, date(2026, 11, 15)) is False
    db.set_setting(mem, jp.MONTHLY_REVIEW_KEY, "2026-11")  # type: ignore[arg-type]
    assert jp.review_due(mem, date(2026, 11, 8)) is False  # 이미 실었다
    assert jp.review_due(mem, date(2026, 11, 2)) is True  # 1~7일은 그대로


def test_주간_요약이_월간_복기를_보내면_그_달을_적는다(monkeypatch) -> None:  # noqa: ANN001
    from datetime import UTC, datetime

    from batch.jobs import paper as jp
    from batch.jobs import weekly_summary as ws

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    monkeypatch.setattr(ws.telegram, "send", lambda t, *a, **k: [1])
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (0, 0, []))
    class 고정(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return datetime(2026, 11, 8, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(ws, "datetime", 고정)
    assert ws.run() == 0  # 11-08 KST — 따라잡기
    assert db.get_setting(mem, jp.MONTHLY_REVIEW_KEY, None) == "2026-11"  # type: ignore[arg-type]
