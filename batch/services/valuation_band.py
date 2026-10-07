"""밸류에이션 밴드 — 종목 상세 화면용 (docs/stock_detail.md 2장).

계산식은 신호의 장기 밴드와 같다(docs/signals.md 1.3절). 함수도 그대로 가져다 쓴다.
신호와 다른 점은 두 가지다.

1. 현재값이 밴드의 몇 % 지점인지를 **3년 계열 전체**로 낸다. 신호의 band_rank 는 분위 네 점
   (20/30/50/80) 사이에서만 세어 0·25·50·75·100 다섯 값만 나온다. 화면에는 거친 값이라 따로 낸다
2. 업종 안 백분위를 더한다. 자기 과거와 비교(밴드)와 남과 비교(업종)를 나란히 보여 주려고
"""

from __future__ import annotations

from dataclasses import dataclass

from batch.services import signals as sg

CALC_VERSION = 1
METRIC = "PBR"
# **신호에서 가져다 쓴다** (2026-09-22, docs/infra.md 25.108).
# "신호와 같은 값" 이라고 적어 두고 숫자는 따로 적고 있었다 — 한쪽만 바뀌면 아무도 모른다
BAND_DAYS = sg.BAND_DAYS
MIN_SAMPLE = sg.BAND_MIN_SAMPLE
# 업종 비교 집단의 최소 크기. 4종목 안에서의 백분위는 25% 단위로 튀어 뜻이 없다 [확인필요: 근거는 판단]
MIN_PEERS = 5


@dataclass
class BandResult:
    current_value: float | None
    p20: float | None
    p30: float | None
    p50: float | None
    p80: float | None
    sample: int
    band_rank: float | None
    price_date: str | None
    equity_report_date: str | None
    skip_reason: str | None


def compute(
    prices: list[tuple[str, float]],
    equities: list[tuple[str, float]] | list[tuple[str, float, int]],
    listed_shares: int | None,
) -> BandResult:
    """한 종목의 밴드. prices·equities 는 날짜 오름차순. prices 는 최근 BAND_DAYS 일만 넘긴다."""

    def skip(reason: str, **kw) -> BandResult:
        base = dict(current_value=None, p20=None, p30=None, p50=None, p80=None, sample=0, band_rank=None,
                    price_date=None, equity_report_date=None)  # fmt: skip
        base.update(kw)
        return BandResult(skip_reason=reason, **base)

    if not listed_shares or listed_shares <= 0:
        return skip("상장주식수 없음")
    if not prices:
        return skip("가격 없음")
    if not equities:
        return skip("연간 연결 자본총계 없음")

    price_date, close = prices[-1]
    # 현재 PBR 도 밴드와 같은 행을 쓴다 — 가장 늦은 사업연도, 같은 연도면 정정 (25.549)
    known = sg.known_equity_row(equities, price_date)
    current = None
    equity_date = known[0] if known else None
    if known and known[1] > 0 and close > 0:
        current = close / (known[1] / listed_shares)

    series = sg.pbr_series(prices, equities, listed_shares)
    band = sg.build_band(series, min_sample=MIN_SAMPLE)
    if band is None:
        return skip(f"PBR 표본 {len(series)}일 (250일 미만)", current_value=current, sample=len(series),
                    price_date=price_date, equity_report_date=equity_date)  # fmt: skip
    return BandResult(
        current_value=current,
        p20=band.p20,
        p30=band.p30,
        p50=band.p50,
        p80=band.p80,
        sample=band.sample,
        band_rank=sg.percentile_rank(series, current) if current is not None else None,
        price_date=price_date,
        equity_report_date=equity_date,
        skip_reason=None if current is not None else "자본총계가 0 이하라 현재 PBR 없음",
    )


def peer_percentiles(
    rows: list[tuple[int, str | None, float | None]], country: str
) -> dict[int, tuple[float, str, int]]:
    """(stock_id, 업종, 현재 PBR) 목록 → stock_id: (백분위, 집단, 집단 크기).

    업종이 MIN_PEERS 종목 미만이거나 비어 있으면 그 나라 전체와 비교한다. 현재값이 없는 종목은 뺀다.
    백분위 = 자기보다 PBR 이 낮은 종목 비율 × 100. 낮을수록 업종 안에서 싸다.
    """
    valued = [(sid, sector, value) for sid, sector, value in rows if value is not None]
    by_sector: dict[str, list[float]] = {}
    for _, sector, value in valued:
        if sector:
            by_sector.setdefault(sector, []).append(value)
    market = [value for _, _, value in valued]

    out: dict[int, tuple[float, str, int]] = {}
    for sid, sector, value in valued:
        peers = by_sector.get(sector or "", [])
        if len(peers) >= MIN_PEERS:
            group, pool = f"sector:{country}:{sector}", peers
        else:
            group, pool = f"market:{country}", market
        rank = sg.percentile_rank(pool, value)
        if rank is not None:
            out[sid] = (rank, group, len(pool))
    return out
