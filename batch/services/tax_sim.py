"""계좌별 세후 적립 시뮬레이션 (docs/etf.md 11.7, docs/infra.md 25.1003).

같은 돈을 같은 수익 가정으로 매달 넣었을 때 **계좌(일반·연금저축)와 상품(국내 주식 ETF·국내 상장 해외 ETF·
미국 상장 ETF)에 따라 세금이 얼마나 갈리는지**를 보인다. 계좌별 ETF 추천(11.1)이 "과세 구조로 순위를 정한다" 고
말로 한 것을 숫자로 보인다.

- **수익은 가정이다.** 예측이 아니다 — 모든 행이 같은 가정(`PRICE_RETURN_PCT`·`DIST_YIELD_PCT`)이라 차이는
  세금에서만 난다
- **세율은 전부 설정(`taxes`)에서 읽는다.** 비어 있으면 그 행은 숫자를 내지 않고 "설정 세율 미입력" 이라 적는다
  (CLAUDE.md 세율 규칙 — 기본값을 지어내지 않는다). 양도차익 기본공제·세액공제 한도 같은 제도 숫자는 아래 상수이고
  `[확인필요]` 다
- 매달 초에 넣고, 달마다 가격이 오르고, 분배금을 받아 세금을 떼고 다시 넣는다. 끝에 한 번에 판다(일반 계좌) 또는
  받는다(연금)
- 환율은 고정으로 본다(미국 상장도 원화로 센다). 금융소득종합과세·건강보험료·연금 수령 한도 초과 과세는 넣지 않는다
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from batch.core import settings_range as sr

#: 매달 넣는 돈(원) — 연금저축 세액공제 한도(연 600만원 `[확인필요]`)와 맞춘 월 50만원
MONTHLY_KRW = 500_000
#: 적립 기간(년) 둘
YEARS = (10, 20)
#: 가정 — 연 가격 수익률(%)과 연 분배율(%). 예측이 아니다(모든 행이 같은 가정)
PRICE_RETURN_PCT = 5.0
DIST_YIELD_PCT = 2.0
#: 해외주식 양도소득 기본공제(원/년) `[확인필요]`
US_GAIN_DEDUCTION_KRW = 2_500_000
#: 연금저축 세액공제 대상 납입 한도(원/년) `[확인필요]`
PENSION_CREDIT_LIMIT_KRW = 6_000_000


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    years: int
    contributed: float
    after_tax: float | None  # 세율이 비었으면 None
    tax: float | None  # 낸(낼) 세금 합 − 받은 세액공제
    missing: list[str]


def _grow(years: int, dist_tax_pct: float) -> tuple[float, float, float]:
    """(끝 평가액, 원가(넣은 돈 + 재투자한 세후 분배금), 분배금 세금 합)."""
    value = basis = dist_tax = 0.0
    for _ in range(years * 12):
        value += MONTHLY_KRW
        basis += MONTHLY_KRW
        value *= 1 + PRICE_RETURN_PCT / 100 / 12
        dist = value * DIST_YIELD_PCT / 100 / 12
        tax = dist * dist_tax_pct / 100
        value += dist - tax
        basis += dist - tax
        dist_tax += tax
    return value, basis, dist_tax


#: 이 시뮬레이션이 읽는 설정 `taxes` 잎
TAX_KEYS = ("kr_dividend_pct", "us_dividend_pct", "us_capital_gains_pct", "pension_income_pct", "pension_credit_pct")
#: 줄 → (이름, 필요한 세율 잎)
KINDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("general_kr_equity", "일반 · 국내 주식 ETF", ("kr_dividend_pct",)),
    ("general_kr_listed_foreign", "일반 · 국내 상장 해외 ETF", ("kr_dividend_pct",)),
    ("general_us_listed", "일반 · 미국 상장 ETF", ("us_dividend_pct", "us_capital_gains_pct")),
    ("pension_kr_listed_foreign", "연금저축 · 국내 상장 해외 ETF", ("pension_income_pct", "pension_credit_pct")),
)


def after_tax(key: str, years: int, r: dict[str, float]) -> tuple[float, float]:
    """(세후 금액, 세금 − 세액공제). r 은 그 줄에 필요한 세율(%)."""
    넣은 = MONTHLY_KRW * 12 * years
    if key == "general_kr_equity":
        v, _, dt = _grow(years, r["kr_dividend_pct"])
        return v, dt  # 매매차익 비과세 `[확인필요]`
    if key == "general_kr_listed_foreign":
        v, b, dt = _grow(years, r["kr_dividend_pct"])
        g = max(v - b, 0) * r["kr_dividend_pct"] / 100  # 매매차익도 배당소득세 `[확인필요]`
        return v - g, dt + g
    if key == "general_us_listed":
        v, b, dt = _grow(years, r["us_dividend_pct"])
        g = max(v - b - US_GAIN_DEDUCTION_KRW, 0) * r["us_capital_gains_pct"] / 100  # 한 해에 모두 판다
        return v - g, dt + g
    # 연금 — 과세이연이라 분배금도 세금 없이 재투자. 해마다 돌려받는 세액공제는 재투자하지 않는다
    v, _, _ = _grow(years, 0.0)
    공제대상 = min(MONTHLY_KRW * 12, PENSION_CREDIT_LIMIT_KRW) * years
    credit = 공제대상 * r["pension_credit_pct"] / 100
    # 공제받은 원금과 운용수익이 연금소득세 대상. 공제받지 않은 원금은 비과세 `[확인필요]`
    tax = (v - (넣은 - 공제대상)) * r["pension_income_pct"] / 100
    return v - tax + credit, tax - credit


def simulate(taxes: dict | None) -> dict:
    """설정 `taxes` → 기간마다 네 줄. 세율이 비었거나 범위 밖(`잎마다`)인 줄은 숫자를 내지 않는다."""
    t, _ = sr.잎마다(taxes, TAX_KEYS)
    rows: list[Row] = []
    for years in YEARS:
        for key, label, need in KINDS:
            빈 = [k for k in need if t.get(k) is None]
            넣은 = float(MONTHLY_KRW * 12 * years)
            if 빈:
                rows.append(Row(key, label, years, 넣은, None, None, 빈))
                continue
            after, tax = after_tax(key, years, {k: float(t[k]) for k in need})
            rows.append(Row(key, label, years, 넣은, round(after), round(tax), []))
    return {
        "assumption": {"monthly_krw": MONTHLY_KRW, "price_return_pct": PRICE_RETURN_PCT,
                       "dist_yield_pct": DIST_YIELD_PCT, "us_gain_deduction_krw": US_GAIN_DEDUCTION_KRW,
                       "pension_credit_limit_krw": PENSION_CREDIT_LIMIT_KRW},  # fmt: skip
        "rows": [asdict(r) for r in rows],
    }
