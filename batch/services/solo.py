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
    return candidates(ranked, held, excluded)[:slots(len(held), max_hold, per_day)]


def candidates(ranked: list[Ranked], held: set[int], excluded: set[int]) -> list[Ranked]:
    """순위에서 살 수 있는 후보 전부(순위 순) — 든 것·뺄 것을 건너뛴다. 몇 개를 살지는 `slots`, 자료 문제(멈춤·시세·
    1주 못 삼)로 다음 순위로 넘기는 것은 부르는 쪽(`jobs/paper_solo.size_orders`)이 한다(25.2320)"""
    return [r for r in ranked if r.stock_id not in held and r.stock_id not in excluded]


def slots(n_held: int, max_hold: int, per_day: int | None) -> int:
    """새로 살 자리 = `max_hold − 보유 수`(0 아래면 0), `per_day` 가 있으면 그것과 작은 쪽(Z3 은 한 달에 1)"""
    자리 = max(0, max_hold - n_held)
    return 자리 if per_day is None else min(자리, per_day)


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


def warning(rec: dict[str, Any]) -> tuple[str, str] | None:
    """Z3·Z4 계좌 실행 기록(`step_log.paper.accounts.Z3|Z4`) → (수준, 글) 또는 None — **살 자리가 있었는데 하나도
    못 산 날**.

    리포트의 ⚠️ 줄(`jobs/paper`)과 운용 요약 '확인할 일'(`desk.checks`)이 같은 술어를 쓴다(25.2170 의 ETF 와 같은 꼴,
    25.2323). 거르는 문도 하나 — 매수 기록의 `rule`(단독 판 이름)이 있는 계좌만(25.2325 — 예전엔 리포트는 고르기 값,
    상황실은 계좌 id 로 걸렀다).
    - 뜨는 날: 자리가 있고(`slots`) 하나도 안 샀는데, 후보가 자료 문제·현금 부족으로 넘겨졌거나(`not_bought`) **매수**가
      위험 점검에 막혔거나(`blocked` — 계좌의 `risk_blocked` 는 막힌 **매도**도 세어 쓰지 않는다, 25.2325 교차검증
      [필수]), Z3 이 순위를 하나도 못 낸 날(유니버스 자료 장애 — 마법공식은 유니버스 전체에 순위를 매겨 0 이면 이상이다.
      Z4 는 두 부서가 모두 찬성한 종목만 순위에 들어 0 이 정상일 수 있다)
    - 정상(None): 자리 없음(보유 상한·달 첫 장 아님·국면) · 살 후보가 아예 없음(이미 다 듦) · 매수 단계가 이상으로 일찍
      끝남(`skipped` — 환율 없음 등은 `desk._buy_fault` 가 따로 띄우고 관문 무사고에도 센다, 겹치지 않게)"""
    b = rec.get("buy") or {}
    if not isinstance(b, dict) or b.get("rule") not in RULE_NAMES.values() or not b.get("slots") or b.get("orders"):
        return None
    if b.get("skipped"):
        return None
    # 넘김 상한 표지(열쇠 −1, JSON 으로 되읽으면 '-1')는 종목이 아니다
    못삼 = [v for k, v in (b.get("not_bought") or {}).items() if str(k) != "-1"]
    막힘 = int(b.get("blocked") or 0)
    if b.get("rule") == RULE_NAMES[MAGIC] and not b.get("ranked"):
        return "normal", f"사지 못함 — 순위 낼 자료 없음(유니버스 {int(b.get('universe') or 0)}종목)"
    if not 못삼 and not 막힘:
        return None
    글 = [f"{못삼[0]}" + (f" 외 {len(못삼) - 1}종목" if len(못삼) > 1 else "")] if 못삼 else []
    if 막힘:
        글.append(f"위험 점검에 막힘 {막힘}건")
    return "normal", "사지 못함 — " + " · ".join(글)
