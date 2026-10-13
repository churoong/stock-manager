"""한 시장만 가격이 빈 날은 날짜 축에서 뺀다 (docs/infra.md 25.1279, 백테스트 감사 재현)."""

from __future__ import annotations

from batch.services import backtest as bt

날들 = [f"2024-02-{d:02d}" for d in range(1, 29)] + [f"2024-03-{d:02d}" for d in range(1, 15)]


def _가격(빈날: str | None) -> tuple[dict[int, dict[str, float]], dict[int, str]]:
    prices: dict[int, dict[str, float]] = {}
    시장: dict[int, str] = {}
    for sid in range(1, 101):  # 코스피 60 · 코스닥 40 → 코스닥이 빠지면 40%
        m = "KOSPI" if sid <= 60 else "KOSDAQ"
        시장[sid] = m
        prices[sid] = {d: 100.0 for d in 날들 if not (m == "KOSDAQ" and d == 빈날)}
    return prices, 시장


def test_코스닥만_빈_월초는_리밸런스_날이_아니다() -> None:
    prices, 시장 = _가격("2024-03-01")
    assert "2024-03-01" in bt.market_dates(prices, "")  # 전체 문턱(20%)만으로는 남는다
    축 = bt.market_dates(prices, "", 시장)
    assert "2024-03-01" not in 축 and "2024-03-04" in 축
    assert bt.month_starts(축)[-1] == "2024-03-02"


def test_정상_날은_그대로() -> None:
    prices, 시장 = _가격(None)
    assert bt.market_dates(prices, "", 시장) == bt.market_dates(prices, "")


def test_작은_무리는_따로_보지_않는다() -> None:
    prices, 시장 = _가격("2024-03-01")
    작게 = {sid: m for sid, m in 시장.items() if m == "KOSPI" or sid < 64}  # 코스닥 3종목만 표시
    assert "2024-03-01" in bt.market_dates(prices, "", 작게)
