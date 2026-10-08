"""**받아 온 행에는 출처와 받은 시각이 있는가** (docs/infra.md 25.173).

CLAUDE.md 데이터 소스 규칙에 이렇게 적혀 있다.

> 모든 데이터 행에 **source, fetched_at 필수**

지키는 장치가 없었다. 2026-09-23 에 재어 보니 표 쉰여섯 중 열넷만 둘 다 갖고 있다.
나머지는 **대부분 옳다** — 계산 결과와 사람 입력에는 "어디서 받았나" 가 없다.
규칙이 말하는 것은 **밖에서 받아 온 행**이다.

그래서 여기서 하는 일은 검사가 아니라 **분류를 강제하는 것**이다. 표를 새로 만들면
다섯 갈래 중 하나에 넣어야 하고, 받아 온 것이면 두 열을 갖춰야 한다. 분류가 곧
"이 표는 무엇인가" 의 기록이다.

`tests/test_backup_covers_web.py` 와 같은 모양이다 — 목록끼리 대 보는 것으로는
**두 목록에 모두 빠진 새 표**를 못 잡으니, 스키마를 훑어 하나도 빠뜨리지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
마이그 = 뿌리 / "migrations"

#: **밖에서 받아 온 것.** `source` 와 `fetched_at` 이 둘 다 있어야 한다.
#: 값은 어디서 받는지 — 나중에 출처가 바뀌면 여기부터 고친다
받아_온_표 = {
    "kr_opinions": "한국투자증권 KIS 증권사 투자의견 (batch/jobs/kis_flows.py, 25.988)",
    "kr_corp_events": "한국투자증권 KIS 예탁원 기업행위 일정 (batch/jobs/kis_flows.py, 25.988)",
    "disclosure_coverage": "DART 공시목록 — 시장 전체 B·I 를 그날 다 받은 날 (batch/jobs/disclosure_reaction.py, 25.1014)",
    "kr_flows": "한국투자증권 KIS 투자자별·공매도·신용 일별 (batch/jobs/kis_flows.py, 25.987)",
    "api_tokens": "한국투자증권 KIS 접근토큰 (web/lib/kis.ts, 25.983)",
    "stocks": "KRX 종목마스터 · SEC company_tickers",
    "prices": "KRX 일별시세 · 야후 (수정주가 포함)",
    "market_calendar": "exchange_calendars 로 낸 휴장일 판정과 그 근거",
    "financials": "DART 사업보고서 · SEC companyfacts",
    "financial_snapshots": "위와 같되 **발표 시점**으로 얼린 것 (point-in-time)",
    "disclosures": "DART 공시목록 · SEC submissions",
    "earnings_calendar": "야후 실적 예정일 · 법정 기한 추정",
    "etfs": "KRX ETF 목록 · 야후",
    "etf_profiles": "야후 펀드 프로필 (보수·순자산·설정일)",
    "etf_lookthrough": "보유 ETF 구성종목 비중 — SEC N-PORT · KODEX · KIS 상위 30 (batch/jobs/etf_tilt.py, 25.1002)",
    "stock_dividends": "DART 배당 · 야후",
    "fx_rates": "야후 USDKRW",
    "news": "언론사 RSS (제목·URL·시각만, 원문은 저장하지 않는다)",
    "index_prices": "KRX 지수 · 야후",
    "insider_trades": "DART 임원·주요주주 소유보고 · SEC Form 4",
}

#: **밖에서 왔지만 "받아 온" 것이 아닌 것.** 왜 `fetched_at` 이 없어도 되는지를 적는다
라이브러리가_낸_표 = {
    "market_sessions": "`exchange_calendars` 가 **계산해 준** 정규장 시각이다. 네트워크를"
    " 타지 않으므로 '언제 받았나' 가 없다 — 근거는 **어느 버전으로 계산했나**이고 그것이"
    " `source` 에 들어간다(CLAUDE.md: \"캘린더 버전을 기록한다\"). 얼마나 채워졌는지는"
    " `MAX(date)` 가 답하고, 마르기 전에 무응답 감시가 알린다(25.104)",
}

#: **우리가 계산해 만든 것.** 입력의 출처는 그 입력 표에 있고, 언제 낸 것인지는
#: `as_of_date`·`created_at`·`computed_at` 같은 제 이름의 열이 답한다
만든_표 = {
    "factors", "scores", "signals", "signal_checks", "signal_outcomes", "signal_outcome_stats",
    "performance_metrics", "valuation_bands", "backtest_runs", "backtest_curves", "stress_runs",
    "etf_picks", "etf_satellite_picks", "stock_accum_picks", "universe_members",
    "article_sentiments", "sentiment_scores", "positions", "trade_lots", "portfolio_values",
    "portfolio_summary", "review_stats", "trade_reviews", "daily_reports", "report_items",
    "sell_flags", "monitor_targets", "news_targets", "adjust_refresh_queue",
    "broker_stats",  # 증권사 적중률 (25.995)
    "disclosure_reaction",  # 공시 반응 통계 (25.996)
}  # fmt: skip

#: **사람이 넣은 것.** 출처는 사용자다. 시스템이 임의로 고치지 않는다(CLAUDE.md)
사람이_넣은_표 = {"trades", "dividend_receipts", "watchlist", "settings", "screener_presets", "stock_aliases"}  # 별칭은 사람이 관리 (25.840)

#: **우리가 무엇을 했는지의 기록.** 판단 자료가 아니다
운영_기록 = {
    "batch_runs", "api_usage", "alerts", "health_alerts", "cron_heartbeats",
    "login_attempts", "news_fetch_log",
}  # fmt: skip


def 스키마_표() -> dict[str, set[str]]:
    """마이그레이션이 만드는 표 → 열 이름. **스키마가 단일 정의처다**(CLAUDE.md)."""
    나온것: dict[str, set[str]] = {}
    for 길 in sorted(마이그.glob("*.sql")):
        글 = 길.read_text(encoding="utf-8")
        for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", 글, re.S):
            나온것[m.group(1)] = set(re.findall(r"^\s{2,}([a-z_]+)\s", m.group(2), re.M))
    return 나온것


def 분류() -> dict[str, str]:
    out = {t: "받아_온_표" for t in 받아_온_표}
    out |= {t: "라이브러리가_낸_표" for t in 라이브러리가_낸_표}
    out |= {t: "만든_표" for t in 만든_표}
    out |= {t: "사람이_넣은_표" for t in 사람이_넣은_표}
    out |= {t: "운영_기록" for t in 운영_기록}
    return out


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 공짜로 통과한다."""
    표 = 스키마_표()
    assert len(표) > 50, f"표를 {len(표)}개밖에 못 찾았다 — 훑기가 깨졌다"
    assert "prices" in 표 and "trades" in 표


