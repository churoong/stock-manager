"""환율 환산 (docs/signals.md 3.4).

총 투자가능금액·최소 주문 금액은 설정에 원화로만 있다. 미국 신호의 권장 금액은 달러여야
주가(달러)와 나란히 읽힌다. 그래서 원화 설정값을 그날 USDKRW 로 나눈다.

환율이 없거나 너무 오래됐으면 금액을 내지 않는다. 틀린 통화의 금액보다 빈칸이 낫다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from batch.core.turso import TursoClient

PAIR = "USDKRW"
YAHOO_SYMBOL = "KRW=X"

# 이보다 오래된 환율로는 환산하지 않는다. 원·달러는 한 주에 1~2% 움직인다.
# 연휴(추석·미국 휴장)가 겹쳐도 수집이 5 거래일 넘게 비지는 않는다. 추정이다 [확인필요].
MAX_STALE_DAYS = 7


@dataclass(frozen=True)
class FxRate:
    pair: str
    date: str
    rate: float
    source: str

    def describe(self) -> str:
        return f"{self.rate:,.2f}원/달러 ({self.date}, {self.source} {YAHOO_SYMBOL})"

    def as_dict(self) -> dict:
        return {"pair": self.pair, "date": self.date, "rate": self.rate, "source": self.source}


def convert_krw(amount_krw: float, currency: str, rate: FxRate | None) -> float | None:
    """원화 금액을 그 종목의 통화로. 환산할 수 없으면 None."""
    if currency == "KRW":
        return amount_krw
    if currency != "USD" or rate is None or rate.rate <= 0:
        return None
    return amount_krw / rate.rate


def usable(rate: FxRate | None, on: date) -> FxRate | None:
    """기준일에 쓸 수 있는 환율. 미래 날짜거나 MAX_STALE_DAYS 보다 오래되면 None."""
    # **0 이하·NaN 환율은 없는 것이다** (docs/infra.md 25.687, 감사 재현). 날짜만 봐서 0 이 들어오면 사이징 설정이
    # `최소 주문 ÷ 환율` 에서 ZeroDivisionError 로 미국 신호 배치가 통째로 멈췄고, 음수면 "총액 미설정" 이라는
    # 틀린 사유가 나갔다. 수집기는 양수만 넣지만 수동 입력·복구로 들어올 수 있고 표에 CHECK 가 없다
    if rate is None or not math.isfinite(rate.rate) or rate.rate <= 0:
        return None
    age = (on - date.fromisoformat(rate.date)).days
    return rate if 0 <= age <= MAX_STALE_DAYS else None


def latest_rate(client: TursoClient, on_or_before: str, pair: str = PAIR) -> FxRate | None:
    rs = client.execute(
        # 0 이하 행은 건너뛰고 그 앞의 정상 환율로 (25.690) — 가장 최근 행이 0 이면 7일 안의 정상 값을 두고 "없음" 이
        # 됐다
        "SELECT pair, date, rate, source FROM fx_rates WHERE pair = ? AND date <= ? AND rate > 0"
        " ORDER BY date DESC LIMIT 1",
        [pair, on_or_before],
    )
    if not rs.rows:
        return None
    row = rs.rows[0]
    return usable(FxRate(str(row[0]), str(row[1]), float(row[2]), str(row[3])), date.fromisoformat(on_or_before))
