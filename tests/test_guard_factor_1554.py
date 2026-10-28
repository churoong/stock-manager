"""등락률이 빈 국내 행(야후 대체 출처)에서도 가격제한폭 밖 움직임은 기업행위로 감시한다 (docs/adjust.md 2장, docs/infra.md 25.1554).

지키는 것
- `guard_factor`: 등락률이 있으면 `factor_of` 와 같다 · 없으면 ±30% 안은 1.0, 밖은 종가 비율(손계산)
- 보유 기업행위 경계(`action_factors`)·이어 멈춤 날(`action_dates`)이 야후 날의 2:1 분할을 잡는다(가상모의투자 멈춤)
- 수정주가 계열(`factor_of`)은 그대로 — 모르면 조정하지 않는다
"""

from __future__ import annotations

import pytest

from batch.jobs import monitor_targets as job
from batch.services import adjust as adj
from tests.test_portfolio_job import MemClient


def test_감시_계수_손계산() -> None:
    assert adj.guard_factor(10_000, 5_000, 0.0) == adj.factor_of(10_000, 5_000, 0.0) == pytest.approx(0.5)
    assert adj.guard_factor(10_000, 7_000, None) == 1.0  # 하한가 −30% 는 시장이 낼 수 있다
    assert adj.guard_factor(10_000, 13_000, None) == 1.0
    assert adj.guard_factor(10_000, 6_990, None) == pytest.approx(0.699)  # 폭 밖
    assert adj.guard_factor(10_000, 5_000, None) == pytest.approx(0.5)
    assert adj.guard_factor(1_000, 10_000, None) == pytest.approx(10.0)  # 10:1 병합
    assert adj.guard_factor(0, 5_000, None) == 1.0
    # 계수 범위(MIN·MAX) 밖은 자료 이상 — 기업행위로 보지 않는다(25.1854, 25.1853 교차검증 [참고]: 범위 검사를 지워도 통과했다)
    assert adj.guard_factor(10_000, 3_000_000, None) == 1.0  # 300배
    assert adj.guard_factor(1_000_000, 4_000, None) == 1.0  # 1/250
    # 수정주가 계열은 그대로 — 모르면 조정하지 않는다
    assert adj.factor_of(10_000, 5_000, None) == 1.0


def _준비(pct_on_split: float | None) -> MemClient:
    from batch.core import db

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 'active', 't', 't')"
    )
    # 09-15(화) 2:1 분할 — 그날은 거래소가 실패해 야후로 받았다(등락률 빈칸)
    for day, close, pct in (("2026-09-14", 100_000, 0.0), ("2026-09-15", 50_500, pct_on_split),
                            ("2026-09-16", 51_000, 0.99)):  # fmt: skip
        c.execute(
            "INSERT INTO prices (stock_id, date, close, change_pct, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'KRW', 't', 't')",
            [day, close, pct],
        )
    return mem


def test_야후_날_분할도_보유_경계와_이어_멈춤_날에_잡힌다() -> None:
    mem = _준비(None)
    assert job.action_factors(mem, 1, "KR") is None  # type: ignore[arg-type]  # 경계 — 목표·손절을 대지 않는다
    assert job.action_dates(mem, 1, "KR") == ["2026-09-15"]  # type: ignore[arg-type]
    # 거래소 등락률이 있는 날(+1% 로 적힌 분할)은 예전과 같이 잡는다
    mem2 = _준비(1.0)
    assert job.action_factors(mem2, 1, "KR") is None  # type: ignore[arg-type]
    assert job.action_dates(mem2, 1, "KR") == ["2026-09-15"]  # type: ignore[arg-type]