def test_표마다_갈래가_있다() -> None:
    """**새 표를 만들면 여기서 깨진다.** 그때 "이 표는 무엇인가" 를 정하게 된다."""
    빠진것 = sorted(set(스키마_표()) - set(분류()))

    assert not 빠진것, (
        f"갈래를 안 정한 표: {빠진것}\n"
        "밖에서 받아 온 것이면 `받아_온_표` 에 출처와 함께 넣고 `source`·`fetched_at` 을 둬라.\n"
        "계산 결과·사람 입력·운영 기록이면 해당 목록에 넣어라 (docs/infra.md 25.173)"
    )


def test_목록이_낡지_않았다() -> None:
    없는것 = sorted(set(분류()) - set(스키마_표()))
    assert not 없는것, f"스키마에 없는 표가 목록에 남아 있다: {없는것}"


def test_한_표가_두_갈래에_있지_않다() -> None:
    모든것 = [
        *받아_온_표, *라이브러리가_낸_표, *만든_표, *사람이_넣은_표, *운영_기록,
    ]  # fmt: skip
    겹침 = sorted({t for t in 모든것 if 모든것.count(t) > 1})
    assert not 겹침, f"두 갈래에 동시에 있다: {겹침}"


@pytest.mark.parametrize("표이름", sorted(받아_온_표))
def test_받아_온_표에는_출처와_받은_시각이_있다(표이름: str) -> None:
    """CLAUDE.md: "모든 데이터 행에 source, fetched_at 필수"."""
    열 = 스키마_표()[표이름]
    빠진것 = sorted({"source", "fetched_at"} - 열)

    assert not 빠진것, f"`{표이름}` 에 {빠진것} 이 없다 — {받아_온_표[표이름]} 에서 받는 표다"


def test_출처가_적혀_있다() -> None:
    짧은것 = [t for t, v in 받아_온_표.items() if len(v.strip()) < 5]
    assert not 짧은것, f"출처가 비어 있다: {짧은것}"

    짧은사유 = [t for t, v in 라이브러리가_낸_표.items() if len(v.strip()) < 20]
    assert not 짧은사유, f"`fetched_at` 이 없는 사유가 너무 짧다: {짧은사유}"


@pytest.mark.parametrize("표이름", sorted(만든_표 | 사람이_넣은_표 | 운영_기록))
def test_안_받아_온_표에는_출처_열을_만들지_않는다(표이름: str) -> None:
    """**둘 다 있으면 "받아 온 표" 로 오해된다.**

    `source` 가 있는데 목록에 없으면 둘 중 하나다 — 갈래를 잘못 정했거나,
    받아 온 표가 되었는데 목록을 안 고쳤거나.
    """
    열 = 스키마_표()[표이름]
    assert not ({"source", "fetched_at"} <= 열), (
        f"`{표이름}` 이 `source` 와 `fetched_at` 을 둘 다 갖고 있다."
        " 밖에서 받아 오는 표가 되었으면 `받아_온_표` 로 옮겨라"
    )
