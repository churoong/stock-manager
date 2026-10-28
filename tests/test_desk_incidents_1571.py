"""관문 "운영 무사고" — DB 한도로 건너뛴·끝맺지 못한 일일 배치도 센다 (docs/desk.md 5장, docs/infra.md 25.1571)."""

from __future__ import annotations

from batch.core import db
from batch.jobs import desk as jd
from batch.services import desk
from tests.test_portfolio_job import MemClient


def test_사고_날은_성공_기록이_없거나_실패가_있던_거래일() -> None:
    """한도로 첫 읽기에서 깨지면 실행 기록 행조차 없다 — 행이 아니라 거래일로 센다 (25.1572, 25.1571 반박 [필수])"""
    from batch.services import desk

    기대 = {"daily_kr": ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"], "daily_us": ["2026-10-15"]}
    줄 = [{"job_name": "daily_kr", "trade_date": "2026-10-14", "status": "success"},
          {"job_name": "daily_kr", "trade_date": "2026-10-15", "status": "failed"},
          {"job_name": "daily_kr", "trade_date": "2026-10-15", "status": "success"},  # 실패 뒤 성공 — 그래도 사고
          {"job_name": "daily_kr", "trade_date": "2026-10-16", "status": "skipped"},  # 한도 건너뜀 — 성공 없음
          # 10-19 는 행이 아예 없다(한도로 첫 읽기에서 깨짐)
          {"job_name": "daily_us", "trade_date": "2026-10-15", "status": "partial"}]  # fmt: skip
    assert desk.batch_incident_days(줄, 기대) == 3


def test_다뤘어야_할_거래일은_장날마다_직전_거래일_오늘은_뺀다() -> None:
    from datetime import UTC, datetime

    # 10-20(화) 08:30 KST — 장날 10-13(화)~10-19(월): 다룬 거래일은 그 직전 거래일들, 오늘(10-20) 장날은 뺀다
    날들 = jd._expected_trade_dates("KR", "2026-10-13", datetime(2026, 10, 19, 23, 30, tzinfo=UTC))
    assert 날들 == ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


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
    # 절이 깨져 오류 한 줄만 나가면 표시하지 않는다(25.1572, 25.1571 반박 [권고] 4) — 다음 주가 따라잡는다
    def 깨짐(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("한도")

    monkeypatch.setattr(jp, "summary_section", 깨짐)
    assert ws.run(force=True) == 0
    assert db.get_setting(mem, jp.MONTHLY_REVIEW_KEY, None) is None  # type: ignore[arg-type]
    # 월간 복기를 실어 보내면 그 달을 적는다
    monkeypatch.setattr(jp, "summary_section", lambda *_a, **_k: ["[가상모의투자 월간 복기] 2026-10-01 부터"])
    assert ws.run(force=True) == 0  # 11-08 KST — 따라잡기
    assert db.get_setting(mem, jp.MONTHLY_REVIEW_KEY, None) == "2026-11"  # type: ignore[arg-type]


def test_가상모의투자_단계가_못_돈_실행은_성공으로_세지_않는다() -> None:
    """25.1636(36회차 P3) — 단계가 예외·잠금으로 끝나도 실행은 partial 이라 무사고로 셌다."""
    from batch.services import desk

    기대 = {"daily_kr": ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]}
    줄 = [{"job_name": "daily_kr", "trade_date": "2026-10-14", "status": "partial", "paper_error": "boom"},
          {"job_name": "daily_kr", "trade_date": "2026-10-15", "status": "partial",
           "paper_skipped": "다른 시장의 가상모의투자 단계가 도는 중(잠금)"},
          # 잠금으로 건너뛴 뒤 같은 거래일 손 실행이 끝까지 돌았다 — 사고 아님
          {"job_name": "daily_kr", "trade_date": "2026-10-16", "status": "partial",
           "paper_skipped": "다른 시장의 가상모의투자 단계가 도는 중(잠금)"},
          {"job_name": "daily_kr", "trade_date": "2026-10-16", "status": "success"},
          {"job_name": "daily_kr", "trade_date": "2026-10-19", "status": "success", "paper_skipped": "입금 없음"},
          {"job_name": "daily_kr", "trade_date": "2026-10-20", "status": "success"}]  # paper 칸 없음 — 모름, 세지 않음
    assert desk.batch_incident_days(줄, 기대) == 2
    # 메시지가 빈 예외(`StopIteration()`)도 사고 — 교차검증 [필수]
    assert desk.paper_step_failed({"paper_error": ""}) and not desk.paper_step_failed({"paper_error": None})


def test_실행_기록_질의는_깨진_step_log_에도_돈다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for i, (log, st) in enumerate((("not json", "success"), ('{"paper": {"error": "x"}}', "partial"), (None, "success"),
                                   ('{"paper": {"skipped": "입금 없음", "accounts": {"H": {"buy": {"skipped": "y"}}}}}',
                                    "success"),
                                   ('{"paper": {"account_errors": {"S": "KeyError: 1"}}}', "partial"))):  # fmt: skip
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, trade_date, status, started_at, step_log) VALUES (?, ?, ?, ?, ?)",
            ["daily_kr", f"2026-10-1{i}", st, f"2026-10-1{i}T00:00:00+00:00", log])
    줄 = [dict(zip(("job_name", "trade_date", "status", "paper_error", "paper_skipped", "paper_account_errors"), r,
                   strict=True))
          for r in mem.conn.execute(jd.BATCH_RUNS_SQL, ["daily_kr", "daily_us", "2026-01-01"]).fetchall()]
    줄.sort(key=lambda r: r["trade_date"])  # 질의에 순서가 없다
    assert [r["paper_error"] for r in 줄] == [None, "x", None, None, None]
    assert [r["paper_skipped"] for r in 줄] == [None, None, None, "입금 없음", None]  # 매수 단계 `buy.skipped` 와 섞이지 않음
    # 계좌 하나만 깨진 날도 사고(25.1920) — 단계 전체는 끝까지 돌아 paper.error 는 없다
    assert [desk.paper_step_failed(r) for r in 줄] == [False, True, False, False, True]
