"""매도 플래그를 이을 때 오늘 판정한 속 근거는 뺀다 — 흑자로 돌아 풀린 적자 전환이 점수 없는 날 남지 않게
(docs/infra.md 25.1790, 결함 루프)."""

from __future__ import annotations

import inspect
from datetime import date

from batch.jobs import sell_flags as job
from batch.services import sell_flags as sf

적자 = {"label": sf.FIN_LOSS_LABEL, "display": "FY2024 −5 (전년 +10)"}
점수 = {"label": "종합 점수 하락", "display": "70 → 45"}


def test_오늘_재무를_봤으면_적자_전환은_잇지_않는다() -> None:
    h = sf.HoldingInput(stock_id=1, name="가", horizon="long", first_buy_date="2025-01-02", currency="KRW", cost=1000.0,
                        market_value=1000.0, price_date="2026-03-19", op_income_latest=8.0, op_income_prev=-5.0)
    assert "재무악화" in sf.unjudged(h, date(2026, 3, 20))  # 점수를 못 봐 재무악화는 "판정 못 함"
    assert sf.carry_criteria(h, "재무악화", [적자]) == []  # 그래도 적자 전환은 판정했다 — 풀림
    assert sf.carry_criteria(h, "재무악화", [적자, 점수]) == [점수]  # 점수 하락 근거만 잇는다
    모름 = sf.HoldingInput(**{**h.__dict__, "op_income_latest": None})
    assert sf.carry_criteria(모름, "재무악화", [적자, 점수]) == [적자, 점수]  # 재무도 못 봤으면 그대로
    assert sf.carry_criteria(h, "감성급락", [적자]) == [적자]  # 다른 사유는 그대로


def test_문장에서도_적자_전환_조각을_뺀다() -> None:
    글 = "삼성: FY2024 영업이익 적자 전환, 종합 점수 70→45점. 보유 이유가 유지되는지 점검하세요"
    assert sf.without_fin_loss(글) == "삼성: 종합 점수 70→45점. 보유 이유가 유지되는지 점검하세요"


def test_잇는_곳이_속_근거를_거른다() -> None:
    원문 = inspect.getsource(job.run)
    assert "sf.carry_criteria(보유별[sid], 사유, 어제근거)" in 원문 and "if 어제근거 and not 남김:" in 원문
    assert "글 = sf.without_fin_loss(글)" in 원문
