"""팩터 계산과 정규화.

계산식의 단일 정의처는 `docs/factors.md` 다. 여기 있는 함수는 그 문서를
코드로 옮긴 것이고, 문서에 없는 지표는 만들지 않는다.

이 모듈은 **DB 를 모른다.** 숫자를 받아 숫자를 돌려준다. 같은 입력에 항상
같은 값을 주므로 네트워크 없이 손으로 검증할 수 있다. 적재는 jobs/scores.py
가 한다.

값을 만들어 내지 않는 규칙을 단계마다 적용한다.
  분모가 성립하지 않으면 그 지표는 None. 0 으로 두지 않는다
  구성 지표가 절반 미만만 살아 있으면 그 팩터는 None
  팩터가 둘 이상 비면 종합 점수를 내지 않고 사유를 남긴다
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from batch.core import settings_range
from batch.services import metrics as mt

# 계산식이 바뀌면 이 값을 올린다. 과거 행을 덮어쓰지 않고 새 행을 쌓는다.
#   1: 처음 (2026-09-16)
#   2: 모멘텀에 52주 고점·꾸준함·변동성 조정, 리스크에 Amihud 추가 (2026-09-17, docs/factors.md 10.1)
#   3: 퀄리티에 자산 성장률·유동비율·피오트로스키 축소판 추가 (2026-09-17, docs/factors.md 10.2)
#   4: 밸류에 배당수익률, 리스크에 MAX 효과·잔차 변동성 추가 (2026-09-21, docs/factors.md 11장)
#      **퀄리티는 그대로 8개다** — 배당 연속성은 계산 함수만 두고 넣지 않았다.
#      넣으면 절반 규칙의 문턱이 4→5 로 올라 재무가 성긴 종목이 점수를 잃는다(아래 주석)
#   5: 윈저라이즈가 적어도 한 종목씩 자른다 (2026-09-26, docs/infra.md 25.245). 예전엔 100 종목 이하 집단에서 안 잘랐다
#      모멘텀 변동성 조정의 σ 를 표본(n−1)으로 (2026-09-27, 25.274). 판 5 가 운영에서 돌기 전이라 함께 넣었다
#      시장 집단의 비교 대상을 그 시장 전체로 (2026-09-27, 25.307). 같은 이유로 판 5 에 함께 넣었다
#      매출 3년 CAGR 은 4개 연도가 다 있을 때만 (2026-09-27, 25.308). 〃
#      값이 10개 미만인 지표는 윈저라이즈하지 않는다 (2026-09-27, 25.309). 〃
#      연결이 없는 회사는 별도 재무로 (2026-09-27, 25.314 — 입력 쪽 변경). 〃
#   6: 센티먼트는 50 에서 벗어난 만큼만 더하고 뺀다 — 중립이 점수를 깎지 않게 (2026-09-29, docs/infra.md 25.639).
#      판 5 가 운영에서 돌았는지 확인하지 못해 [확인필요] 판을 올려 과거 행과 가른다
#   7: 미회복 낙폭의 회복 기간을 **그만큼도 안 빠진 종목들의 최악값**으로 채운다 — 신고가 다음 날 −0.75% 인 종목이
#      −40% 종목과 같은 최악이 되지 않게 (2026-09-29, docs/infra.md 25.685)
#   8: 그 치환값이 바닥 뒤 지난 행 수보다 좋지 않게 (25.691, 교차검증). 판 7 이 미국 배치에서 한 번 돌았을 수 있어
#      [확인필요] 판을 합치지 않고 올렸다
# 9: 밸류 분모 시점 정합 — 시가총액을 기준일 가격으로 옮긴다 (docs/factors.md 3.1, infra 25.954)
# 10: 옮기는 조건을 "시총 날짜 == 스냅샷" 에서 "시총 날짜 ≤ 스냅샷, 14일 안" 으로 — 미국이 실제로 옮겨진다 (25.960)
CALC_VERSION = 10

FACTORS = ("value", "quality", "growth", "momentum", "risk")

# 비교 집단의 최소 종목 수. 이보다 작으면 한 단계 위 집단으로 올린다.
# 표본 열둘로 낸 z-score 는 순위가 아니라 잡음이다.
MIN_PEER_SIZE = 30

# 윈저라이즈 비율. 상·하위 1% 를 그 분위값으로 자른다.
# 이것이 없으면 비율 지표 하나가 집단 전체의 평균과 표준편차를 지배한다.
WINSOR_PCT = 0.01

# z 를 0~100 으로 옮길 때의 절단. z=+3 이 100점, z=-3 이 0점이다.
Z_CLIP = 3.0


#: 최신 사업보고서가 이보다 오래되면 **연간 재무를 쓰지 않는다** — 결산 뒤 약 3개월에 내고 12개월마다 새로 나온다(3 +
#: 12 + 여유).
#: 신호 쪽 중기 성장률(25.660)과 점수·백테스트(25.804)가 같은 문턱을 쓴다 — 정의처는 여기
STALE_ANNUAL_DAYS = 457


def annual_stale_reason(report_date: str | None, as_of: str, latest_fy: int | None = None) -> str | None:
    """가장 새 사업보고서가 묵었으면 그 사유, 아니면 None.

    두 겹이다. ① 접수일이 기준일보다 `STALE_ANNUAL_DAYS` 넘게 앞섰다(25.660). ② **정정공시가 접수일을 새로
    덮으면 ①을 빠져나가므로**(25.663) 7월 이후인데 최신 회계연도가 재작년 이전이면 묵음이다 — 7월이면 결산월과
    상관없이 작년 회계연도 사업보고서가 나와 있어야 한다. 25.804 가 ①만 옮겨 점수·백테스트에서 이 구멍이
    다시 열렸다(25.808, 교차검증). 접수일·회계연도를 모르면 그 겹은 보지 않는다.
    """
    from datetime import date as _date

    기준 = _date.fromisoformat(str(as_of)[:10])
    if report_date and (기준 - _date.fromisoformat(str(report_date)[:10])).days > STALE_ANNUAL_DAYS:
        연도 = "" if latest_fy is None else f"FY{latest_fy} "
        return f"{연도}사업보고서 접수 {str(report_date)[:10]} — {STALE_ANNUAL_DAYS}일 넘게 새 사업보고서가 없음"
    if latest_fy is not None and 기준.month >= 7 and latest_fy <= 기준.year - 2:
        return f"최신 사업보고서가 FY{latest_fy} — {기준.year - 1} 회계연도 사업보고서가 없음"
    return None


def annual_is_stale(report_date: str | None, as_of: str, latest_fy: int | None = None) -> bool:
    """`annual_stale_reason` 이 사유를 내는가."""
    return annual_stale_reason(report_date, as_of, latest_fy) is not None


@dataclass(frozen=True)
class Metric:
    """지표 하나의 정의.

    higher_is_better 가 False 면 z 에 -1 을 곱해 방향을 맞춘다. 다섯 팩터가
    모두 "높을수록 좋다"로 통일돼야 가중합이 뜻을 가진다.

    missing_is_worst 는 결측을 집단의 최악값으로 치환한다는 뜻이다.
    MDD 회복 기간이 그렇다. 미회복은 값이 없는 것이 아니라 가장 나쁜 상태다.
    """

    name: str
    higher_is_better: bool = True
    missing_is_worst: bool = False
    #: 결측을 "최악" 으로 치환하는 것은 **이 지표가 있을 때만**이다 (docs/infra.md 25.558, 감사 재현). 회복 기간의
    #: None 은 MDD 를 알 때만 "미회복" 이고, 성과지표 행이 없어 MDD 도 모르면 **모름**이다 — 예전에는 둘 다 최악값으로
    #: 채워 표본에 가짜 최악값이 여러 번 들어가 다른 종목들의 z 가 일괄 이동·압축됐다
    known_with: str | None = None
    #: 결측을 채울 때 **이 값보다 좋게 채우지 않는다** — 이미 아는 하한 (25.691).
    #: 미회복 회복 기간이면 바닥 뒤 지난 행 수다
    floor_with: str | None = None


#: 미회복 낙폭의 바닥 뒤 지난 행 수 — 회복 기간의 하한 (25.691). 팩터 지표가 아니라 치환의 재료다
UNRECOVERED_ROWS = "mdd_unrecovered_rows"


def elapsed_rows_since(dates: list[str], trough: str | None) -> float | None:
    """바닥(`trough`) 뒤 계열의 행 수. 바닥이 계열보다 앞이면 계열 전체 길이(그 이상이라는 하한). 모르면 None."""
    if not trough or not dates:
        return None
    return float(sum(1 for d in dates if d > trough))


FACTOR_METRICS: dict[str, tuple[Metric, ...]] = {
    "value": (
        Metric("ep"),  # 이익수익률 = 순이익 / 시가총액
        Metric("bp"),  # 순자산수익률 = 자본총계 / 시가총액
        Metric("sp"),  # 매출수익률 = 매출 / 시가총액
        # 2026-09-21 추가 (docs/factors.md 11.1). 배당 표는 진작 있었는데 점수는 본 적이 없다
        Metric("dividend_yield"),  # 현금배당총액 / 시가총액. 다른 밸류 지표와 분모가 같다
    ),
    "quality": (
        Metric("roe"),
        Metric("roa"),
        Metric("operating_margin"),
        Metric("debt_ratio", higher_is_better=False),
        Metric("profit_stability"),
        # 2026-09-17 추가 (docs/factors.md 10.2)
        Metric("asset_growth", higher_is_better=False),  # 총자산 증가율. Cooper, Gulen & Schill 2008
        Metric("current_ratio"),  # 유동자산 / 유동부채
        Metric("piotroski_lite"),  # F-score 9개 중 낼 수 있는 6개의 합. Piotroski 2000
        # **배당 연속성(`dividend_years`)은 일부러 여기 없다** (2026-09-21, docs/factors.md 11.1).
        # 계산 함수와 테스트는 있다. 넣지 않은 이유는 **절반 규칙의 분모**다 —
        # 8개에서 9개가 되면 문턱이 4개에서 5개로 올라가고, 재무가 성긴 지금
        # 그 한 칸에 걸려 **점수를 통째로 잃는 종목**이 생긴다. 지금은 추천이 0건인 상황이라
        # 문턱을 올릴 때가 아니다. Turso 로 돌아가 백테스트로 확인한 뒤에 넣는다
    ),
    "growth": (
        Metric("revenue_growth"),
        Metric("operating_income_growth"),
        Metric("revenue_cagr_3y"),
    ),
    "momentum": (
        Metric("momentum_12_1"),
        Metric("momentum_6m"),
        Metric("momentum_3m"),
        # 2026-09-17 추가 (docs/factors.md 10.1)
        Metric("high_52w_proximity"),  # 52주 고점 대비 종가. George & Hwang 2004
        Metric("momentum_consistency"),  # 꾸준함. Da, Gurun & Warachka 2014 의 단순화
        Metric("momentum_vol_adjusted"),  # 12-1 / 연환산 변동성. Barroso & Santa-Clara 2015
    ),
    "risk": (
        Metric("mdd_abs", higher_is_better=False),
        Metric("volatility_ann", higher_is_better=False),
        Metric("sharpe"),
        Metric("sortino"),
        Metric("beta_abs", higher_is_better=False),
        Metric(
            "mdd_recovery_days", higher_is_better=False, missing_is_worst=True, known_with="mdd_abs",
            floor_with=UNRECOVERED_ROWS,
        ),
        Metric("cagr"),
        # 2026-09-17 추가. 클수록 거래가 가격을 흔든다 = 빠져나오기 어렵다. Amihud 2002
        Metric("amihud_illiquidity", higher_is_better=False),
        # 2026-09-21 추가 (docs/factors.md 11.2·11.3)
        Metric("max_daily_return", higher_is_better=False),  # 지난 한 달 최대 일간 수익률. Bali 외 2011
        Metric("idio_volatility", higher_is_better=False),  # 시장모형 잔차 변동성. Ang 외 2006
    ),
}


def _swap(factor: str, old: str, new: str | None) -> tuple[Metric, ...]:
    """`factor` 구성에서 `old` 를 `new` 로 바꾼(None 이면 뺀) 지표 묶음."""
    out: list[Metric] = []
    for m in FACTOR_METRICS[factor]:
        if m.name != old:
            out.append(m)
        elif new is not None:
            out.append(Metric(new))
    return tuple(out)


#: **지표 교체 A/B** (docs/factors.md 12.12, 10회차). 이름 → (바뀌는 팩터, 바꾼 구성). 백테스트 한 실행 안에서 현행(A)과
#: 나란히 그 팩터의 IC 를 내 진단 키에 남긴다 — `IC_NAMES`·`verdict` 가 아니다. 운영 점수는 바꾸지 않는다
AB_VARIANTS: dict[str, tuple[str, tuple[Metric, ...]]] = {
    "momentum_echo": ("momentum", _swap("momentum", "momentum_vol_adjusted", "momentum_echo")),
    "momentum_drop_vol_adj": ("momentum", _swap("momentum", "momentum_vol_adjusted", None)),
    "growth_oi_assets": ("growth", _swap("growth", "operating_income_growth", "oi_change_assets")),
    # 12회차 (docs/factors.md 12.14) — 퀄리티 ROA 를 영업이익/총자산으로, 리스크에서 잔차 변동성 빼기
    "quality_op_assets": ("quality", _swap("quality", "roa", "op_assets")),
    "risk_drop_idio": ("risk", _swap("risk", "idio_volatility", None)),
}


# ----------------------------------------------------------------------
# 지표 계산
# ----------------------------------------------------------------------


def safe_ratio(
    numerator: float | None,
    denominator: float | None,
    *,
    positive_denominator: bool = True,
) -> float | None:
    """비율. 분모가 성립하지 않으면 None 을 돌려준다.

    positive_denominator 가 True 면 분모가 0 이하일 때도 None 이다.
    자본잠식 기업의 ROE 는 "나쁜 값"이 아니라 **성립하지 않는 값**이다.
    음수 자본으로 나누면 적자 기업의 ROE 가 양수로 나와 좋아 보인다.
    """
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    if positive_denominator and denominator < 0:
        return None
    return numerator / denominator


def value_metrics(
    net_income: float | None,
    total_equity: float | None,
    revenue: float | None,
    market_cap: float | None,
) -> dict[str, float | None]:
    """밸류 지표. PER 이 아니라 그 역수를 쓴다.

    PER 은 적자에서 음수가 되어 "가장 싼 종목"으로 올라오고, 이익이 0 에
    가까우면 무한대로 발산한다. 역수는 적자를 자연스럽게 음수(나쁨)로 두고
    0 근처에서 연속이다.
    """
    if market_cap is None or market_cap <= 0:
        return {"ep": None, "bp": None, "sp": None}

    # 분자는 음수여도 된다. 적자는 낮은 점수로 표현되어야 하기 때문이다.
    return {
        "ep": safe_ratio(net_income, market_cap),
        "bp": safe_ratio(total_equity, market_cap),
        "sp": safe_ratio(revenue, market_cap),
    }


def quality_metrics(
    net_income: float | None,
    total_assets: float | None,
    total_equity: float | None,
    operating_income: float | None,
    revenue: float | None,
    total_liabilities: float | None = None,
    profitable_years: int | None = None,
    observed_years: int | None = None,
) -> dict[str, float | None]:
    """퀄리티 지표. 자본이 0 이하면 자본 기반 지표는 성립하지 않는다.

    부채총계는 저장된 값을 우선 쓰고, 없으면 자산에서 자본을 빼서 낸다.
    DART 응답에 한쪽만 오는 경우가 있다.
    """
    liabilities = (
        total_liabilities
        if total_liabilities is not None
        else total_liabilities_of(total_assets, total_equity)
    )
    return {
        "roe": safe_ratio(net_income, total_equity),
        "roa": safe_ratio(net_income, total_assets),
        # A/B 전용(12.14, `quality_op_assets`) — `FACTOR_METRICS` 밖이라 운영 점수·기록에 들어가지 않는다.
        # 금융업도 ROA 와 같게 값을 낸다(A 와 같은 처리, 검증 A·B 조건)
        "op_assets": safe_ratio(operating_income, total_assets),
        "operating_margin": safe_ratio(operating_income, revenue),
        "debt_ratio": safe_ratio(liabilities, total_equity),
        "profit_stability": profit_stability(profitable_years, observed_years),
    }


def total_liabilities_of(
    total_assets: float | None, total_equity: float | None
) -> float | None:
    """부채총계. 자산에서 자본을 뺀다.

    financials 에 total_liabilities 가 따로 있지만, 둘 중 하나가 비는 경우가
    있어 자산−자본으로도 낼 수 있게 둔다. 호출부가 실제 컬럼을 우선한다.
    """
    if total_assets is None or total_equity is None:
        return None
    return total_assets - total_equity


def profit_stability(
    profitable_years: int | None, observed_years: int | None
) -> float | None:
    """관측 연수 중 영업흑자 연수의 비율. 3년 미만이면 None."""
    if profitable_years is None or observed_years is None:
        return None
    if observed_years < 3:
        return None
    if profitable_years < 0 or profitable_years > observed_years:
        raise ValueError("흑자 연수가 관측 연수를 넘을 수 없습니다")
    return profitable_years / observed_years


def same_currency(*rows: Mapping[str, Any] | None) -> bool:
    """재무 행들의 통화가 모두 같은가 (docs/infra.md 25.919, 11회차).

    통화를 모르는 행(옛 스냅샷)은 견줄 수 없으니 막지 않는다 — 모르는 것을 자르면 옛 표본 전체가 빈다.
    비교할 값은 `currency` 열(또는 키)이다.
    """
    통화들 = {str(r.get("currency")) for r in rows if r is not None and r.get("currency")}
    return len(통화들) <= 1


def growth_rate(current: float | None, previous: float | None) -> float | None:
    """전년 대비 성장률. 전년이 0 이하이면 None.

    적자에서 적자 축소는 성장률로 표현되지 않는다. -100억에서 -10억은
    "90% 성장"이 아니다. 분모가 음수면 부호가 뒤집혀 순위가 거꾸로 선다.
    """
    if current is None or previous is None:
        return None
    if previous <= 0:
        return None
    return current / previous - 1


def cagr(current: float | None, past: float | None, years: int) -> float | None:
    """연평균 성장률. 과거 값이 0 이하이면 None."""
    if current is None or past is None or past <= 0 or years <= 0:
        return None
    if current <= 0:
        # 매출이 음수가 되는 일은 없지만, 0 이면 거듭제곱근이 뜻을 잃는다
        return None
    return (current / past) ** (1 / years) - 1


def has_cagr_years(years: object, latest: int) -> bool:
    """매출 3년 CAGR 에 필요한 **4개 사업연도가 모두 있는가** (docs/factors.md 3.3, docs/infra.md 25.308).

    예전에는 당해와 3년 전만 봐서, 중간 두 해가 비어도(합병·수집 누락) CAGR 이 나왔다.
    `years` 는 사업연도를 키로 가진 것이면 무엇이든 된다(`in` 만 쓴다). 점수와 백테스트가 같은 규칙을 쓴다.
    """
    return all((latest - k) in years for k in (0, 1, 2, 3))  # type: ignore[operator]


def oi_change_assets(
    operating_income: float | None, prev_operating_income: float | None, prev_total_assets: float | None
) -> float | None:
    """영업이익 변화 / 전년 총자산 — **A/B 진단 지표** (docs/factors.md 12.12, 10회차 2).

    `growth_rate` 는 전년 영업이익이 0 이하면 None 이라 흑자전환·적자축소 종목이 성장 팩터에서 빠진다(국내 22%,
    12.11 진단). 분모를 전년 총자산으로 바꾸면 부호가 뒤집히지 않는다. 셋 중 하나라도 없거나 전년 총자산이
    0 이하면 None.
    """
    if operating_income is None or prev_operating_income is None or prev_total_assets is None:
        return None
    if prev_total_assets <= 0:
        return None
    return (operating_income - prev_operating_income) / prev_total_assets


def growth_metrics(
    revenue: float | None,
    prev_revenue: float | None,
    operating_income: float | None,
    prev_operating_income: float | None,
    revenue_3y_ago: float | None = None,
    prev_total_assets: float | None = None,
) -> dict[str, float | None]:
    return {
        "revenue_growth": growth_rate(revenue, prev_revenue),
        "operating_income_growth": growth_rate(operating_income, prev_operating_income),
        "revenue_cagr_3y": cagr(revenue, revenue_3y_ago, 3),
        # A/B 진단 — 점수에 넣지 않는다 (12.12)
        "oi_change_assets": oi_change_assets(operating_income, prev_operating_income, prev_total_assets),
    }


# ----------------------------------------------------------------------
# 재무제표 지표 (2026-09-17 추가, docs/factors.md 10.2)
# 적재(jobs/scores)와 백테스트(jobs/backtest)가 같은 statement_metrics 를 부른다
# ----------------------------------------------------------------------

# 피오트로스키 축소판의 조건 수. 원본 9개 중 저장 컬럼으로 낼 수 있는 것만
PIOTROSKI_LITE_CONDITIONS = 6


def _field(row: Mapping[str, Any] | None, key: str) -> float | None:
    """재무 행(dict)에서 숫자 하나. 행이 없거나 값이 없으면 None."""
    if row is None:
        return None
    value = row.get(key)
    return None if value is None else float(value)


def asset_growth(total_assets: float | None, prev_total_assets: float | None) -> float | None:
    """총자산 증가율. 전년이 0 이하면 None. 높을수록 나쁘다(FACTOR_METRICS 에서 방향 지정)."""
    return growth_rate(total_assets, prev_total_assets)


def current_ratio(
    current_assets: float | None, current_liabilities: float | None
) -> float | None:
    """유동비율. 유동부채가 0 이하면 성립하지 않는다."""
    return safe_ratio(current_assets, current_liabilities)


def _leverage_pair(
    cur: Mapping[str, Any] | None, prev: Mapping[str, Any] | None
) -> tuple[float | None, float | None]:
    """조건 3 의 레버리지 두 해. 비유동부채가 두 해 다 있으면 그것, 아니면 두 해 다 부채총계.

    같은 종목의 두 해는 반드시 같은 잣대여야 한다. 한 해는 비유동부채, 한 해는
    부채총계로 비교하면 감소한 것처럼 보일 뿐이다.
    """
    for key in ("noncurrent_liabilities", "total_liabilities"):
        a, b = _field(cur, key), _field(prev, key)
        if a is not None and b is not None:
            return (
                safe_ratio(a, _field(cur, "total_assets")),
                safe_ratio(b, _field(prev, "total_assets")),
            )
    return None, None


def piotroski_lite(
    cur: Mapping[str, Any] | None, prev: Mapping[str, Any] | None
) -> int | None:
    """피오트로스키 F-score 축소판. 6개 조건 중 참인 개수(0~6).

    하나라도 판정할 수 없으면 None. 부분 합은 낮은 쪽으로 치우쳐 "모르는 것"이
    "나쁜 것"으로 읽히기 때문이다. 조건 목록과 원본과의 차이는 docs/factors.md 10.2.
    """
    if cur is None or prev is None:
        return None
    roa_now = safe_ratio(_field(cur, "net_income"), _field(cur, "total_assets"))
    roa_prev = safe_ratio(_field(prev, "net_income"), _field(prev, "total_assets"))
    lev_now, lev_prev = _leverage_pair(cur, prev)
    cr_now = current_ratio(_field(cur, "current_assets"), _field(cur, "current_liabilities"))
    cr_prev = current_ratio(_field(prev, "current_assets"), _field(prev, "current_liabilities"))
    margin_now = safe_ratio(_field(cur, "operating_income"), _field(cur, "revenue"))
    margin_prev = safe_ratio(_field(prev, "operating_income"), _field(prev, "revenue"))
    turn_now = safe_ratio(_field(cur, "revenue"), _field(cur, "total_assets"))
    turn_prev = safe_ratio(_field(prev, "revenue"), _field(prev, "total_assets"))

    pairs: list[tuple[float | None, float | None]] = [
        (roa_now, 0.0),  # 1 ROA > 0
        (roa_now, roa_prev),  # 2 ROA 개선
        (lev_prev, lev_now),  # 3 레버리지 감소 (전년 > 당해)
        (cr_now, cr_prev),  # 4 유동비율 개선
        (margin_now, margin_prev),  # 5 이익률 개선
        (turn_now, turn_prev),  # 6 자산회전율 개선
    ]
    if any(a is None or b is None for a, b in pairs):
        return None
    return sum(1 for a, b in pairs if a > b)  # type: ignore[operator]


def statement_metrics(
    cur: Mapping[str, Any] | None, prev: Mapping[str, Any] | None
) -> dict[str, float | None]:
    """재무제표로 내는 세 지표. 적재와 백테스트가 둘 다 이 함수를 부른다.

    cur/prev 는 최근 사업연도와 그 전 해의 재무 행(dict). 키는 financials 컬럼 이름과 같다.
    """
    score = piotroski_lite(cur, prev)
    return {
        "asset_growth": asset_growth(_field(cur, "total_assets"), _field(prev, "total_assets")),
        "current_ratio": current_ratio(
            _field(cur, "current_assets"), _field(cur, "current_liabilities")
        ),
        "piotroski_lite": None if score is None else float(score),
    }


# 모멘텀에 쓰는 거래일 수. docs/factors.md 3.4 절.
MOMENTUM_SKIP_DAYS = 21  # 최근 한 달. 단기 반전 효과를 덜어낸다
MOMENTUM_12M_DAYS = 252
MOMENTUM_6M_DAYS = 126
MOMENTUM_3M_DAYS = 63


def _growth_between(end: float | None, start: float | None) -> float | None:
    """시작 시점 대비 도착 시점의 수익률. 한쪽이 없거나 시작이 0 이하면 None."""
    if end is None or start is None or start <= 0:
        return None
    return end / start - 1


def momentum_from_points(
    p_now: float | None,
    p_21: float | None,
    p_63: float | None,
    p_126: float | None,
    p_273: float | None,
) -> dict[str, float | None]:
    """기준점 다섯 개로 모멘텀을 낸다. 숫자는 거래일 수만큼 거슬러 간 종가다.

    이 형태를 따로 둔 이유는 적재 쪽에서 종목마다 300일치를 통째로 읽지 않기
    위해서다. 필요한 날짜 다섯 개만 조회하면 왕복과 행 수가 크게 준다.
    계열에서 뽑든 날짜로 뽑든 계산식은 여기 하나뿐이다.
    """
    return {
        # 최근 한 달(21 거래일)을 빼고 그 앞 12개월을 본다.
        "momentum_12_1": _growth_between(p_21, p_273),
        "momentum_6m": _growth_between(p_now, p_126),
        "momentum_3m": _growth_between(p_now, p_63),
        # 에코 모멘텀 — **점수에 넣지 않는 A/B 진단 지표** (docs/factors.md 12.12, 10회차 1).
        # Novy-Marx 2012 의 12-7 을 이 저장소 12-1 관례(한 달 건너뛰기)로: 126 → 273 거래일.
        # `FACTOR_METRICS` 에 없어 `raw_json` 에도 실리지 않는다
        "momentum_echo": _growth_between(p_126, p_273),
    }


def momentum_metrics(closes: list[float]) -> dict[str, float | None]:
    """가격 계열에서 기준점을 뽑아 momentum_from_points 로 넘긴다.

    closes 는 날짜 오름차순이고 마지막이 기준일 종가다. 거래일이 모자라면
    그 지표는 None 이다. 없는 구간을 있는 데이터로 늘려 계산하지 않는다.
    """

    def at(back: int) -> float | None:
        return closes[-1 - back] if len(closes) > back else None

    return momentum_from_points(
        at(0),
        at(MOMENTUM_SKIP_DAYS),
        at(MOMENTUM_3M_DAYS),
        at(MOMENTUM_6M_DAYS),
        at(MOMENTUM_SKIP_DAYS + MOMENTUM_12M_DAYS),
    )


# 조회해야 하는 기준점의 거래일 수. 적재 쪽이 이 목록으로 날짜를 고른다.
MOMENTUM_OFFSETS = (
    0,
    MOMENTUM_SKIP_DAYS,
    MOMENTUM_3M_DAYS,
    MOMENTUM_6M_DAYS,
    MOMENTUM_SKIP_DAYS + MOMENTUM_12M_DAYS,
)


# ----------------------------------------------------------------------
# 가격·거래량 계열에서 내는 지표 (2026-09-17, docs/factors.md 10.1)
#
# 전부 날짜 오름차순 계열을 받고 마지막이 기준일이다. 적재 경로와 백테스트
# 경로가 같은 함수를 부른다. 계산식은 여기 하나뿐이다.
# ----------------------------------------------------------------------

HIGH_52W_DAYS = 252  # 52주 = 기존 12개월 모멘텀 창과 같은 거래일 수
CONSISTENCY_MIN_RETURNS = 126  # 12-1 창(252 수익률)의 절반. 이보다 적으면 모른다
AMIHUD_DAYS = 63  # 3개월. 유동성은 변하므로 최근 상태를 본다
AMIHUD_MIN_DAYS = 30  # 거래대금이 있는 날이 이보다 적으면 모른다
AMIHUD_VALUE_UNIT = 1_000_000_000  # 거래대금 10억당. 단위는 시장마다 다르지만 z 는 시장 안에서 낸다
TRADING_DAYS_PER_YEAR = 252
# 연환산 변동성이 이 값 이하이면 0 으로 본다 (부동소수 오차 방지. 실제 종목은 1% 미만도 드물다)
VOL_ANN_EPS = 1e-6


def _daily_returns(
    closes: list[float], dates: list[str] | None = None
) -> list[float | None]:
    """연속한 두 종가의 수익률. 앞 값이 0 이하면 그 자리는 None.

    **`dates` 를 주면 구멍을 사이에 둔 쌍도 None 이다** (2026-09-22, docs/metrics.md 0장).
    두 행이 `MAX_SESSION_GAP_DAYS`(11일, 실측) 보다 벌어져 있으면 그 움직임은
    **하루치가 아니다** — 2주 쉬었다 재개한 종목의 첫날은 그동안 쌓인 것이다.

    문턱은 `services/metrics` 에서 가져온다. **새 숫자를 만들지 않는다** —
    같은 규칙이 두 곳에 있으면 한 곳만 고쳐진다(docs/infra.md 25.0).

    `dates` 를 안 주면 예전처럼 **전부 하루치로 센다.** 날짜를 못 가진 부르는 쪽이
    아직 있어서다(`docs/infra.md` 25.103 에 어디인지 적어 두었다).
    """
    if dates is not None and len(dates) != len(closes):
        raise ValueError(f"날짜 {len(dates)}개와 종가 {len(closes)}개의 길이가 다릅니다")
    out: list[float | None] = []
    for i, (prev, cur) in enumerate(zip(closes, closes[1:], strict=False)):
        if prev is None or cur is None or prev <= 0:
            out.append(None)
            continue
        if dates is not None and _벌어짐(dates[i], dates[i + 1]) > mt.MAX_SESSION_GAP_DAYS:
            out.append(None)
            continue
        out.append(cur / prev - 1)
    return out


def _벌어짐(앞: str, 뒤: str) -> int:
    """두 날짜 사이 달력 날수. 읽을 수 없는 날짜는 **0 으로 친다**(막지 않는다)."""
    from datetime import date

    try:
        return (date.fromisoformat(뒤) - date.fromisoformat(앞)).days
    except (TypeError, ValueError):
        return 0


def high_52w_proximity(closes: list[float]) -> float | None:
    """종가 / 최근 252 거래일 최고 종가. 0~1 이고 1 이면 지금이 고점이다.

    장중 고가가 아니라 **종가**를 쓴다. 백테스트 가격 창에는 고가가 없고,
    종가 기준이 더 보수적이다. 252일이 다 있어야 한다.
    """
    if len(closes) < HIGH_52W_DAYS:
        return None
    window = [c for c in closes[-HIGH_52W_DAYS:] if c is not None]
    if len(window) < HIGH_52W_DAYS:
        return None
    peak = max(window)
    if peak <= 0:
        return None
    return closes[-1] / peak


def _formation_bounds(n: int) -> tuple[int, int] | None:
    """12-1 창의 (시작, 끝) 자리. 최근 21일을 뺀 그 앞 252일(종가 253개).

    **자리로 돌려주는 이유**: 종가와 날짜를 **같은 자리로** 잘라야 짝이 유지된다.
    예전에는 종가만 잘라 돌려줬는데, 날짜를 함께 태우면서 자르는 규칙이 두 벌이 될
    뻔했다 (2026-09-22).
    """
    need = MOMENTUM_SKIP_DAYS + MOMENTUM_12M_DAYS + 1
    if n < need:
        return None
    end = n - MOMENTUM_SKIP_DAYS
    return (end - MOMENTUM_12M_DAYS - 1, end)


def _formation_window(closes: list[float]) -> list[float] | None:
    """12-1 창의 종가."""
    bounds = _formation_bounds(len(closes))
    return None if bounds is None else closes[bounds[0] : bounds[1]]


def momentum_consistency(closes: list[float], dates: list[str] | None = None) -> float | None:
    """꾸준함. 12-1 창의 일간 수익률 중 (양수 비율 − 음수 비율).

    Da, Gurun & Warachka (2014) 의 정보 이산성을 단조 지표로 **단순화**한 것이다
    (원식은 수익률 부호를 곱해 방향이 조건부다). 조금씩 꾸준히 오른 종목은 양수,
    하루 급등으로 오른 종목은 0 근처, 꾸준히 내린 종목은 음수다.
    """
    bounds = _formation_bounds(len(closes))
    if bounds is None:
        return None
    window = closes[bounds[0] : bounds[1]]
    창날짜 = None if dates is None else dates[bounds[0] : bounds[1]]
    returns = [r for r in _daily_returns(window, 창날짜) if r is not None]
    if len(returns) < CONSISTENCY_MIN_RETURNS:
        return None
    positive = sum(1 for r in returns if r > 0)
    negative = sum(1 for r in returns if r < 0)
    return (positive - negative) / len(returns)


def momentum_vol_adjusted(closes: list[float], dates: list[str] | None = None) -> float | None:
    """12-1 수익률 / 같은 창의 연환산 변동성.

    Barroso & Santa-Clara (2015) 는 포트폴리오 수준에서 변동성으로 스케일링했다.
    여기서는 종목별로 옮겼다. 같은 수익률이면 덜 흔들린 쪽이 높다.
    """
    bounds = _formation_bounds(len(closes))
    if bounds is None:
        return None
    window = closes[bounds[0] : bounds[1]]
    창날짜 = None if dates is None else dates[bounds[0] : bounds[1]]
    ret_12_1 = _growth_between(window[-1], window[0])
    returns = [r for r in _daily_returns(window, 창날짜) if r is not None]
    if ret_12_1 is None or len(returns) < CONSISTENCY_MIN_RETURNS:
        return None
    # **표본 표준편차(n−1)** 다 (docs/infra.md 25.274). docs/metrics.md 3장의 연환산 변동성 정의이고
    # `metrics.stdev` 와 같다.
    # 2026-09-27 까지 여기만 모집단(n)으로 나눴다 — 테스트가 그 식을 그대로 옮겨 지키고 있었다
    sigma = mt.stdev(returns)
    if sigma is None:
        return None
    vol_ann = mt.annualize_volatility(sigma)
    # 매일 같은 비율로 움직인 급수는 부동소수 오차로 분산이 1e-30 쯤 남는다.
    # 그대로 나누면 1e14 같은 값이 튀므로 연 0.0001% 아래는 변동성 없음으로 본다
    if vol_ann <= VOL_ANN_EPS:
        return None
    return ret_12_1 / vol_ann


def amihud_illiquidity(
    closes: list[float], values: list[float | None] | None, dates: list[str] | None = None
) -> float | None:
    """Amihud (2002). 최근 63 거래일의 mean(|일간 수익률| / (거래대금 / 10억)).

    클수록 적은 돈으로 가격이 크게 움직인다 = 팔고 나오기 어렵다.
    거래대금이 0 이거나 없는 날은 뺀다. 남은 날이 30일 미만이면 모른다.
    """
    if values is None or len(values) != len(closes) or len(closes) < 2:
        return None
    start = max(0, len(closes) - AMIHUD_DAYS - 1)
    window_closes = closes[start:]
    window_dates = None if dates is None else dates[start:]
    window_values = values[start + 1 :]  # 수익률은 다음 날 것이라 한 칸 민다
    ratios: list[float] = []
    for r, v in zip(_daily_returns(window_closes, window_dates), window_values, strict=False):
        if r is None or v is None or v <= 0:
            continue
        ratios.append(abs(r) / (v / AMIHUD_VALUE_UNIT))
    if len(ratios) < AMIHUD_MIN_DAYS:
        return None
    return sum(ratios) / len(ratios)


# ----------------------------------------------------------------------
# 2026-09-21 추가 (docs/factors.md 11장)
# ----------------------------------------------------------------------

#: 배당 연속성을 볼 사업연도 수. `profit_stability`(이익 안정성)와 같은 창이다
DIVIDEND_YEARS_WINDOW = 5
#: 관측 연수가 이보다 적으면 모른다. 이익 안정성과 같은 하한
DIVIDEND_MIN_YEARS = 3
#: MAX 효과의 창. `MOMENTUM_SKIP_DAYS`(지난 한 달)와 **같은 숫자**다 — 새 값을 만들지 않았다
MAX_RETURN_DAYS = 21
#: 그 창에서 일간 수익률이 이보다 적으면 모른다
MAX_RETURN_MIN_DAYS = 10
#: 잔차 변동성에 필요한 겹치는 날. 베타와 같은 하한(jobs/metrics.MIN_BENCHMARK_POINTS)
IDIO_MIN_POINTS = 60
#: 잔차 변동성을 재는 창(겹치는 **일간 수익률** 개수). 1년치다 (docs/factors.md 11.3).
#:
#: **2026-09-21 까지 창이 정해져 있지 않았다.** 문서가 하한(60)만 적고 위를 안 적어서,
#: 적재는 274일(모멘텀 창을 얻어 쓴 값)·백테스트는 **전 구간**(최대 5년)으로 쟀다.
#: 같은 이름의 지표가 두 경로에서 **다른 추정량**이었다 (docs/infra.md 25.99).
#:
#: 252 로 정한 근거: 이 저장소의 1년 창 관례(`services/metrics.MIN_POINTS["1Y"]`·
#: `TRADING_DAYS_PER_YEAR`)와 맞고, 실무에서 쓰는 IVOL 창도 대개 1년이다.
#: 짧으면(Ang 외 원논문의 한 달) 하한 60 을 못 채우는 종목이 많아진다
#: `[확인필요: 백테스트로 창 길이를 재 본다]`.
IDIO_WINDOW_DAYS = 252
# 연환산 계수(`TRADING_DAYS_PER_YEAR`)는 **위에 이미 있다**(428행). 2026-09-21 에 여기 다시
# 적었다가 지웠다 — 모듈을 읽을 때 나중 것이 앞을 덮어 **앞의 정의가 죽은 상수**가 된다.
# 그러면 앞줄만 고친 사람은 값이 안 변하는 이유를 못 찾는다 (docs/infra.md 25.48·25.92)


def us_dividend_or_zero(total: float | None, dividend_year: int | None, latest_10k_year: int | None) -> float | None:
    """미국: 알 수 있던 가장 최근 10-K 연도에 배당 행이 없으면 **무배당(0)** (docs/infra.md 25.557·25.561).

    SEC 수집은 배당 태그가 없는 해에 행을 만들지 않는다. 그래서 무배당 종목은 행이 없어 NULL, 배당을 끊은 종목은
    옛 지급액이 남았다. **적재(`jobs/scores`)와 백테스트(`jobs/backtest`)가 이 한 함수를 쓴다** — 25.557 은 적재에만
    넣어 둘이 갈렸다. `latest_10k_year` 는 **배당 행이 보이는 날과 같은 잣대**로 골라야 한다: 배당 행의
    `as_of_date` 는 제출 **다음 거래일**이라, 10-K 도 제출일이 기준일보다 **앞선** 것만 센다(같은 날이면 아직 아니다)
    """
    if latest_10k_year is None:
        return total
    if dividend_year is None or dividend_year < latest_10k_year:
        return 0.0
    return total


def dividend_yield(cash_dividend_total: float | None, market_cap: float | None) -> float | None:
    """배당수익률 = 현금배당총액 / 시가총액 (docs/factors.md 11.1).

    **주당배당금이 아니라 총액을 쓴다.** 액면분할이 있으면 주당 값은 해마다 비교할 수 없다.
    총액을 시가총액으로 나누면 E/P·B/P·S/P 와 **분모가 같아** 밸류 축의 잣대가 하나가 된다.

    `cash_dividend_total` 이 None 이면 **모르는 것**이다(수집이 안 닿았다).
    무배당은 **0 이지 None 이 아니다** — 부르는 쪽이 그 구별을 한다(jobs/scores).
    """
    if market_cap is None or market_cap <= 0 or cash_dividend_total is None:
        return None
    if cash_dividend_total < 0:
        return None
    return cash_dividend_total / market_cap


def dividend_years(totals: list[float | None]) -> float | None:
    """배당 연속성 = 배당한 연수 ÷ 관측 연수 (docs/factors.md 11.1).

    `totals` 는 최근 사업연도부터의 현금배당총액이다. None 인 해는 **관측하지 못한 해**라
    분모에서도 뺀다 — 0 원을 준 해(무배당)와 다르다.

    `profit_stability` 와 같은 모양이고 하한도 같다(관측 3년 미만이면 모른다).
    """
    본것 = [x for x in totals[:DIVIDEND_YEARS_WINDOW] if x is not None]
    if len(본것) < DIVIDEND_MIN_YEARS:
        return None
    return sum(1 for x in 본것 if x > 0) / len(본것)


def max_daily_return(closes: list[float], dates: list[str] | None = None) -> float | None:
    """지난 한 달의 **최대 일간 수익률** (docs/factors.md 11.2).

    Bali, Cakici & Whitelaw (2011). 하루 크게 튄 종목은 이후 수익률이 낮다 —
    복권 같은 종목에 사람이 웃돈을 준다.

    **변동성과 다른 것을 본다.** 변동성은 퍼짐이고 이것은 **한 번의 꼬리**다.
    """
    if len(closes) < 2:
        return None
    창 = closes[-(MAX_RETURN_DAYS + 1) :]
    창날짜 = None if dates is None else dates[-(MAX_RETURN_DAYS + 1) :]
    수익률 = [r for r in _daily_returns(창, 창날짜) if r is not None]
    if len(수익률) < MAX_RETURN_MIN_DAYS:
        return None
    return max(수익률)


def aligned_returns(
    stock: dict[str, float], market: dict[str, float]
) -> tuple[list[float], list[float]]:
    """겹치는 날짜만 남겨 일간 수익률 두 줄 (docs/factors.md 11.3).

    **날짜를 맞추는 규칙이 한 곳에 있어야 한다.** 적재(`jobs/scores`)와
    백테스트(`jobs/backtest`)가 이 함수를 함께 부른다 — 각자 맞추면 갈라진다
    (docs/infra.md 25.0 "한 규칙이 두 곳에 있다").

    값이 0 이하인 날은 뺀다 — 거래정지 종목에서 0 이 들어오면 무한대가 되거나
    부호가 뒤집힌다(`_daily_returns` 와 같은 규칙).
    """
    공통 = sorted(set(stock) & set(market))
    if len(공통) < 2:
        return [], []
    s_ret, m_ret = [], []
    for 앞, 뒤 in zip(공통, 공통[1:], strict=False):
        sp, sc_, mp, mc = stock[앞], stock[뒤], market[앞], market[뒤]
        if sp <= 0 or sc_ <= 0 or mp <= 0 or mc <= 0:
            continue
        # **11일 넘게 벌어진 쌍은 하루치가 아니다** (docs/infra.md 25.228). `metrics.paired_returns`·
        # `_daily_returns` 가 지키는 규칙인데 이 자리만 빠져 있었다 — 거래정지 뒤 재개 하루가 잔차 변동성을 끌었다
        if _벌어짐(앞, 뒤) > mt.MAX_SESSION_GAP_DAYS:
            continue
        s_ret.append(sc_ / sp - 1)
        m_ret.append(mc / mp - 1)
    return s_ret, m_ret


def idio_volatility(
    stock_returns: list[float], market_returns: list[float]
) -> float | None:
    """시장모형 잔차의 연환산 변동성 (docs/factors.md 11.3).

    Ang, Hodrick, Xing & Zhang (2006). 시장으로 설명되지 않는 변동이 큰 종목이
    이후 수익률이 낮다.

    **날짜를 맞춘 수익률 두 줄을 받는다.** 맞추는 일은 부르는 쪽이 한다
    (`services/metrics.align` — 베타와 같은 길). 여기서 다시 맞추면 규칙이 두 곳에 생긴다.

    잔차 e = r_i − (α + β·r_m) 의 **표본**표준편차 × √252.

    **창을 여기서 자른다** (`IDIO_WINDOW_DAYS`, 2026-09-21). 부르는 쪽마다 자르게 두면
    갈라진다 — 실제로 적재는 274일, 백테스트는 전 구간이었다(docs/infra.md 25.99).
    자르는 자리를 **계산 함수 안에** 두면 부르는 쪽이 무엇을 넘기든 같은 창이 된다.

    뒤에서 자른다(최근 것을 남긴다). 두 계열은 이미 `aligned_returns` 가 날짜를 맞춰
    같은 길이로 준 것이라, 같은 개수만큼 뒤를 남기면 짝이 유지된다.
    """
    stock_returns = stock_returns[-IDIO_WINDOW_DAYS:]
    market_returns = market_returns[-IDIO_WINDOW_DAYS:]
    n = min(len(stock_returns), len(market_returns))
    if n < IDIO_MIN_POINTS:
        return None
    r_i = stock_returns[:n]
    r_m = market_returns[:n]
    평균_i = sum(r_i) / n
    평균_m = sum(r_m) / n
    시장분산 = sum((m - 평균_m) ** 2 for m in r_m) / (n - 1)
    if 시장분산 == 0:
        return None  # 회귀가 성립하지 않는다
    공분산 = sum((s - 평균_i) * (m - 평균_m) for s, m in zip(r_i, r_m, strict=True)) / (n - 1)
    beta = 공분산 / 시장분산
    alpha = 평균_i - beta * 평균_m
    잔차 = [s - (alpha + beta * m) for s, m in zip(r_i, r_m, strict=True)]
    남은분산 = sum(e**2 for e in 잔차) / (n - 1)
    return math.sqrt(남은분산) * math.sqrt(TRADING_DAYS_PER_YEAR)


# ----------------------------------------------------------------------
# 분기 실적 서프라이즈 SUE (docs/factors.md 11.4). 2026-09-21
# ----------------------------------------------------------------------
#
# 2026-09-30 정정 (docs/infra.md 25.734, 재무 감사 7번): "사업보고서만 받아 늘 None" 은 낡았다.
# `jobs/financials.py` 는 기본으로 사업·반기·1분기·3분기를 모두 받는다(25.434·25.455, docs/factors.md 11.4).
# 지금 이 값을 쓰는 곳은 **백테스트의 SUE 전략뿐**이다(`jobs/backtest.py`). 운영 DB 에 분기 행이 쌓였는지는 [확인필요].
#
# **팩터에 등록하지 않았다.** 값을 만들 수 없는 지표를 `FACTOR_METRICS` 에 넣으면
# 절반 규칙의 분모만 키워 멀쩡한 종목의 점수를 지운다(docs/infra.md 25.92).
# 중기 신호(docs/signals.md 1.2)에는 아직 잇지 않았다 — 백테스트가 효용을 보인 뒤에 잇는다(CLAUDE.md 기법 발굴 루프 4).

#: 계절성 주기. 4분기 전과 견준다 — 직전 분기와 견주면 계절 변동이 실적 변화로 읽힌다
SUE_SEASON = 4
#: 표준편차를 내는 데 쓰는 전년동기 대비 변화의 수. 12분기(3년)치 실적이 필요하다
SUE_MIN_CHANGES = 8


def yoy_changes(quarterly: list[float | None]) -> list[float | None]:
    """분기 계열에서 **전년동기 대비 변화** (docs/factors.md 11.4).

    `quarterly` 는 **오래된 것부터**다. 어느 쪽 한 분기라도 모르면 그 변화는 None —
    모르는 것을 0 으로 채우면 없는 안정성을 지어낸다(docs/infra.md 25.74).

    길이는 항상 `len(quarterly) - SUE_SEASON` 이다. **자리를 유지한다** — 빠진 것을
    솎아 내면 뒤의 값이 앞으로 당겨져 "최근 8개" 가 실제로는 3년 전 것을 섞는다.
    """
    if len(quarterly) <= SUE_SEASON:
        return []
    나온것: list[float | None] = []
    for i in range(SUE_SEASON, len(quarterly)):
        지금, 작년 = quarterly[i], quarterly[i - SUE_SEASON]
        나온것.append(None if 지금 is None or 작년 is None else 지금 - 작년)
    return 나온것


def sue(quarterly_net_income: list[float | None], market_cap: float | None) -> float | None:
    """분기 실적 서프라이즈 (docs/factors.md 11.4). `quarterly_net_income` 은 **오래된 것부터**.

    `(당분기 − 4분기 전) / stdev(최근 8개 전년동기 대비 변화)`.

    Bernard & Thomas (1989). 실적 발표 뒤 주가가 며칠에 걸쳐 같은 방향으로 밀린다(PEAD).

    **EPS 가 아니라 순이익을 시가총액으로 나눈 값**을 쓴다. 주식수 이력이 없어서다.
    주식수가 안 변했다는 가정이고 유상증자가 있으면 틀린다 `[확인필요: 주식수 수집]`.

    알고 쓰는 것 하나: **시가총액이 하나뿐이면 그 나눗셈은 약분돼 사라진다.**
    분자도 분모도 같은 값으로 나뉘기 때문이다(SUE 는 원래 단위가 없는 값이다).
    그러므로 지금 `market_cap` 이 하는 일은 **0 이하를 걸러내는 문지기**뿐이다.
    분기마다 다른 시가총액을 쓰게 되면 그때는 약분되지 않는다 — 그때 이 주석을 고쳐라.
    없는 효과를 있다고 적어 두면 다음 사람이 그것을 믿는다.

    **분모에 당분기 변화가 들어간다.** `docs/factors.md` 11.4 를 글자 그대로 옮긴 것이다.
    표준(Foster 1977)은 **이전** 8개로 정규화한다 — 그쪽이 큰 서프라이즈가 제 분모를
    부풀리지 않아 더 날카롭지만, 분기가 12개가 아니라 13개 필요하다. 지금은 분기 수집
    자체가 없어 재어 볼 수 없다 `[확인필요: 켠 뒤 둘을 나란히 본다]`.

    None 인 분기가 섞여 살아 있는 변화가 8개에 못 미치면 None. 표준편차가 0 이어도 None —
    한 번도 안 변한 실적에서 "몇 표준편차" 는 뜻이 없다.
    """
    if market_cap is None or market_cap <= 0:
        return None
    정규화 = [None if x is None else x / market_cap for x in quarterly_net_income]
    변화 = yoy_changes(정규화)
    if not 변화 or 변화[-1] is None:
        return None  # 당분기 변화를 모르면 분자가 없다
    창 = [x for x in 변화[-SUE_MIN_CHANGES:] if x is not None]
    if len(창) < SUE_MIN_CHANGES:
        return None
    평균 = sum(창) / len(창)
    분산 = sum((x - 평균) ** 2 for x in 창) / (len(창) - 1)
    표준편차 = math.sqrt(분산)
    if 표준편차 == 0:
        return None
    return 변화[-1] / 표준편차


def price_series_metrics(
    closes: list[float],
    values: list[float | None] | None = None,
    dates: list[str] | None = None,
) -> dict[str, float | None]:
    """계열로 내는 다섯 지표. 적재와 백테스트가 둘 다 이 함수를 부른다.

    `dates` 를 주면 **구멍을 사이에 둔 쌍을 하루치로 세지 않는다**
    (2026-09-22, docs/infra.md 25.103). `high_52w_proximity` 만은 수익률이 아니라
    **최고 종가**를 보므로 날짜와 무관하다.
    """
    return {
        "high_52w_proximity": high_52w_proximity(closes),
        "momentum_consistency": momentum_consistency(closes, dates),
        "momentum_vol_adjusted": momentum_vol_adjusted(closes, dates),
        "amihud_illiquidity": amihud_illiquidity(closes, values, dates),
        # 2026-09-21 추가. 가격만으로 나므로 여기서 함께 낸다 (docs/factors.md 11.2).
        # 잔차 변동성은 지수 계열이 더 필요해 이 함수에 넣지 않았다 — 부르는 쪽이 따로 낸다
        "max_daily_return": max_daily_return(closes, dates),
    }


def risk_metrics(
    mdd: float | None,
    volatility_ann: float | None,
    sharpe: float | None,
    sortino: float | None,
    beta: float | None,
    mdd_recovery_days: int | None,
    cagr_value: float | None,
    unrecovered_rows: float | None = None,
) -> dict[str, float | None]:
    """안정성 지표. performance_metrics 에서 읽은 값을 방향만 맞춰 옮긴다.

    여기서 새로 계산하지 않는다. 계산식은 docs/metrics.md 가 단일 정의처다.

    MDD 와 베타는 절댓값으로 바꾼다. MDD 는 음수로 저장되고 베타는 음수일 수
    있는데, 부호를 그대로 두면 "많이 떨어진 종목"과 "역방향 종목"의 순위가
    뒤집힌다. 안정성 축이 보는 것은 크기다.
    """
    out: dict[str, float | None] = {
        "mdd_abs": None if mdd is None else abs(mdd),
        "volatility_ann": volatility_ann,
        "sharpe": sharpe,
        "sortino": sortino,
        "beta_abs": None if beta is None else abs(beta),
        # 미회복(None)은 값이 없는 것이 아니라 가장 나쁜 상태다.
        # 집단 최악값으로 치환하는 일은 정규화 단계에서 한다.
        "mdd_recovery_days": (
            None if mdd_recovery_days is None else float(mdd_recovery_days)
        ),
        "cagr": cagr_value,
    }
    # 미회복이고 알 때만 — 회복 기간 치환의 하한 (25.691). 지표가 아니라 재료라 없으면 키도 두지 않는다
    if mdd_recovery_days is None and unrecovered_rows is not None:
        out[UNRECOVERED_ROWS] = unrecovered_rows
    return out


# ----------------------------------------------------------------------
# 정규화
# ----------------------------------------------------------------------


#: 이보다 값이 적으면 윈저라이즈하지 않는다 (docs/infra.md 25.309)
WINSOR_MIN_VALUES = 10


def winsorize(values: list[float], pct: float = WINSOR_PCT) -> list[float]:
    """상·하위 pct 를 그 분위값으로 자른다. **적어도 한 종목씩** 자른다. 순서는 유지한다.

    **왜 "적어도 한 종목" 인가** (docs/infra.md 25.245). 예전 식(`floor(pct × (n−1))` 번째)은 1% 에서 n ≤ 100 이면
    자르는 자리가 최솟값·최댓값 **자신**이라 아무것도 자르지 않았다. 업종 집단은 30 종목부터 쓰므로(2.1절) 거의 모든
    업종 집단에서 윈저라이즈가 없었다 — 1..59 에 1000 하나를 섞으면 1000 이 그대로 남아 나머지 59 종목이 44~52점에
    몰렸다. 2.2절이 이 단계를 두는 까닭("한 종목이 집단 전체를 망친다")을 작은 집단에서도 지키려면
    한 종목은 잘라야 한다.

    **값이 `WINSOR_MIN_VALUES`(10) 개 미만이면 자르지 않는다** (docs/infra.md 25.309).
    집단이 30종목이어도 어떤 지표가 3종목에만 있으면, 예전 규칙(세 종목부터)은 k=1 로 셋을
    **모두 중앙값으로** 잘라 우열이 사라졌다(`[1, 2, 100]` → z 전부 0). 넷이면 `[1, 2, 3, 100]` →
    `[2, 2, 3, 3]` 으로 순서가 뭉개졌다. 값이 적을 때는 튀는 값을 누르는 것보다 **순서를 지키는 것**이
    낫다. 10개면 한쪽씩 잘라도 값의 20% 만 건드린다.
    """
    if not values or pct <= 0:
        return list(values)

    ordered = sorted(values)
    n = len(ordered)
    if n < WINSOR_MIN_VALUES:
        return list(values)
    k = max(1, math.floor(pct * (n - 1)))
    low = ordered[k]
    high = ordered[n - 1 - k]
    return [min(max(v, low), high) for v in values]


#: `raw_json` 안에서 **치환 사실**을 담는 자리. 지표 이름이 아니라 예약어라 밑줄로 시작한다.
#: `docs/factors.md` 3.5 가 "이 치환 사실을 `raw_json` 에 기록한다" 고 이미 요구하고 있었다.
SUBSTITUTED_KEY = "_substituted"


def worst_substitute(values: list[float | None], *, higher_is_better: bool) -> float | None:
    """결측을 대신할 **집단의 최악값**. 집단이 통째로 비면 None — 치환하지 않는다.

    `zscores` 와 `score_factors` 가 **같은 함수**를 부른다. 각자 세면 갈라지고,
    그러면 근거표에 적힌 치환값이 실제로 점수에 쓰인 값과 달라진다
    (docs/infra.md 25.0 "한 규칙이 두 곳에 있다").
    """
    present = [v for v in values if v is not None]
    if not present:
        return None
    return min(present) if higher_is_better else max(present)


def depth_substitutes(
    values: list[float | None],
    depth: list[float | None] | None,
    *,
    higher_is_better: bool,
    floor: list[float | None] | None = None,
) -> list[float | None]:
    """결측 자리마다 대신할 값 (docs/factors.md 3.5, docs/infra.md 25.685). 값이 있는 자리는 None.

    `depth` 가 없으면 모두 집단의 최악값(`worst_substitute`)이다. 있으면 **그 깊이 이하로 빠졌던 종목들의 최악값**이다 —
    미회복 −0.75% 는 "−0.75% 이하로 빠졌다가 회복한 종목 중 가장 오래 걸린 것" 만큼 나쁘다고 본다. 예전에는 깊이를 보지
    않고 집단 최악(−40% 종목의 회복 기간)을 넣어, 신고가 다음 날 조금 내린 우상향 종목이 회복 점수 최하였다.
    그만큼 얕게 빠진 종목이 하나도 없으면 **그보다 깊은 것 중 가장 얕은 종목**의 값을 쓴다(가장 비슷한 종목).
    깊이를 모르면 예전처럼 집단 최악이다 — 모를 때는 보수적으로 둔다.
    `floor` 가 있으면 **그보다 좋게 채우지 않는다** (25.691, 교차검증) — 바닥에서 400행째 회복 못 한 −10% 종목을
    "−10% 이하 종목 최악 100행" 으로 채우면, 300행 만에 회복한 종목보다 좋게 나왔다. 회복 기간은 적어도 지난 행 수다.
    """
    out = _depth_substitutes(values, depth, higher_is_better=higher_is_better)
    if not floor:
        return out
    나쁜쪽 = min if higher_is_better else max
    return [
        v if v is None or floor[i] is None else 나쁜쪽(v, floor[i])  # type: ignore[type-var]
        for i, v in enumerate(out)
    ]


def _depth_substitutes(
    values: list[float | None], depth: list[float | None] | None, *, higher_is_better: bool
) -> list[float | None]:
    group = worst_substitute(values, higher_is_better=higher_is_better)
    out: list[float | None] = []
    for i, v in enumerate(values):
        d = depth[i] if depth else None
        if v is not None:
            out.append(None)
            continue
        if d is None or group is None or depth is None:
            out.append(group)
            continue
        쌍 = [(dd, x) for x, dd in zip(values, depth, strict=True) if x is not None and dd is not None]
        얕은 = [x for dd, x in 쌍 if dd <= d]
        if 얕은:
            out.append(worst_substitute(얕은, higher_is_better=higher_is_better))  # type: ignore[arg-type]
            continue
        깊은 = sorted((dd, x) for dd, x in 쌍 if dd > d)
        out.append(깊은[0][1] if 깊은 else group)
    return out


def zscores(
    values: list[float | None],
    *,
    higher_is_better: bool = True,
    missing_is_worst: bool = False,
    pct: float = WINSOR_PCT,
    unknown: list[bool] | None = None,
    depth: list[float | None] | None = None,
    floor: list[float | None] | None = None,
) -> list[float | None]:
    """집단 안에서 z-score 를 낸다. 결측은 결측으로 남긴다.

    표준편차가 0 이면 전원 0 이다. 모두 같은 값이면 우열이 없다.
    `unknown[i]` 가 참인 결측은 치환하지 않고 결측으로 남긴다 (25.558).
    `depth` 를 주면 결측을 깊이에 맞춰 채운다(`depth_substitutes`, 25.685).
    """
    working = list(values)

    if missing_is_worst:
        대신 = depth_substitutes(working, depth, higher_is_better=higher_is_better, floor=floor)
        working = [
            대신[i] if v is None and not (unknown and unknown[i]) else v for i, v in enumerate(working)
        ]

    present = [v for v in working if v is not None]
    if not present:
        return [None] * len(working)

    clipped = winsorize(present, pct)
    mean = sum(clipped) / len(clipped)
    variance = sum((v - mean) ** 2 for v in clipped) / len(clipped)
    stdev = math.sqrt(variance)

    # 자른 값으로 평균과 표준편차를 내고, 개별 값도 같은 경계로 자른다.
    low, high = min(clipped), max(clipped)

    out: list[float | None] = []
    sign = 1.0 if higher_is_better else -1.0
    for value in working:
        if value is None:
            out.append(None)
            continue
        if stdev == 0:
            out.append(0.0)
            continue
        bounded = min(max(value, low), high)
        out.append(sign * (bounded - mean) / stdev)
    return out


def to_score(z: float | None, clip: float = Z_CLIP) -> float | None:
    """z 를 0~100 으로 옮긴다. z=+clip 이 100, z=-clip 이 0 이다."""
    if z is None:
        return None
    bounded = min(max(z, -clip), clip)
    return round(50 + 50 * bounded / clip, 1)


# ----------------------------------------------------------------------
# 팩터 집계
# ----------------------------------------------------------------------


def factor_from_metrics(
    metric_z: dict[str, float | None], factor: str, factor_metrics: Mapping[str, tuple[Metric, ...]] | None = None
) -> tuple[float | None, list[str]]:
    """구성 지표 z 의 평균과 빠진 지표 이름을 돌려준다.

    절반 미만만 살아 있으면 팩터를 내지 않는다. 지표 셋 중 하나로 낸 값을
    그 팩터의 점수라고 부를 수 없다.
    """
    metrics = (factor_metrics or FACTOR_METRICS)[factor]
    alive = [metric_z.get(m.name) for m in metrics]
    missing = [m.name for m, z in zip(metrics, alive, strict=True) if z is None]
    present = [z for z in alive if z is not None]

    if len(present) * 2 < len(metrics):
        return None, missing
    return sum(present) / len(present), missing


# ----------------------------------------------------------------------
# 비교 집단
# ----------------------------------------------------------------------


def peer_key(market: str, sector: str | None) -> str:
    """업종이 있으면 업종 집단, 없으면 시장 집단."""
    if sector:
        return f"sector:{market}:{sector}"
    return f"market:{market}"


def market_key(market: str) -> str:
    return f"market:{market}"


@dataclass
class StockInput:
    """한 종목의 지표 묶음. 팩터 계산의 입력."""

    stock_id: int
    market: str
    sector: str | None = None
    metrics: dict[str, float | None] = field(default_factory=dict)
    #: 리스크 팩터가 쓴 성과 지표 행 — {"window": "3Y"|"1Y", "as_of_date": ...} (docs/factors.md, infra 25.383)
    risk_source: dict[str, Any] | None = None
    #: 밸류 분모(시가총액)를 **어느 날 값에서 어떻게 옮겼는지** (docs/factors.md 3.1 "분모 시점 규칙", infra 25.954).
    #: `scale_market_cap` 의 기록 그대로. 밸류 팩터의 `raw_json` 에 `MARKET_CAP_KEY` 로 실린다
    market_cap_note: dict[str, Any] | None = None


#: 리스크 팩터의 `raw_json` 에 **어느 창·기준일의 성과 지표를 썼는지** 남기는 키 (docs/infra.md 25.383).
#: 문서는 "어느 창을 썼는지 `factors.raw_json` 에 남긴다" 고 적었는데 코드는 값만 넘기고 창을 버렸다 —
#: 3Y 가 비어 1Y 로 내려간 종목도 기록에 흔적이 없어,
#: 상세 화면의 1Y/3Y/5Y 표 중 어느 열이 점수에 들어갔는지 알 수 없었다
RISK_SOURCE_KEY = "_metrics_source"

#: 리스크 팩터의 `raw_json` 에 **회복 기간 하한**(`UNRECOVERED_ROWS`, 바닥 뒤 지난 행 수)을 함께 남기는 키
#: (docs/infra.md 25.1027). z 계산은 이 값을 치환의 하한으로 쓰는데(`depth_substitutes`) 지표 이름 목록에 없어
#: `raw_json` 에 안 실렸다 — 저장된 원값만으로 같은 z 를 다시 낼 수 없었다. 유니버스 밖 종목 참고 분석이
#: 유니버스 재료를 다시 읽지 않고 이 원값을 쓴다(`inputs_from_stored`)
UNRECOVERED_KEY = "_unrecovered_rows"

#: 밸류 팩터의 `raw_json` 에 **시가총액을 어느 날 값에서 어떻게 옮겼는지** 남기는 키 (docs/factors.md 3.1,
#: infra 25.954). 값은 `scale_market_cap` 의 기록 — {cap, cap_date, snapshot_date, factor, scaled, reason, source}.
#: 웹 `lib/stockDetail.MARKET_CAP_KEY` 와 같은 글자여야 한다
MARKET_CAP_KEY = "_market_cap"

#: 시총 배수가 이 띠 밖이면 옮기지 않는다 (docs/factors.md 3.1). 한 주 안에 두 배·절반이 된 종목은 기업행위(분할·병합)일
#: 가능성이 더 크다 — 옮기지 않는 쪽이 지금(스냅샷 값 그대로)과 같아 더 나빠지지 않는다
#: `[확인필요: 몇 주 뒤 raw_json 의 reason 분포를 보고 띠 재검토]`
MARKET_CAP_BAND = (0.5, 2.0)
#: 시총 날짜가 스냅샷 날짜보다 이만큼 넘게 앞서면 묵은 시총이라 옮기지 않는다 — 유니버스의 묵은 시총 경고
#: (`jobs/universe.MARKET_CAP_MAX_AGE_DAYS`)와 같은 값 (docs/factors.md 3.1, 25.960)
MARKET_CAP_STALE_DAYS = 14


def price_factor_kr(
    dates: list[str], change_pcts: dict[str, float | None], from_date: str, to_date: str
) -> float | None:
    """국내 가격 배수 = Π_{d ∈ (from, to]} (1 + 등락률(d)/100) (docs/factors.md 3.1).

    KRX 등락률(`prices.change_pct`)은 기준가 조정 뒤 값이라 분할·권리락을 `adjust_kr` 실행과 무관하게 건넌다.
    구간에 등락률이 빈 날이 하나라도 있거나, 시작일이 계열에 없거나, 끝이 시작보다 앞이면 None(모름). 같은 날이면 1.
    """
    if from_date > to_date or from_date not in dates:
        return None
    factor = 1.0
    for d in dates:
        if from_date < d <= to_date:
            pct = change_pcts.get(d)
            if pct is None or pct != pct:
                return None
            factor *= 1.0 + float(pct) / 100.0
    return factor


def price_factor_us(dates: list[str], closes: list[float], from_date: str, to_date: str) -> float | None:
    """미국 가격 배수 = 수정종가(to) / 수정종가(from). 둘 중 하나라도 없거나 0 이하면 None. 같은 날이면 1."""
    if from_date > to_date:
        return None
    by = dict(zip(dates, closes, strict=False))
    a, b = by.get(from_date), by.get(to_date)
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return float(b) / float(a)


def scale_market_cap(
    cap: float | None,
    factor: float | None,
    *,
    cap_date: str | None,
    snapshot_date: str | None,
    reason: str | None = None,
) -> tuple[float | None, dict[str, Any]]:
    """스냅샷 시총을 기준일 가격으로 옮긴 값과 그 기록 (docs/factors.md 3.1 "분모 시점 규칙").

    옮기는 조건: 시총이 있고, 시총의 가격 날짜(`stocks.market_cap_date`)가 스냅샷 날짜와 같고(그래야 그 시총이 그날
    가격의 값이다 — 13회차 검증 A·B), 배수를 낼 수 있고, 배수가 `MARKET_CAP_BAND` 안. 아니면 **그대로**(지금까지의
    동작)이고 `scaled=false` 와 사유를 남긴다. 값을 지어내지 않는다 — 모르면 옮기지 않는다.
    """
    note: dict[str, Any] = {
        "cap": cap, "cap_date": cap_date, "snapshot_date": snapshot_date, "factor": None,
        "scaled": False, "reason": None, "source": "universe_members/stocks.market_cap_date/prices",
    }  # fmt: skip
    if cap is None:
        note["reason"] = "시총 없음"
        return None, note
    if reason:
        note["reason"] = reason
        return cap, note
    # 기준은 **시총의 실제 가격 날짜**(market_cap_date)다 (25.960, 13회차 검증 B 의 조건). 처음(25.954)엔 "== 스냅샷
    # 날짜" 로 더 엄격히 했는데, 미국은 토요일 us_shares 가 그때 마지막 종가(목요일 — 금요일 종가는 월요일에 온다)로
    # 시총을 내고 일요일 스냅샷 날짜는 금요일이라 **늘 하루 어긋나** 한 번도 옮기지 못했다(10-05 실측 1,860종목 전부).
    # 시총 날짜가 스냅샷보다 늦으면(스냅샷 뒤 덮어씀·옛 스냅샷 재계산) 그 시총은 이 스냅샷의 값이 아니라 옮기지 않고,
    # 14일 넘게 앞서면(묵은 시총 복사) 옮기지 않는다
    if not cap_date or not snapshot_date:
        note["reason"] = "시총 날짜 모름"
        return cap, note
    if str(cap_date)[:10] > str(snapshot_date)[:10]:
        note["reason"] = "시총 날짜가 스냅샷보다 늦음"
        return cap, note
    from datetime import date as _date

    벌어짐 = (_date.fromisoformat(str(snapshot_date)[:10]) - _date.fromisoformat(str(cap_date)[:10])).days
    if 벌어짐 > MARKET_CAP_STALE_DAYS:
        note["reason"] = f"시총 날짜가 스냅샷보다 {MARKET_CAP_STALE_DAYS}일 넘게 앞섬(묵은 시총)"
        return cap, note
    if factor is None or factor != factor or factor <= 0:
        note["reason"] = "배수 못 냄"
        return cap, note
    low, high = MARKET_CAP_BAND
    if not (low <= factor <= high):
        note["reason"] = f"배수 {factor:.3f} 가 띠 {low}~{high} 밖"
        return cap, note
    note["factor"] = round(factor, 6)
    note["scaled"] = True
    return float(cap) * factor, note


def assign_peer_groups(
    stocks: list[StockInput], min_size: int = MIN_PEER_SIZE
) -> dict[int, str]:
    """종목마다 쓸 비교 집단을 정한다.

    업종 집단이 작으면 시장 집단으로 올린다. **시장을 넘어서는 올라가지
    않는다.** 한국과 미국을 같은 잣대로 비교하지 않는다는 규칙이 여기서
    지켜진다.
    """
    counts: dict[str, int] = {}
    for stock in stocks:
        key = peer_key(stock.market, stock.sector)
        counts[key] = counts.get(key, 0) + 1

    assigned: dict[int, str] = {}
    for stock in stocks:
        key = peer_key(stock.market, stock.sector)
        if counts.get(key, 0) < min_size:
            key = market_key(stock.market)
        assigned[stock.stock_id] = key
    return assigned


@dataclass
class FactorResult:
    stock_id: int
    factor: str
    zscore: float | None
    score: float | None
    peer_group: str
    peer_size: int
    missing_fields: list[str]
    #: 원시 지표 `{이름: 값}`. 치환이 있었으면 `_substituted` 키 하나가 더 있고
    #: 그 값은 `{지표 이름: 대신 쓴 값}` 이다 (`SUBSTITUTED_KEY`).
    raw: dict[str, Any]


def inputs_from_stored(rows: list[dict[str, Any]]) -> list[StockInput] | None:
    """저장된 팩터 원값(`factors.raw_json`)으로 z 계산의 입력을 다시 만든다 (docs/analysis.md 8장, infra 25.1027).

    rows: stock_id·market·sector·factor·raw(dict). `score_factors` 가 z 에 쓰는 것은 지표 원값과 회복 기간
    하한뿐이라
    그 둘이 있으면 **같은 결과**가 나온다(`tests/test_reference_light_1027.py` 가 대 본다). 리스크 원값에 하한 키가 없는
    행(25.1027 전에 쓴 것)이 하나라도 있으면 None — 부르는 쪽이 재료를 처음부터 읽는다."""
    by_stock: dict[int, StockInput] = {}
    for r in rows:
        sid = int(r["stock_id"])
        s = by_stock.setdefault(sid, StockInput(stock_id=sid, market=str(r["market"]), sector=r.get("sector") or None))
        raw = r.get("raw") or {}
        for k, v in raw.items():
            if not k.startswith("_"):
                s.metrics[k] = v
        if r["factor"] == "risk":
            if UNRECOVERED_KEY not in raw:
                return None
            s.metrics[UNRECOVERED_ROWS] = raw[UNRECOVERED_KEY]
    return list(by_stock.values())


def score_factors(
    stocks: list[StockInput],
    min_size: int = MIN_PEER_SIZE,
    factor_metrics: Mapping[str, tuple[Metric, ...]] | None = None,
) -> list[FactorResult]:
    """모든 종목의 다섯 팩터를 계산한다.

    집단 안에서만 정규화한다. 집단이 다르면 서로의 평균에 영향을 주지 않는다.
    `factor_metrics` 는 백테스트의 지표 교체 A/B 만 쓴다(`AB_VARIANTS`, 12.12) — 운영 점수는 늘 `FACTOR_METRICS` 다.
    """
    구성 = factor_metrics or FACTOR_METRICS
    groups = assign_peer_groups(stocks, min_size)
    by_group: dict[str, list[StockInput]] = {}
    for stock in stocks:
        by_group.setdefault(groups[stock.stock_id], []).append(stock)

    # **시장 집단의 비교 대상은 그 시장 전체다** (docs/factors.md 2.1, docs/infra.md 25.307).
    # 예전에는 작은 업종에서 올라온 종목끼리만 `market:KOSPI` 로 묶여, 업종 A(40)·B(10)·C(5) 면
    # B·C 15종목이 서로만 비교됐다 — 이름은 "시장 전체" 인데 표본은 30 하한보다 작았다.
    # 이제 z 는 그 시장의 **모든 종목**으로 내고, 결과는 그 집단에 배정된 종목에만 적는다
    by_market: dict[str, list[StockInput]] = {}
    for stock in stocks:
        by_market.setdefault(market_key(stock.market), []).append(stock)

    results: list[FactorResult] = []

    for group_key, assigned in by_group.items():
        members = by_market.get(group_key, assigned)
        배정 = {s.stock_id for s in assigned}
        # 지표별 z 를 집단 안에서 먼저 낸다.
        metric_z: dict[str, list[float | None]] = {}
        #: 치환이 **실제로 일어난** 지표와 종목마다 쓴 값. 집단이 통째로 비면 안 일어난다
        치환값: dict[str, dict[int, float]] = {}
        for factor in FACTORS:
            for metric in 구성[factor]:
                raw = [m.metrics.get(metric.name) for m in members]
                모름 = [metric.known_with is not None and m.metrics.get(metric.known_with) is None for m in members]
                # 깊이는 `known_with`(회복 기간이면 MDD 절댓값)다 — 얼마나 빠졌는지에 맞춰 채운다 (25.685)
                깊이 = [m.metrics.get(metric.known_with) for m in members] if metric.known_with else None
                하한 = [m.metrics.get(metric.floor_with) for m in members] if metric.floor_with else None
                if metric.missing_is_worst:
                    대신들 = depth_substitutes(raw, 깊이, higher_is_better=metric.higher_is_better, floor=하한)
                    for m, 값 in zip(members, 대신들, strict=True):
                        if 값 is not None:
                            치환값.setdefault(metric.name, {})[m.stock_id] = 값
                metric_z[metric.name] = zscores(
                    raw,
                    higher_is_better=metric.higher_is_better,
                    missing_is_worst=metric.missing_is_worst,
                    unknown=모름,
                    depth=깊이,
                    floor=하한,
                )

        for index, stock in enumerate(members):
            if stock.stock_id not in 배정:
                continue  # 시장 전체로 z 를 냈지만 이 종목은 자기 업종 집단에서 따로 점수를 받는다
            per_stock_z = {name: col[index] for name, col in metric_z.items()}
            for factor in FACTORS:
                z, missing = factor_from_metrics(per_stock_z, factor, 구성)
                names = [m.name for m in 구성[factor]]
                raw_values: dict[str, Any] = {name: stock.metrics.get(name) for name in names}
                # **치환한 사실을 남긴다** (docs/factors.md 3.5, docs/infra.md 25.93).
                # 이 종목이 그 지표를 모르는데 집단의 최악값으로 대신 점수를 받았다면,
                # `raw` 에는 null 이 들어가고 `missing_fields` 에도 안 들어간다 —
                # 화면이 "이 값은 왜 비었는데 점수는 있나" 에 답할 수 없다.
                알_때만 = {m.name: m.known_with for m in 구성[factor]}
                섞인것 = {
                    name: 치환값[name][stock.stock_id]
                    for name in names
                    if stock.stock_id in 치환값.get(name, {}) and stock.metrics.get(name) is None
                    and (알_때만[name] is None or stock.metrics.get(알_때만[name]) is not None)  # type: ignore[arg-type]
                }
                if 섞인것:
                    raw_values[SUBSTITUTED_KEY] = 섞인것
                if factor == "risk" and stock.risk_source:
                    raw_values[RISK_SOURCE_KEY] = stock.risk_source
                if factor == "risk":
                    raw_values[UNRECOVERED_KEY] = stock.metrics.get(UNRECOVERED_ROWS)
                # 밸류 분모를 어느 날 값에서 어떻게 옮겼는지 (docs/factors.md 3.1, 25.954) — 근거표가 펼친다
                if factor == "value" and stock.market_cap_note:
                    raw_values[MARKET_CAP_KEY] = stock.market_cap_note
                results.append(
                    FactorResult(
                        stock_id=stock.stock_id,
                        factor=factor,
                        zscore=None if z is None else round(z, 4),
                        score=to_score(z),
                        peer_group=group_key,
                        peer_size=len(members),
                        missing_fields=missing,
                        raw=raw_values,
                    )
                )
    return results


# ----------------------------------------------------------------------
# 종합 점수
# ----------------------------------------------------------------------

SKIP_TOO_MANY_MISSING = "팩터 2개 이상 결측"

#: 가중치가 들 수 있는 범위. **정의처는 `batch/core/settings_range` 하나다**
#: (2026-09-23, docs/infra.md 25.171). 그 표가 `web/lib/settings.ts` 의 `percent` 와
#: 같은지는 `tests/test_settings_range.py` 가 그 파일을 읽어 대 본다.
WEIGHT_MIN, WEIGHT_MAX = settings_range.설정_범위["sentiment_weight"]

#: 범위 밖 가중치로는 **점수를 내지 않는다** (docs/infra.md 25.169).
#:
#: `settings.ts` 머리말은 "설정은 웹앱만 쓴다. 배치는 읽기만 한다. 따라서 검증은 여기
#: 한 곳에 둔다" 고 적는다. 그 전제가 **복구·이주 경로에서 깨진다** — `restore_backup`
#: 과 `move_user_data` 는 `settings` 행을 검증 없이 써 넣는다.
#:
#: 문서는 이 함수가 **0~100** 을 돌려준다고 약속한다(docs/factors.md 5장). 약속을 지킬
#: 수 없으면 숫자를 지어내지 않고 사유를 남긴다 — `sentiment_weight=150` 이면 총점이
#: −40 이 나왔고, 음수 가중치는 **좋은 값이 점수를 깎게** 만든다. 둘 다 조용했다.
SKIP_BAD_WEIGHTS = "가중치가 0~100 밖"


@dataclass
class TotalScore:
    total: float | None
    weights_used: dict[str, float]
    sentiment_weight_used: float
    skip_reason: str | None = None
    #: 이 종합 점수에 **실제로 들어간** 센티먼트(-100~+100). 안 들어갔으면 None.
    #:
    #: 왜 돌려주나: 종합 점수는 센티먼트를 섞어 내면서 그 값을 아무 데도 남기지 않으면
    #: **화면이 분리해 보여 줄 수 없다**(CLAUDE.md "화면에는 항상 분리 표시"). 매수 시점
    #: 스냅샷 `sentiment_at_trade` 도 이것으로 채워진다 — 복기의 기준이다 (docs/infra.md 25.66).
    sentiment_used: float | None = None


def total_score(
    factor_scores: dict[str, float | None],
    weights: dict[str, float],
    sentiment: float | None = None,
    sentiment_weight: float = 0.0,
) -> TotalScore:
    """가중합. 빠진 팩터가 있으면 남은 가중치를 다시 나눈다.

    센티먼트는 -100~+100 이라 0~100 으로 옮겨 섞는다. 가중치가 0 이거나
    값이 없으면 5팩터만으로 재정규화한다. 센티먼트 축이 꺼진 상태로도
    동작해야 한다는 규칙이 여기서 지켜진다.
    """
    alive = {f: s for f, s in factor_scores.items() if s is not None}
    missing_count = len(FACTORS) - len(alive)

    if missing_count >= 2:
        return TotalScore(
            total=None,
            weights_used={},
            sentiment_weight_used=0.0,
            skip_reason=SKIP_TOO_MANY_MISSING,
        )

    나쁜값 = [
        f for f in alive if not (WEIGHT_MIN <= weights.get(f, 0.0) <= WEIGHT_MAX)
    ] or ([] if WEIGHT_MIN <= sentiment_weight <= WEIGHT_MAX else ["sentiment"])
    if 나쁜값:
        return TotalScore(
            total=None,
            weights_used={},
            sentiment_weight_used=0.0,
            skip_reason=f"{SKIP_BAD_WEIGHTS} ({', '.join(sorted(나쁜값))})",
        )

    weight_sum = sum(weights.get(f, 0.0) for f in alive)
    if weight_sum <= 0:
        return TotalScore(
            total=None,
            weights_used={},
            sentiment_weight_used=0.0,
            skip_reason="살아 있는 팩터의 가중치 합이 0",
        )

    normalized = {f: weights.get(f, 0.0) / weight_sum * 100 for f in alive}
    factor_part = sum(alive[f] * normalized[f] for f in alive) / 100

    use_sentiment = sentiment is not None and sentiment_weight > 0
    if not use_sentiment:
        return TotalScore(
            total=round(factor_part, 1),
            weights_used=normalized,
            sentiment_weight_used=0.0,
        )

    # **중립 감성은 점수를 움직이지 않는다** (docs/factors.md 5장, docs/infra.md 25.639, 감사).
    # 예전 식 `F×(1−w) + S×w` 는 F 를 S 쪽으로 당겨, F 가 50 보다 높은 종목은 중립 감성(50)에도 깎였다 —
    # 팩터합 80 에 감성 0 이면 77, 감성 +40 이어도 79 인데 감성이 없는 종목은 80 그대로였다(값이 없으면
    # 재정규화, 5장). 상위권 순위가 **뉴스 노출 여부**로 뒤바뀌었다. 지금은 50 에서 벗어난 만큼만 더하고 뺀다
    share = sentiment_weight / 100
    sentiment_0_100 = (sentiment + 100) / 2
    total = min(100.0, max(0.0, factor_part + (sentiment_0_100 - 50) * share))
    return TotalScore(
        total=round(total, 1),
        weights_used=normalized,
        sentiment_weight_used=sentiment_weight,
        sentiment_used=sentiment,
    )


def rank_within(scores: list[tuple[int, float | None]]) -> dict[int, int]:
    """점수 내림차순 순위. 점수가 없는 종목은 순위를 받지 않는다."""
    ranked = sorted(
        [(sid, s) for sid, s in scores if s is not None],
        key=lambda pair: -pair[1],
    )
    out: dict[int, int] = {}
    previous: float | None = None
    rank = 0
    for index, (stock_id, score) in enumerate(ranked, start=1):
        if score != previous:
            rank = index
            previous = score
        out[stock_id] = rank
    return out
