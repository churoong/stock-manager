"""ETF 전술 가상계좌 판정 — 지수 국면으로 지수 ETF·인버스·현금 (docs/paper.md 17장, 2026-11-06 사용자 지시).

판 1 `etf_tactical_v1` (사전 등록 — 값은 근거 없는 첫 값 `[확인필요]`):

    상승 = 판정일 종가 ≥ 200거래일 단순이동평균 그리고 20거래일 수익 > 0  → 지수 ETF 평가 총액의 98%
    하락 = 종가 < 200일선 그리고 20거래일 수익 < 0                          → 인버스 50% (나머지 현금)
    그 밖(엇갈림) · 모름(200일치 미만, 마지막 값이 7달력일 넘게 묵음)  → 현금

국면이 **바뀐 날만** 갈아탄다(엔진 몫). 이 파일은 순수 함수 — 엔진(`jobs/paper`)과 지수 대리 리플레이(`replay`)가
같은 것을 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

RULE = "etf_tactical_v1"
#: 200일선 — 시장 추세 필터(docs/signals.md 3.5, Faber 2007)와 같은 잣대
SMA_DAYS = 200
#: 단기 추세 — 20거래일(약 한 달) 수익의 부호
MOM_DAYS = 20
#: 지수 마지막 값이 판정일보다 이만큼(달력일) 넘게 묵으면 모름 — 추세 필터와 같다
STALE_DAYS = 7
#: 상승 국면 지수 ETF 비중 — 2% 는 수수료·시가 틈 여유
LONG_WEIGHT = 0.98
#: 하락 국면 인버스 비중 — 인버스는 오래 들면 복리로 깎이고 반등에 크게 잃어 반만
INVERSE_WEIGHT = 0.5

UP, DOWN, MIXED, UNKNOWN = "up", "down", "mixed", "unknown"
LABEL = {UP: "상승", DOWN: "하락", MIXED: "엇갈림", UNKNOWN: "모름"}
ACCOUNTS_ROLE_LABEL = {"long": "지수 ETF", "inverse": "인버스"}

#: 계좌마다 판정 지수(`index_prices.index_code`)와 두 ETF 의 몫. ETF 종목은 `etf_link.TACTICAL_ETFS` 가 잇는다
#: 국내는 이름으로(코드를 박지 않는다), 미국은 티커로
ACCOUNTS: dict[str, dict[str, str]] = {
    "X": {"market": "KR", "index": "KOSPI", "long": "KODEX 200", "inverse": "KODEX 인버스"},
    "Y": {"market": "US", "index": "SP500", "long": "SPY", "inverse": "SH"},
}


@dataclass(frozen=True)
class Regime:
    state: str
    as_of: str | None  # 판정에 쓴 지수 마지막 날
    close: float | None
    sma: float | None
    mom: float | None  # 20거래일 수익(비율)

    @property
    def label(self) -> str:
        return LABEL[self.state]


def regime(series: list[tuple[str, float]], as_of: str) -> Regime:
    """`as_of`(판정일) 이하 지수 종가로 국면. `series` 는 (날짜 'YYYY-MM-DD', 종가) — 순서는 묻지 않는다."""
    줄 = sorted((d[:10], float(c)) for d, c in series if d[:10] <= as_of and c is not None and float(c) > 0)
    if len(줄) < max(SMA_DAYS, MOM_DAYS + 1):
        return Regime(UNKNOWN, 줄[-1][0] if 줄 else None, None, None, None)
    마지막날, 종가 = 줄[-1]
    if (date.fromisoformat(as_of[:10]) - date.fromisoformat(마지막날)).days > STALE_DAYS:
        return Regime(UNKNOWN, 마지막날, 종가, None, None)
    sma = sum(c for _d, c in 줄[-SMA_DAYS:]) / SMA_DAYS
    mom = 종가 / 줄[-1 - MOM_DAYS][1] - 1
    if 종가 >= sma and mom > 0:
        state = UP
    elif 종가 < sma and mom < 0:
        state = DOWN
    else:
        state = MIXED
    return Regime(state, 마지막날, 종가, sma, mom)


def target(state: str) -> dict[str, float]:
    """국면 → 목표 비중 {'long'|'inverse': 평가 총액 대비}. 빈 dict = 현금"""
    if state == UP:
        return {"long": LONG_WEIGHT}
    if state == DOWN:
        return {"inverse": INVERSE_WEIGHT}
    return {}


def step(held: str | None, want: str | None) -> tuple[str, str | None]:
    """오늘 할 일 — (`hold`|`sell`|`buy`, 몫). `held` = 들고 있는 몫(`long`·`inverse`·None), `want` = 목표 몫.

    **갈아탈 때는 파는 날과 사는 날이 하루 갈린다** — 주문 때 현금 검사(배분·위험 점검)가 판 돈이 들어오기 전
    현금을 보므로, 판 돈이 장부에 들어온 뒤(다음 실행) 산다. 그 하루는 현금이다(17.1, 리플레이도 같다)"""
    if held == want:
        return "hold", held
    if held is not None:
        return "sell", held
    return "buy", want


def replay(series: list[tuple[str, float]], cost: float = 0.0012) -> dict[str, float | int | None]:
    """지수 대리 리플레이(docs/paper.md 17.3) — 판정일 종가로 국면을 정하고 **다음 날부터** 그 비중을 든다
    (미래 정보 없음).
    지수 ETF = 지수 일간 수익, 인버스 = −1 × 지수 일간 수익, 보수·괴리 무시. `cost` = 갈아탈 때 바뀐 비중 1 당 비용
    (수수료 + 슬리피지 왕복 어림 `[확인필요]`). 보유(buy & hold)와 같은 기간으로 시간가중 수익·MDD 를 낸다"""
    줄 = sorted((d[:10], float(c)) for d, c in series if c is not None and float(c) > 0)
    if len(줄) < SMA_DAYS + 2:
        return {"days": 0}
    시작 = SMA_DAYS  # 첫 판정이 가능한 날
    전략, 보유 = [1.0], [1.0]
    든: dict[str, float] = {}
    바꿈 = 0
    for i in range(시작, len(줄) - 1):
        목표 = target(regime(줄[: i + 1], 줄[i][0]).state)
        할일, 몫 = step(next(iter(든), None), next(iter(목표), None))
        if 할일 != "hold":
            새 = {} if 할일 == "sell" else {몫: 목표[몫]}  # 파는 날은 현금으로 하루(`step`)
            변화 = sum(abs(새.get(k, 0.0) - 든.get(k, 0.0)) for k in {"long", "inverse"})
            전략[-1] *= 1 - cost * 변화
            든 = 새
            바꿈 += 1
        r = 줄[i + 1][1] / 줄[i][1] - 1
        하루 = 든.get("long", 0.0) * r - 든.get("inverse", 0.0) * r
        전략.append(전략[-1] * (1 + 하루))
        보유.append(보유[-1] * (1 + r))
    return {
        "days": len(전략) - 1, "from": 줄[시작][0], "to": 줄[-1][0], "switches": 바꿈,
        "twr_pct": (전략[-1] - 1) * 100, "hold_pct": (보유[-1] - 1) * 100,
        "mdd_pct": _mdd(전략) * 100, "hold_mdd_pct": _mdd(보유) * 100,
    }  # fmt: skip


def _mdd(values: list[float]) -> float:
    """고점 대비 최대 하락률(음수, docs/metrics.md)"""
    고점, 최악 = -math.inf, 0.0
    for v in values:
        고점 = max(고점, v)
        최악 = min(최악, v / 고점 - 1)
    return 최악
