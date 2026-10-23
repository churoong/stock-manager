"""25.1740·1741 교차검증 [권고] 반영 — 줄표 꼬리, 공시 줄 이름, 보유 점수 클래스주 겹침 (docs/infra.md 25.1744)."""

from __future__ import annotations

from batch.notify import formatter
from batch.services import holding_scores as hs
from batch.services.accumulation import display_name


def test_줄표로_이은_꼬리는_줄표까지_뗀다() -> None:
    assert formatter.short_name(display_name("Alphabet Inc. - Class C Capital Stock"), "GOOG", "USD") == "Alphabet(GOOG)"
    assert formatter.short_name(display_name("Fox Corporation - Class B Common Stock"), "FOX", "USD") == "Fox(FOX)"


def test_보유_점수는_티커로_클래스주를_가른다() -> None:
    a = hs.HoldingScore("Berkshire Hathaway Inc. Class A", "NYSE", 60.0, 3, None, None, None, ticker="BRK.A",
                        currency="USD")  # fmt: skip
    b = hs.HoldingScore("Berkshire Hathaway Inc. Class B", "NYSE", 60.0, 3, None, None, None, ticker="BRK.B",
                        currency="USD")  # fmt: skip
    assert hs.line(a).split(":")[0] != hs.line(b).split(":")[0]
    assert hs.line(a).startswith("Berkshire Hathaway(BRK.A): ")
    # 통화가 원이면 시장 이름과 상관없이 그대로
    assert hs.line(hs.HoldingScore("카카오", "KOSDAQ GLOBAL", 45.0, 1, None, None, None, ticker="035720",
                                   currency="KRW")).startswith("카카오: ")  # fmt: skip
    rows = hs.from_rows([{"name": "X Inc.", "market": "NYSE", "ticker": "X", "currency": "USD"}])
    assert rows[0].ticker == "X" and rows[0].currency == "USD"
