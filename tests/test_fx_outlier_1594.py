"""환율 이상값 — 앞뒤 이웃 중앙값에서 10% 넘게 벗어난 날은 저장하지 않는다 (docs/infra.md 25.1594)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.jobs import fx as job


def _줄(값: list[float]) -> list[tuple]:
    d0 = date(2026, 10, 1)
    return [("USDKRW", (d0 + timedelta(days=i)).isoformat(), v, "t", "t") for i, v in enumerate(값)]


def test_하루짜리_깨진_값만_뺀다() -> None:
    값 = [1380.0, 1385.0, 1390.0, 13.9, 1388.0, 1392.0, 1395.0]  # 넷째 날 ÷100
    남김, 경고 = job.drop_outliers(_줄(값))
    assert [r[2] for r in 남김] == [1380.0, 1385.0, 1390.0, 1388.0, 1392.0, 1395.0]
    assert 경고 and "2026-10-04 13.90" in 경고[0]


def test_큰_추세와_큰_하루는_그대로() -> None:
    # 5년치 채우기처럼 천천히 30% 오른 계열 — 창 전체 중앙값이면 양끝이 빠졌을 것
    값 = [1100.0 + i * 3 for i in range(120)]
    남김, 경고 = job.drop_outliers(_줄(값))
    assert len(남김) == 120 and not 경고
    # 하루 5% 급등(위기 날)은 거르지 않는다
    남김, 경고 = job.drop_outliers(_줄([1300.0, 1302.0, 1305.0, 1370.0, 1372.0, 1375.0, 1374.0]))
    assert len(남김) == 7 and not 경고


def test_셋_미만은_그대로() -> None:
    assert job.drop_outliers(_줄([1380.0, 13.8])) == (_줄([1380.0, 13.8]), [])
