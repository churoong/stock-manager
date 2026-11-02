"""**알고 둔 차이** — 예약 판정 실행의 월 계열 마지막 "달" 은 부분 달이다 (25.2105, 11-02 결함 회차 [권고], funnel.md 3장 0→1).

매월 3~7일 판정 실행의 곡선은 그달 1~5거래일에서 끝난다. `luck.monthly_returns` 는 달마다 마지막 값끼리 이어, 그 며칠의
수익이 한 달 관측으로 들어간다(backtest.md 9장 "월말 자산끼리" 와 어긋남 — 25.1320 은 시작 쪽만 고쳤다). 판정 계산이
바뀌므로 11-03 판정 실행 전에는 엔진을 고치지 않고 결정만 사전 등록했다. 11-03 결과는 끝 달을 뺀 판정과 다른 줄을
"모름" 으로 읽고, 엔진은 그 뒤 판을 올려 고친다(handoff).

이 시험은 **지금 꼴을 붙든다** — 고침이 들어가면 깨지고, 그 커밋에서 기대를 뒤집는다(끝 달 = 앞 달, 창 4·버림 11).
"""

from __future__ import annotations

from datetime import date, timedelta

from batch.services import luck
from batch.services import scoreboard as scb


def _곡선(시작: date, 끝: date, 기울기: float) -> list[tuple[str, float]]:
    out, d, k = [], 시작, 0
    while d <= 끝:
        if d.weekday() < 5:
            out.append((d.isoformat(), 100.0 * (1 + 기울기) ** k * (1 + 0.01 * ((k * 7) % 5 - 2))))
            k += 1
        d += timedelta(days=1)
    return out


def test_11월_3일_실행의_끝_달은_하루짜리_11월이다() -> None:
    # 11-03 실행: 시작은 25.1320 대로 다음 달 첫날(2021-11-01), 끝은 마지막 시세일 11-02(월)
    전략 = luck.monthly_returns(_곡선(date(2021, 11, 1), date(2026, 11, 2), 0.0006))
    기준 = luck.monthly_returns(_곡선(date(2021, 11, 1), date(2026, 11, 2), 0.0004))
    계열 = luck.excess_months(전략, 기준)
    assert 계열[-1][0] == "2026-11"  # 10-30 → 11-02 하루가 '한 달'
    assert len(계열) == 60
    창 = scb.walk_windows([(m, v) for m, v in 계열])
    assert (창["total"], 창["dropped"]) == (5, 0)  # 끝 달을 빼면 (4, 11) — 0→1 의 3/4 가 다른 창 묶음으로 판정된다
    뺀 = scb.walk_windows([(m, v) for m, v in 계열[:-1]])
    assert (뺀["total"], 뺀["dropped"]) == (4, 11)  # 판정판의 경계 규칙(25.2107)이 이 값으로 다시 판정한다
