"""주간 성과 지표 실행 기록의 기준일과 나라별 행 날짜가 상태 화면 신선도 창(기준일 6일 전 ~ 기준일) 안에 든다
(docs/infra.md 25.1861, 25.1860 교차검증 [권고]).

25.1859 는 실행 기록 `trade_date`(나라별 기준일 가운데 큰 값 — 예약 월 17:23 UTC 면 국내 월)와 미국 행(금)이 달라 미국
칸이 늘 빈 값이 될 뻔했다. 웹 시험은 `trade_date` 를 손으로 넣어 배치 규칙과 묶이지 않는다 — 여기서 배치 쪽 규칙을 잰다.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from batch.core import calendar as cal
from batch.jobs import metrics

뿌리 = Path(__file__).resolve().parent.parent
창 = int(re.search(r"date\(r\.trade_date, '-(\d+) day'\)", (뿌리 / "web" / "lib" / "health.ts").read_text(encoding="utf-8")).group(1))  # type: ignore[union-attr]  # noqa: E501
분, 시, *_, 요일 = re.search(r'cron: "([^"]+)"', (뿌리 / ".github" / "workflows" / "metrics.yml").read_text(encoding="utf-8")).group(1).split()  # type: ignore[union-attr]  # noqa: E501


def test_실행은_나라별_기준일_가운데_큰_값을_적는다() -> None:
    글 = inspect.getsource(metrics.run)
    assert "as_of = max(as_of_by.values())" in 글 and "trade_date=as_of" in 글 and '"as_of_by": as_of_by' in 글


@pytest.mark.parametrize("날", ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-23", "2026-12-28", "2027-01-04"])
def test_예약_시각의_나라별_기준일이_창_안이다(monkeypatch: pytest.MonkeyPatch, 날: str) -> None:
    지금 = datetime.fromisoformat(f"{날}T{int(시):02d}:{int(분):02d}:00+00:00").astimezone(UTC)
    assert 지금.weekday() == (int(요일) - 1) % 7  # cron 요일(일=0) → 파이썬(월=0)
    monkeypatch.setattr(cal, "local_today", lambda market: 지금.astimezone(cal.market_tz(market)).date())
    as_of_by = {c: cal.default_as_of(c) for c in ("KR", "US")}
    기준 = max(as_of_by.values())
    for 나라, d in as_of_by.items():
        차 = (datetime.fromisoformat(기준) - datetime.fromisoformat(d)).days
        assert 0 <= 차 <= 창, (날, 나라, d, 기준)
