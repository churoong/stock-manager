"""매도 플래그 문장의 금액·수익률·점수도 .5 를 0 에서 멀어지게 올린다 (docs/infra.md 25.1324, 매도 플래그 감사 #3).

파이썬 서식 `.1f` 는 이진 표현 그대로 잘라 −12.25억이 "−12.2억원" 이 됐다 — 웹(`toFixed`)과 규칙(25.1105)은 "−12.3"."""

from __future__ import annotations

from batch.services import sell_flags as sf
from tests.test_sell_flags import TODAY, holding


def test_재무_금액은_반_올림() -> None:
    assert sf._재무금액(-1_225_000_000, "KRW") == "-12.3억원"
    assert sf._재무금액(1_234_250_000_000, "KRW") == "12,342.5억원"
    assert sf._재무금액(-12_250_000, "USD") == "$-12.3M"


def test_적자_전환_근거에_반_올림_금액() -> None:
    f = sf.evaluate(holding(op_income_latest=-1_225_000_000, op_income_prev=2_000_000_000, fiscal_year_latest=2025,
                            op_income_currency="KRW", op_income_prev_currency="KRW"), TODAY)  # fmt: skip
    assert "-12.3억원" in str(f[0].criteria[0])


def test_점수_근거도_같은_규칙() -> None:
    f = sf.evaluate(holding(score_at_buy=70.25, score_now=50.0), TODAY)
    assert "70.3 → 50.0 (−20.3)" in str(f[0].criteria[0])  # 예전 "70.2 → 50.0 (−20.2)"
