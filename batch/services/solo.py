"""거장 단독 규칙 계좌 Z3(그린블랫)·Z4(오닐) — 순수 판정
(docs/company.md 12.5, docs/paper.md 18장, docs/infra.md 25.2319).

정의처는 docs/company.md 12.5(사전 등록 — 고치지 않는다)와 docs/paper.md 18장(구현 확인)이다. 이 파일은 읽지도 쓰지도
않는다 — DB 는 `jobs/paper_solo` 가 읽어 이것을 부른다.

- **Z3 `magic_formula_v1`("대리")**: 매월 그 시장 **첫 거래일 장**에(그 장에 체결되는 주문을 그 앞 결정일에 낸다)
  그린블랫부 순위(`masters.greenblatt_parts` — EY·ROC 오름차순 백분위 합의 백분위, 모집단 = 그날 유니버스 편입 종목
  가운데 값이 나오는 것) 상위에서 아직 안 든 1종목. 평가 총액 1/24, 시장당 보유 12 상한, 매도 `hold_1y`(첫 체결 365
  달력일 뒤 첫 결정일, 다음 장 시가), 손절·목표 없음
- **Z4 `canslim_v1`**: 그날 유니버스에서 `oneil_earn`·`oneil_price` 를 **모두 찬성**으로 넘고 시장 국면(M)이 강세인 날만
  (12.5 "약세가 아닌 날" — 국면을 모르는 날은 사지 않는다, paper.md 18.2), `momentum_12_1` 백분위 순으로 시장당 보유 5
  상한·종목 10%, 매도 `canslim_exit`(첫 체결가 대비 종가 −8% 이하 손절, +25% 이상 익절, 기한 없음, 다음 장 시가)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from batch.services import masters
from batch.services.company import Dossier, percentile_ranks

MAGIC = "magic_formula"
CANSLIM = "canslim"
#: 엔진이 아는 단독 고르기 값 → 판 이름(사전 등록 12.5)
RULE_NAMES: dict[str, str] = {MAGIC: "magic_formula_v1", CANSLIM: "canslim_v1"}

HOLD_1Y = "hold_1y"
CANSLIM_EXIT = "canslim_exit"
#: 매도 까닭 코드 — `손절` 과 이름이 같으면 "손절 보유의 남은 회차 거둠" 길을 탄다(스윙 `스윙손절` 과 같은 까닭으로
#: 따로)
HOLD_CODE = "보유1년"
CANSLIM_CODES: dict[str, str] = {"stop": "오닐손절", "target": "오닐익절"}

# ── 사전 등록 값 (company.md 12.5 — 맞춘 값 없음) ─────────────────────────────────────────────
#: Z3 크기 = 평가 총액 ÷ 24 (두 시장 × 보유 12), 시장당 보유 상한
Z3_SLICES = 24
Z3_MAX_HOLD = 12
#: 첫 체결 뒤 365 달력일 (Greenblatt 2005 — 1년 들고 판다)
HOLD_DAYS = 365
#: Z4 시장당 보유 상한·종목 비중(%)
Z4_MAX_HOLD = 5
Z4_WEIGHT_PCT = 10.0
#: Z4 매도 — 첫 체결가 대비 종가 (O'Neil 1988 — 7~8% 손절, 20~25% 익절)
CANSLIM_STOP = -0.08
CANSLIM_TARGET = 0.25
#: Z4 시장 국면(M)을 재는 지수 — 그 시장의 대표 지수 하나(`trend.regimes_for_country` 의 열쇠). 국내는 KOSPI(KOSDAQ
#: 종목도
#: 같은 날 같은 M 을 본다 — "그날" 의 조건이다, paper.md 18.2)
M_INDEX: dict[str, str] = {"KR": "KOSPI", "US": "SP500"}


def is_month_first_session(day: date, previous: date) -> bool:
    """`day` 가 그 달 첫 거래일인가 — 바로 앞 거래일(`previous`, 부르는 쪽이 달력으로)이 다른 달이면 참"""
    return (previous.year, previous.month) != (day.year, day.month)


@dataclass(frozen=True)
class Ranked:
    """순위 줄 하나 — 고른 까닭(근거표)에 그대로 쓴다"""

    stock_id: int
    pct: float  # 그 갈래의 정렬 잣대(Z3 마법공식 백분위 · Z4 12-1 모멘텀 백분위)
    detail: dict[str, Any]


def magic_ranks(universe: list[Dossier]) -> list[Ranked]:
    """Z3 순위 — 값이 나오는 종목만(금융·유틸리티·영업이익 ≤ 0·분모 ≤ 0·통화 다름은 빠진다), 백분위 내림차순·같으면
    종목 번호.

    식은 `masters.greenblatt` 와 같다(EY·ROC 오름차순 백분위의 합 → 백분위) — 모집단이 신호 종목이 아니라 유니버스다"""
    부분 = {d.stock_id: masters.greenblatt_parts(d) for d in universe}
    ey = percentile_ranks({k: v[0] for k, v in 부분.items()})
    roc = percentile_ranks({k: v[1] for k, v in 부분.items()})
    합 = percentile_ranks({k: (ey[k] + roc[k]) if k in ey and k in roc else None for k in 부분})
    줄 = [Ranked(k, 합[k], {"ey": 부분[k][0], "roc": 부분[k][1]}) for k in 합]
    return sorted(줄, key=lambda r: (-r.pct, r.stock_id))


def canslim_ranks(universe: list[Dossier]) -> list[Ranked]:
    """Z4 후보 — 오닐부 두 부서(`oneil_earn`·`oneil_price`, 모집단 = 유니버스)가 모두 찬성한 종목만, 12-1 모멘텀 백분위
    내림차순·같으면 종목 번호. 국면(M)은 부르는 쪽이 본다(`canslim_market_ok`)"""
    실적 = masters.oneil_earn(universe, universe)
    주가 = masters.oneil_price(universe, universe)
    rs = percentile_ranks({d.stock_id: masters._raw(d, "mom12_1") for d in universe})
    줄 = [Ranked(d.stock_id, rs[d.stock_id], {"rs": rs[d.stock_id]}) for d in universe
         if 실적[d.stock_id].stance == 1 and 주가[d.stock_id].stance == 1 and d.stock_id in rs]  # fmt: skip
    return sorted(줄, key=lambda r: (-r.pct, r.stock_id))


def canslim_market_ok(state: str | None) -> bool:
    """M — 강세(bull)인 날만. 약세(bear)·모름(unknown·None)은 사지 않는다(paper.md 18.2)"""
    return state == "bull"


def picks(ranked: list[Ranked], held: set[int], excluded: set[int], max_hold: int, per_day: int | None) -> list[Ranked]:
    """순위에서 새로 살 종목 — 이미 든 것·뺄 것(`excluded` — 매도가 열린·다시 사기 쉼)은 건너뛴다.

    자리 = `max_hold − 보유 수`(0 아래면 0), `per_day` 가 있으면 그것과 작은 쪽(Z3 은 한 달에 1)"""
    자리 = max(0, max_hold - len(held))
    if per_day is not None:
        자리 = min(자리, per_day)
    out: list[Ranked] = []
    for r in ranked:
        if len(out) >= 자리:
            break
        if r.stock_id in held or r.stock_id in excluded:
            continue
        out.append(r)
    return out


def hold_1y_due(first_fill: str, today: date) -> bool:
    """`hold_1y` — 첫 체결일 + 365 달력일이 지났거나 오늘이면 참(그날이 결정일 — 다음 장 시가에 판다)"""
    return today >= date.fromisoformat(first_fill[:10]) + timedelta(days=HOLD_DAYS)


def canslim_exit(entry_price: float, close: float) -> str | None:
    """`canslim_exit` — 그날 종가(권리락은 부르는 쪽이 되돌린 값)가 첫 체결가 대비 −8% 이하면 'stop', +25% 이상이면
    'target', 아니면 None. 같으면 넘은 것이다(사전 등록 "이하"·"이상")"""
    if entry_price <= 0:
        return None
    r = close / entry_price - 1
    if r <= CANSLIM_STOP + 1e-12:
        return "stop"
    if r >= CANSLIM_TARGET - 1e-12:
        return "target"
    return None
