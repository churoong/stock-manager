"""이익의 질 — 발생액 (종목선정 기법 발굴 루프 1회차 C, docs/factors.md 12.2, docs/infra.md 25.445).

발생액 비율 = (순이익 − 영업활동 현금흐름) / 평균 총자산. 현금으로 뒷받침되지 않는 이익이 클수록 다음 해
수익이 나빴다는 이상현상이다. Sloan (1996), *The Accounting Review*. 방향은 −(작을수록 좋다).

**지금은 미국만 값이 있다** — 국내는 현금흐름표를 받지 않는다(docs/factors.md 11.5). 백테스트 IC(`accrual`)로만
잰다. 효과가 최근 약해졌다는 보고가 있다(Green, Hand & Soliman 2011, 1회차 검증 2) `[확인필요: 한국 실증]`.

계산만 한다. DB 도 시각도 모른다.
"""

from __future__ import annotations


def accrual_ratio(
    net_income: float | None,
    operating_cash_flow: float | None,
    total_assets: float | None,
    total_assets_prev: float | None,
) -> float | None:
    """(순이익 − 영업현금흐름) / 평균 총자산. 하나라도 없거나 평균 총자산이 0 이하이면 None.

    전년 총자산이 없으면 **당기 총자산만으로 대신하지 않는다** — 평균의 뜻이 바뀌고, 자산이 급히 는 해
    (인수합병)에 분모가 작아 값이 부푼다.
    """
    if None in (net_income, operating_cash_flow, total_assets, total_assets_prev):
        return None
    avg = (float(total_assets) + float(total_assets_prev)) / 2  # type: ignore[arg-type]
    if avg <= 0:
        return None
    return (float(net_income) - float(operating_cash_flow)) / avg  # type: ignore[arg-type]
