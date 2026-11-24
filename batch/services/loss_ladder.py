"""계좌 손실 사다리 — 그림자 (docs/desk.md 6장, docs/roadmap.md 1장 #3, docs/infra.md 25.2296).

멈춤 스위치는 하나뿐이고(켜면 손절·정리까지 멈춘다) 계좌 수준의 "한 주·한 달·고점 대비 이만큼 잃으면 새 매수를 멈춘다"
는 규칙이 없다 — 하루 손실 한도(`risk_gate.DAILY_LOSS_HALT_PCT`)만 있다. 실전에서는 계좌 수준 중단이 있어야 하지만
**여기서는 규칙을 만들지 않는다**: 문턱을 정하려면 그 문턱이 지금까지 며칠·몇 번 걸렸을지를 먼저 알아야 한다(너무 자주
걸리는 문턱은 전략을 멈춰 세우고, 한 번도 안 걸리는 문턱은 없는 것과 같다). 순수 계산 — 읽지도 쓰지도 않고, 주문·장부·
판정에 닿지 않는다. 문턱과 켜기는 사용자 결정이다.

- 잣대는 계좌의 **시간가중 지수**(`paper_daily.twr_index` — 입출금을 뺀 수익). 평가 **행** 단위다: 한 주 = 평가 행 5개
  앞과 견줌, 한 달 = 21개 앞과 견줌(달력 날짜가 아니다 — 배치가 빠진 날은 행이 없어 창이 그만큼 길어진다)
- 고점 대비 = 그날 지수 ÷ 그날까지의 최고 지수 − 1
- **걸렸을 날** = 그 값이 문턱보다 **작은**(넘게 잃은) 날 수. 정확히 문턱인 날은 아니다(하루 손실 한도와 같은 꼴)
- **걸렸을 번** = 안 걸린 날 다음에 걸린 횟수(이어진 날은 한 번). 멈춤은 켜지면 이어지므로 "몇 번 켜졌을까" 는 번이다
- 살펴보는 문턱 격자(`GRID`)는 **관찰용**이다 — 이 가운데 하나를 고르자는 것이 아니라 분포의 꼴을 보려는 것
"""

from __future__ import annotations

from typing import Any

#: 창 — 평가 행 수(한 주·한 달). 21 은 한 달의 거래일 어림
WINDOWS: dict[str, int] = {"week": 5, "month": 21}
#: 살펴보는 문턱(%) — 관찰용 격자. 규칙이 아니다(문턱은 사용자 결정)
GRID: dict[str, tuple[float, ...]] = {
    "week": (-3.0, -5.0, -7.0),
    "month": (-5.0, -8.0, -12.0),
    "dd": (-10.0, -15.0, -20.0),
}

#: 문턱 비교의 여유(%p) — 95 ÷ 100 − 1 은 부동소수로 −5.000000000000004% 라 "정확히 −5%" 가 걸린 날로 세어졌다(25.2296
#: 시험이 잡음). 지수는 소수 여러 자리 값이라 이 여유가 진짜 걸림을 놓칠 일은 없다
EPS = 1e-9

def measures(series: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """[(날짜, 지수)] → 날마다 {as_of, week, month, dd}(%). 창이 안 찬 날의 week·month 는 None.

    날짜 차례로 세운다. 지수가 0 이하인 행은 버린다(견줄 수 없다)"""
    v = [(d, float(t)) for d, t in sorted(series) if t is not None and float(t) > 0]
    out: list[dict[str, Any]] = []
    고점 = 0.0
    for i, (d, t) in enumerate(v):
        고점 = max(고점, t)
        줄: dict[str, Any] = {"as_of": d, "dd": (t / 고점 - 1) * 100}
        for 이름, n in WINDOWS.items():
            줄[이름] = (t / v[i - n][1] - 1) * 100 if i >= n else None
        out.append(줄)
    return out


def _걸림(값들: list[float | None], 문턱: float) -> tuple[int, int]:
    """(걸렸을 날, 걸렸을 번). None(창이 안 참)은 "안 걸린 날" 로 친다 — 그 뒤 첫 걸림은 새 번이다"""
    날, 번, 앞 = 0, 0, False
    for x in 값들:
        지금 = x is not None and x < 문턱 - EPS
        if 지금:
            날 += 1
            if not 앞:
                번 += 1
        앞 = 지금
    return 날, 번


def ladder(series: list[tuple[str, float]]) -> dict[str, Any]:
    """한 계좌의 사다리 그림자: {days, now: {week, month, dd}, hits: {week: [[문턱, 날, 번], …], month: …, dd: …}}.

    `days` = 잰 평가 행 수. 행이 없으면 now 는 전부 None, hits 는 전부 0"""
    m = measures(series)
    끝 = m[-1] if m else {}
    return {
        "days": len(m),
        "now": {k: 끝.get(k) for k in GRID},
        "hits": {k: [[문턱, *_걸림([x[k] for x in m], 문턱)] for 문턱 in GRID[k]] for k in GRID},
    }


#: 줄에 쓰는 이름
_이름 = {"week": "한 주", "month": "한 달", "dd": "고점 대비"}


def lines(account: str, found: dict[str, Any]) -> list[str]:
    """사람이 읽는 줄 — 지금 값 한 줄 + 갈래마다 문턱별 "날/번" 한 줄"""
    지금 = found.get("now") or {}

    def 값(x: Any) -> str:
        return "모름" if x is None else f"{float(x):+.1f}%"

    줄 = [f"계좌 손실 사다리(그림자) {account}: 평가 {found.get('days', 0)}일 · "
          + " · ".join(f"{_이름[k]} {값(지금.get(k))}" for k in GRID)]  # fmt: skip
    for k in GRID:
        칸 = " · ".join(f"{문턱:g}% {날}일/{번}번" for 문턱, 날, 번 in (found.get("hits") or {}).get(k) or [])
        줄.append(f"  {_이름[k]} 걸렸을 날/번: {칸}")
    return 줄
