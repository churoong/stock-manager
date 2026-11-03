"""KIS 대 가상 H 견주기의 "미국 배치 끝난 날" 은 가상 단계가 끝까지 돈 실행만 (25.2118, 11-03 결함 회차 [권고]).

`US_DONE_SQL` 은 `status IN ('success','partial')` 만 봐서, 가상 단계가 예외·계좌 예외·잠금 건너뜀으로 끝난 `partial`
실행 날도 끝날로 셌다 — 그날 H 평가 행은 미국 종가로 덮이지 않았다(h_compare·h_check 가 미국 하루치만큼 어긋남).
관문 쪽 `desk.paper_step_failed`(25.1636)와 같은 잣대로 거른다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.jobs import broker_mirror as bmj
from batch.services import desk as sd
from tests.test_paper_job_1445 import Client


def _실행(c: Client, 시작: str, status: str, step: Any) -> None:
    c.conn.execute(
        "INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status, step_log)"
        " VALUES ('daily_us', 'US', '2026-11-03', 'cron', ?, ?, ?)",
        [시작, status, step if isinstance(step, str) or step is None else json.dumps(step, ensure_ascii=False)],
    )


def _끝난날(c: Client) -> set[str]:
    return {r[0] for r in c.conn.execute(bmj.US_DONE_SQL, ["daily_us", "2026-11-01"]).fetchall()}


@pytest.mark.parametrize(
    ("status", "step", "든다"),
    [
        ("success", {"paper": {"accounts": {"H": {}}}}, True),
        ("partial", {"paper": {"error": "KeyError: 'x'"}}, False),
        ("partial", {"paper": {"error": ""}}, False),  # 빈 글 예외도
        ("partial", {"paper": {"skipped": "다른 시장 단계가 잠금을 쥐고 있다"}}, False),
        ("partial", {"paper": {"skipped": "입금 없음"}}, True),  # 사고가 아닌 건너뜀
        ("partial", {"paper": {"account_errors": {"H": "ValueError"}}}, False),
        ("partial", {"paper": {"account_errors": {}}}, True),
        ("partial", {"warnings": ["시세 2종목 없음"]}, True),  # 가상 단계와 무관한 partial
        ("partial", None, True),  # paper 칸 없는 옛 행 — 모름은 예전처럼
        ("partial", "not json", True),
        ("failed", {"paper": {}}, False),
    ],
)
def test_가상_단계가_끝까지_돈_실행만_끝난_날(status: str, step: Any, 든다: bool) -> None:
    c = Client()
    _실행(c, "2026-11-03T13:27:00+00:00", status, step)
    assert ("2026-11-03" in _끝난날(c)) is 든다  # 13:27Z = KST 22:27 같은 날
    # 관문 쪽 잣대와 같다(25.1636) — json 이 아닌 행·paper 칸 없는 행은 둘 다 사고 아님
    if isinstance(step, dict) and status != "failed":
        p = step.get("paper") or {}
        행 = {"paper_error": p.get("error"), "paper_skipped": p.get("skipped"),
             "paper_account_errors": json.dumps(p["account_errors"]) if "account_errors" in p else None}  # fmt: skip
        assert sd.paper_step_failed(행) is (not 든다)


def test_같은_날_정상_재실행이_있으면_그날은_든다() -> None:
    c = Client()
    _실행(c, "2026-11-03T13:27:00+00:00", "partial", {"paper": {"error": "x"}})
    assert _끝난날(c) == set()
    _실행(c, "2026-11-03T14:27:00+00:00", "success", {"paper": {"accounts": {}}})
    assert _끝난날(c) == {"2026-11-03"}


def test_건너뜀_허용_글은_관문과_같다() -> None:
    assert sd.PAPER_SKIP_OK == ("입금 없음",) and "'입금 없음'" in bmj.US_DONE_SQL
