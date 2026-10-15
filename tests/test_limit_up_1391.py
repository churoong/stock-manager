"""단기 신호 창의 상한가 날 — **기록만** (docs/infra.md 25.1391, docs/factors.md 12.28 P3)."""

from __future__ import annotations

from batch.services import signals as sg
from tests.test_signals import short_input


def _계열(마지막: list[float]) -> list[float | None]:
    return [10_000.0] * 40 + 마지막


def test_상한가는_가격제한폭과_호가표에서만_나온다() -> None:
    # 10,000 → 13,000(상한가, 1만~2만원 호가 10원) → 13,000 → 16,900(상한가: 13,000×1.3)
    assert sg.limit_up_days(_계열([13_000.0, 13_000.0, 16_900.0]), "KRW") == 2
    # 상한가에 한 호가 못 미치면 세지 않는다
    assert sg.limit_up_days(_계열([12_990.0]), "KRW") == 0
    # 호가 내림: 37,700 × 1.3 = 49,010 → 5만원 미만 호가 50원 → 상한가 49,000
    assert sg.limit_up_days([37_700.0] * 30 + [49_000.0], "KRW") == 1


def test_모르면_모른다() -> None:
    assert sg.limit_up_days(_계열([13_000.0]), "USD") is None  # 미국은 가격제한이 없다
    assert sg.limit_up_days([10_000.0] * 10, "KRW") is None  # 창이 모자라다
    assert sg.limit_up_days(_계열([20_000.0]), "KRW") is None  # 하루 +100% — 수정주가 빈틈
    # 정지일 빈칸을 사이에 둔 쌍은 건너뛴다(빈칸 뒤 첫날을 상한가로 세지 않는다)
    assert sg.limit_up_days(_계열([None, 13_000.0]), "KRW") == 0


def test_판정과_근거표는_그대로() -> None:
    inp = short_input()
    passed, data = sg.short_term(inp)
    assert "limit_up_days_21" in data
    assert "limit_up_days_21" not in str(sg.judgement("short", inp, data))
