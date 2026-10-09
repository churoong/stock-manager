"""참고 분석이 묵지 않은 성과 지표를 매일 다시 내지 않는다 (docs/infra.md 25.1080).

`metrics.run` 은 종목 하나를 줘도 나라 전체 시장 날짜·재무 사건·배당을 읽는다 — 유니버스 밖 관심 종목 하나에 국내
약 55만 행(10-08 `ensure_data`)이 일일 배치마다 나갔다. 지표는 주간 작업이 내는 값이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from batch.jobs import analyze_extra, financials, metrics


class _결과:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def dicts(self) -> list[dict]:
        return self._rows


class _가짜:
    def __init__(self, 날: dict[int, str | None], 실패: bool = False) -> None:
        self.날, self.실패 = 날, 실패

    def execute(self, sql: str, args: list) -> _결과:
        assert sql == analyze_extra.METRICS_LATEST_SQL
        if self.실패:
            raise RuntimeError("읽기 실패")
        return _결과([{"stock_id": k, "d": v} for k, v in self.날.items()])


@pytest.fixture()
def 부른것(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(financials, "collection_plan", lambda *a, **k: None)
    monkeypatch.setattr(financials, "run", lambda *a, **k: 0)
    monkeypatch.setattr(metrics, "run", lambda w, ticker=None, countries=(): out.append(ticker) or 0)
    return out


def test_묵은_것만_다시_낸다(부른것: list[str]) -> None:
    오늘 = datetime.now(UTC).date()
    날 = {1: (오늘 - timedelta(days=2)).isoformat(), 2: (오늘 - timedelta(days=30)).isoformat(), 3: None}
    rows = [{"id": i, "ticker": f"T{i}"} for i in (1, 2, 3)]
    경고 = analyze_extra.ensure_data(_가짜(날), "KR", rows)  # type: ignore[arg-type]
    assert 부른것 == ["T2", "T3"] and 경고 == []


def test_날짜를_못_읽으면_예전처럼_모두(부른것: list[str]) -> None:
    rows = [{"id": i, "ticker": f"T{i}"} for i in (1, 2)]
    경고 = analyze_extra.ensure_data(_가짜({}, 실패=True), "KR", rows)  # type: ignore[arg-type]
    assert 부른것 == ["T1", "T2"] and "모두 다시" in 경고[0]
