"""개장 전 잔고 기준점이 수능일 한 시간 늦춤을 안다 (docs/infra.md 25.2348 — 11-24 결함 루프 건너뛴 ⓒ)."""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from batch.jobs import monitor_targets as mt


def _줄(날: str, 시각: str) -> dict:
    return {"as_of": 날, "fetched_at": 시각, "holdings_json": '[{"code": "005930", "qty": 3}]'}


def test_수능일_09시대_줄은_개장_전이다() -> None:
    assert "2026-11-19" in mt.KR_CSAT_DAYS
    # 09:35 KST = 00:35 UTC — 달력은 09:00 개장으로 보지만 그날은 10:00 개장이다
    assert job._개장전_잔고([_줄("2026-11-19", "2026-11-19T00:35:00+00:00")], "KR") == [("2026-11-19", {"005930": 3})]
    # 10:00 KST(01:00 UTC) 뒤는 개장 뒤
    assert job._개장전_잔고([_줄("2026-11-19", "2026-11-19T01:00:00+00:00")], "KR") == []


def test_평일은_그대로() -> None:
    assert job._개장전_잔고([_줄("2026-11-18", "2026-11-18T00:35:00+00:00")], "KR") == []
    assert job._개장전_잔고([_줄("2026-11-18", "2026-11-17T23:50:00+00:00")], "KR") == [("2026-11-18", {"005930": 3})]
