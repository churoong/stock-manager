"""한 번도 시세가 없던 오래된 종목은 미국 일일 배치의 "못 받음" 경고에서 뺀다 (docs/infra.md 25.922).

시노백(SVA)은 몇 년째 거래정지라 야후에 시세가 없어 미국 일일 배치가 날마다 partial 이었다.
"""

from __future__ import annotations

from types import SimpleNamespace

from batch.jobs import daily
from tests.test_scores_job import _sqlite_client


def _준비():
    c = _sqlite_client()
    for sid, t in ((51, "SVA"), (52, "AAPL"), (53, "NEWCO")):
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, yahoo_symbol, source, fetched_at)"
            " VALUES (?, ?, 'NASDAQ', 'US', 'USD', 'active', ?, 't', 't')",
            [sid, t, t],
        )
    c.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (52, '2026-09-01', 1, 'USD', 't', 't')")
    for d in ("2026-09-14", "2026-09-21"):
        for sid in (51, 52):
            c.execute(
                "INSERT INTO universe_members (stock_id, snapshot_date, included, exclude_reason, currency, created_at)"
                " VALUES (?, ?, 0, 'x', 'USD', 't')",
                [sid, d],
            )
    return c


def test_오래된_무시세_종목만_경고에서_빠진다() -> None:
    c = _준비()
    ids = {"SVA": 51, "AAPL": 52, "NEWCO": 53}
    note = "시세를 받지 못한 종목 3개 (예: AAPL, NEWCO, SVA)"
    got = daily._drop_never_priced(c, note, ["AAPL", "NEWCO", "SVA"], [], ids)  # type: ignore[arg-type]
    assert "SVA" not in got and "AAPL" in got and "NEWCO" in got  # 시세가 있던 종목·새 종목은 그대로 경고
    assert "2개" in got


def test_그_종목만_빠졌으면_경고가_비고_다른_경고는_남는다() -> None:
    c = _준비()
    note = "3조각 중 1조각 실패: x; 시세를 받지 못한 종목 1개 (예: SVA)"
    got = daily._drop_never_priced(c, note, ["SVA", "AAPL"], [SimpleNamespace(ticker="AAPL")], {"SVA": 51, "AAPL": 52})  # type: ignore[arg-type]
    assert got == "3조각 중 1조각 실패: x"
