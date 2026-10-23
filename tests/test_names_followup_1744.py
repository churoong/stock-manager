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


def test_앤드_컴퍼니는_이름의_일부_25_1747() -> None:
    """25.1744 교차검증 [권고] 3 — "JPMorgan Chase &(JPM)"·"Eli Lilly and(LLY)" 가 나갔다"""
    assert formatter.short_name("JPMorgan Chase & Co. Common Stock", "JPM", "USD") == "JPMorgan Chase & Co.(JPM)"
    assert formatter.short_name("Eli Lilly and Company Common Stock", "LLY", "USD") == "Eli Lilly and Company(LLY)"
    assert formatter.short_name("Apple Inc. Common Stock", "AAPL", "USD") == "Apple(AAPL)"  # 그대로


def test_주간_머리는_동률이면_첫_계좌_값_계좌_하나면_씩_없음_25_1747() -> None:
    from batch.services import paper as pp
    from tests.test_paper_summary_1447 import _행, 한도

    행 = {a: [_행(a, "2026-10-02", 1_000_000, 1_000_000, 1.0),
             _행(a, "2026-10-09", 1_000_000, 1_000_000, 1.0, kospi=1_020_000 if a in ("A", "B") else None)]
         for a in ("A", "B", "C", "D")}  # fmt: skip
    줄 = pp.summary_lines(행, {}, "2026-10-03", None, {}, {}, 한도)
    assert 줄[1] == "  입금 100만원씩 · 같은 돈 KOSPI +2.0% · S&P500 — (A·B)"  # 2:2 동률 — A 가 든 값
    # 거꾸로 — 첫 계좌 쪽 값이 사전순으로 뒤("—")여도 그 값이 머리
    거꾸로 = {a: [_행(a, "2026-10-02", 1_000_000, 1_000_000, 1.0),
                 _행(a, "2026-10-09", 1_000_000, 1_000_000, 1.0, kospi=1_020_000 if a in ("C", "D") else None)]
             for a in ("A", "B", "C", "D")}  # fmt: skip
    assert pp.summary_lines(거꾸로, {}, "2026-10-03", None, {}, {}, 한도)[1].endswith("KOSPI — · S&P500 — (A·B)")
    하나 = pp.summary_lines({"A": 행["A"]}, {}, "2026-10-03", None, {}, {}, 한도)
    assert 하나[1] == "  입금 100만원 · 같은 돈 KOSPI +2.0% · S&P500 —"
