"""주간 요약 '오래 안 돈 작업' 이 일일 배치 안에서 도는 지수 수집을 따로 세지 않는다 (docs/infra.md 25.2230).

`index_prices` 는 일일 배치가 부르고 실행 기록을 남기지 않아, 기대 주기 표에 있으면 늘 거짓 경보였다."""

from __future__ import annotations

import inspect
from pathlib import Path

from batch.jobs import daily, weekly_summary

ROOT = Path(__file__).resolve().parents[1]


def test_지수_수집은_기대_주기_표에_없다() -> None:
    assert "index_prices" not in weekly_summary.EXPECTED_DAYS


def test_지수_수집은_일일_배치_안에서_돌고_워크플로는_손으로만() -> None:
    assert "_collect_index_prices(client)" in inspect.getsource(daily)
    wf = (ROOT / ".github" / "workflows" / "index-prices.yml").read_text(encoding="utf-8")
    assert "schedule:" not in wf and "cron:" not in wf
