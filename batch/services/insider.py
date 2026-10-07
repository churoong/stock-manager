"""내부자 매매 집계 (docs/signals.md 8장). insider_trades 표만 읽는다.

추천 근거표의 **참고 행**을 만든다. 판정에 쓰지 않고 팩터에도 넣지 않는다 — 보고가 회사당
몇 건이라 분포가 성겨, 점수에 넣으면 몇 건이 순위를 휘두르기 때문이다.

시점 규칙: 접수일(filed_date) ≤ 기준일인 행만 본다. 거래일로 거르면 접수 전의 거래를
미리 아는 셈이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from batch.core.turso import TursoClient

# 기준일 이전 며칠까지 "최근" 으로 보나. 추정 [확인필요: 표가 쌓이면 조정]
WINDOW_DAYS = 90

SOURCE_LABEL = "insider_trades (내부자 매매 보고)"


@dataclass(frozen=True)
class Trade:
    filed_date: str
    insider: str
    action: str  # buy · sell · other
    shares: int
    price: float | None = None
    trade_date: str | None = None
    role: str | None = None


@dataclass
class InsiderSummary:
    as_of: str
    window_days: int
    since: str
    buy_shares: int = 0
    sell_shares: int = 0
    buyers: list[str] = field(default_factory=list)
    sellers: list[str] = field(default_factory=list)
    latest_filed: str | None = None
    reports: int = 0

    @property
    def net_shares(self) -> int:
        return self.buy_shares - self.sell_shares

    def net_ratio(self, listed_shares: int | None) -> float | None:
        """순매수 주식수 / 상장주식수. 주식수를 모르면 None."""
        if not listed_shares or listed_shares <= 0:
            return None
        return self.net_shares / listed_shares

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "window_days": self.window_days,
            "since": self.since,
            "buy_shares": self.buy_shares,
            "sell_shares": self.sell_shares,
            "net_shares": self.net_shares,
            "buyers": len(self.buyers),
            "sellers": len(self.sellers),
            "latest_filed": self.latest_filed,
            "reports": self.reports,
        }


def summarize(trades: list[Trade], as_of: str, window_days: int = WINDOW_DAYS) -> InsiderSummary | None:
    """창 안의 매수·매도를 합친다. 창 안에 보고가 없으면 None (행을 만들지 않는다)."""
    since = (date.fromisoformat(as_of) - timedelta(days=window_days)).isoformat()
    summary = InsiderSummary(as_of=as_of, window_days=window_days, since=since)
    for t in sorted(trades, key=lambda t: t.filed_date):
        if t.filed_date > as_of or t.filed_date < since:
            continue
        summary.reports += 1
        summary.latest_filed = t.filed_date
        if t.action == "buy":
            summary.buy_shares += t.shares
            if t.insider not in summary.buyers:
                summary.buyers.append(t.insider)
        elif t.action == "sell":
            summary.sell_shares += t.shares
            if t.insider not in summary.sellers:
                summary.sellers.append(t.insider)
    return summary if summary.reports else None


def load_trades(
    client: TursoClient, country: str, as_of: str, window_days: int = WINDOW_DAYS
) -> dict[int, list[Trade]]:
    """그 나라 종목의 창 안 보고. {stock_id: [Trade]}. 표가 비었으면 빈 dict."""
    since = (date.fromisoformat(as_of) - timedelta(days=window_days)).isoformat()
    rs = client.execute(
        "SELECT t.stock_id, t.filed_date, t.insider, t.action, t.shares, t.price, t.trade_date, t.role"
        " FROM insider_trades t JOIN stocks s ON s.id = t.stock_id"
        " WHERE s.country = ? AND t.filed_date >= ? AND t.filed_date <= ?"
        " ORDER BY t.stock_id, t.filed_date",
        [country, since, as_of],
    )
    out: dict[int, list[Trade]] = {}
    for row in rs.dicts():
        out.setdefault(int(row["stock_id"]), []).append(
            Trade(
                filed_date=str(row["filed_date"]),
                insider=str(row["insider"]),
                action=str(row["action"]),
                shares=int(row["shares"]),
                price=None if row["price"] is None else float(row["price"]),
                trade_date=None if row["trade_date"] is None else str(row["trade_date"]),
                role=None if row["role"] is None else str(row["role"]),
            )
        )
    return out


def load_summaries(client: TursoClient, country: str, as_of: str) -> dict[int, InsiderSummary]:
    """종목별 집계. 보고가 없는 종목은 키가 없다."""
    out: dict[int, InsiderSummary] = {}
    for stock_id, trades in load_trades(client, country, as_of).items():
        summary = summarize(trades, as_of)
        if summary is not None:
            out[stock_id] = summary
    return out


def criteria_rows(summary: InsiderSummary | None, listed_shares: int | None) -> list[dict]:
    """근거표 참고 행 (passed=None). 요약이 없으면 빈 목록 — "없음" 을 적지 않는다."""
    if summary is None:
        return []
    ratio = summary.net_ratio(listed_shares)
    net = summary.net_shares
    direction = "순매수" if net > 0 else ("순매도" if net < 0 else "중립")
    display = f"{direction} {abs(net):,}주"
    if ratio is not None:
        display += f" (상장주식수의 {ratio * 100:+.3f}%)"
    display += f" · 매수 {len(summary.buyers)}명 · 매도 {len(summary.sellers)}명 · 보고 {summary.reports}건"
    return [
        {
            "label": "내부자 매매 (참고)",
            "display": display,
            "threshold": (
                f"최근 {summary.window_days}일 (접수일 {summary.since}~{summary.as_of})."
                " 판정에 쓰지 않음 (docs/signals.md 8장)"
            ),
            "source": SOURCE_LABEL,
            "as_of": summary.latest_filed,
            "passed": None,
        }
    ]
