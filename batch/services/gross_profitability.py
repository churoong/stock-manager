"""매출총이익/총자산 — 종목선정 기법 발굴 루프 3회차 E (docs/factors.md 12.2, docs/infra.md 25.740).

`매출총이익 / 총자산` (Novy-Marx 2013, *JFE*). 원 논문 식 그대로라 문턱이 없다.
**미국만, IC 진단만** (3회차 검증 2). 통과하면 퀄리티의 영업이익률과 **교체**하는 실험 전략으로 본다 — 9번째로 더하면
절반 문턱이 올라 손해다(11.5 의 `dividend_years` 와 같은 논리).
금융업은 매출원가가 뜻이 없어 뺀다(호출하는 쪽이 거른다).

계산만 한다. DB 도 시각도 모른다.
"""

from __future__ import annotations


def gross_profitability(gross_profit: float | None, total_assets: float | None) -> float | None:
    """총자산이 없거나 0 이하면 None. 매출총이익은 음수일 수 있다(원가가 매출보다 큰 해)."""
    if gross_profit is None or total_assets is None or total_assets <= 0:
        return None
    return float(gross_profit) / float(total_assets)
