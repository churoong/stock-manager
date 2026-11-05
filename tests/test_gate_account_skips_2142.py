"""관문 "운영 무사고" 가 계좌 건너뜀·위원회·지주사 실패 날을 사고로 센다 (docs/infra.md 25.2142, 11-05 결함 회차 [필수]).

예전엔 실행이 `partial` 로 끝나고 `step_log.paper` 의 `error`·`skipped`·`account_errors` 에 들지 않아, H 가 지주사 예외로
날마다 사지 못해도·대조 R 이 설정 오류로 날마다 건너뛰어져도 "사고 0일 ✓" 였다(docs/desk.md 6장 "실험 계좌·대조의 실패도 센다").
"""

from __future__ import annotations

import json

import pytest

from batch.core import db
from batch.jobs import desk as jd
from batch.services import desk
from tests.test_portfolio_job import MemClient

_칸 = ("job_name", "trade_date", "status", "paper_error", "paper_skipped", "paper_account_errors", "paper_account_skips",
      "paper_accounts")


def _읽기(logs: list[dict]) -> list[dict]:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for i, log in enumerate(logs):
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, trade_date, status, started_at, step_log) VALUES (?, ?, 'partial', ?, ?)",
            ["daily_kr", f"2026-10-1{i}", f"2026-10-1{i}T00:00:00+00:00", json.dumps(log, ensure_ascii=False)])
    줄 = [dict(zip(_칸, r, strict=True)) for r in mem.conn.execute(jd.BATCH_RUNS_SQL, ["daily_kr", "daily_us", "2026-01-01"])]
    return sorted(줄, key=lambda r: r["trade_date"])


@pytest.mark.parametrize(
    ("계좌", "사고"),
    [
        ({"H": {"holding": {"error": "KeyError: 'weights'"}}}, True),  # 지주사 예외 — H 가 그날 사지 않음
        ({"A": {"committee": {"error": "헌장 검사 실패 — x"}}}, True),  # 위원회 못 엶
        ({"T": {"error": "모르는 매도 규칙 day_v2"}}, True),  # 계좌 통째 건너뜀
        ({"S": {"not_open_until": "2026-10-29"}}, False),  # 개설 전 — 정상
        ({"H": {"buy": {"skipped": "배분하지 않는 날"}, "holding": {}}}, False),  # 매수 단계 정상 쉼
        ({"H": {"fills": 3, "committee": {"votes": 5}}}, False),  # 정상
    ],
)
def test_계좌_기록에서_모은_실패를_사고로_센다(계좌: dict, 사고: bool) -> None:
    모음 = desk.account_skips(계좌)
    assert bool(모음) is 사고, 모음
    log = {"paper": {"accounts": 계좌, **({"account_skips": 모음} if 모음 else {})}}
    (줄,) = _읽기([log])
    assert desk.paper_step_failed(줄) is 사고
    assert desk.batch_incident_days([줄], {"daily_kr": ["2026-10-10"]}) == (1 if 사고 else 0)


def test_가상_단계가_계좌_실패를_한_칸에_모은다() -> None:
    """`jobs/paper` 계좌 루프 뒤 `account_skips` 를 싣는다 — 관문 질의가 읽는 칸(정적)"""
    from pathlib import Path

    글 = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "paper.py").read_text(encoding="utf-8")
    assert '건너뜀들 = desk.account_skips(계좌기록)' in 글 and '기록["account_skips"] = 건너뜀들' in 글


def test_칸이_없는_옛_행도_계좌_기록에서_다시_모은다() -> None:
    """25.2145 — 25.2142 전 실행은 `account_skips` 칸이 없어 60일 동안 무사고로 읽혔다"""
    (옛,) = _읽기([{"paper": {"accounts": {"H": {"holding": {"error": "KeyError: 'weights'"}}}}}])
    assert 옛["paper_account_skips"] is None and 옛["paper_accounts"]
    assert desk.paper_step_failed(옛) and desk.batch_incident_days([옛], {"daily_kr": ["2026-10-10"]}) == 1
    (정상,) = _읽기([{"paper": {"accounts": {"H": {"fills": 2}}}}])
    assert not desk.paper_step_failed(정상)


def test_같은_날_다시_돈_실행이_끝까지_돌아도_계좌_실패는_사고() -> None:
    """25.2145 — 다시 돈 실행은 그 계좌가 '이미 냄' 으로 가 지주사 칸이 비어 그날이 무사고가 됐다"""
    줄 = [{"job_name": "daily_kr", "trade_date": "2026-10-10", "status": "partial",
           "paper_account_skips": '{"H": "holding: x"}'},
          {"job_name": "daily_kr", "trade_date": "2026-10-10", "status": "success"}]  # fmt: skip
    assert desk.batch_incident_days(줄, {"daily_kr": ["2026-10-10"]}) == 1
