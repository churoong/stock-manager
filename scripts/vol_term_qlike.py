"""19회차 A3 — 보유 기간 변동성 σ(t) 가 3년 변동성 L 보다 앞날 변동성을 잘 맞히는가 (일회성 검증).

docs/factors.md 12.21, docs/infra.md 25.1313. **결과를 보기 전에 이 파일로 사전 등록한다.** 아래 상수·판정 규칙을
결과를 본 뒤 바꿔 다시 돌리지 않는다. 일부 칸만 이겼다고 그 칸만 채택하지 않는다.

- 무엇을 견주나: 기준일 d 마다 종목의 3년 변동성 L(d 까지 756거래일, 운영 `metrics.volatility` 와 같은 식)과
  σ(t) = `verdict.term_sigma(L, S, t)`(S = d 까지 `patterns.ewma_vol`, λ 0.94, 반감기 2개월)를, d+1 부터 h 거래일의
  실현 변동성에 견준다. 손실은 QLIKE(Patton 2011): σ²/σ̂² − ln(σ²/σ̂²) − 1 (작을수록 좋다)
- 8칸: 나라(KR·US) × 기간(단기 t = 92/365·h = 63, 중기 t = 1·h = 252) × 표본의 앞·뒤 반
- 한 칸을 "이겼다" 는 평균 손실 차(L − σ(t)) > 0 이고 Diebold–Mariano t ≥ `DM_T`(Newey–West, 시차 h/21)
- **8칸을 모두 이겨야** 사이징을 바꾼다. 바꾸더라도 **줄이기만** 한다 — 배수 = min(L 기준, σ(t) 기준)
  (지금 사이징 배수는 줄이기만 한다: docs/signals.md 3.1·3.5·3.6, 검증 B 조건)
- 표본: 매달 첫 거래일, 끝날에서 `YEARS` 년 거슬러. 그날 시세가 있는 모든 주식(폐지 종목 포함 — 시점 유니버스).
  S 를 낼 수 없으면(일간 수익률 60개 미만) 그 쌍을 뺀다
- 시세는 시세 사본(`PRICE_REPLICA_PATH`, 로컬 SQLite)에서만 읽는다 — Turso 를 읽지 않는다(이달 읽기 진도, 25.1248).
  결과는 집계만 출력한다(종목·가격을 찍지 않는다 — 공개 로그 규칙)

실행(11-01 뒤): python scripts/vol_term_qlike.py <사본 경로> [--end YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from batch.services import metrics as m  # noqa: E402
from batch.services import patterns as pt  # noqa: E402
from batch.services import verdict as vd  # noqa: E402

# ---- 사전 등록 (2026-10-13, 결과 보기 전) ----
#: 장기 변동성 창 — 운영 신호가 쓰는 3년(`performance_metrics` 3Y)
LONG_DAYS = 756
#: (이름, t 년, 실현 창 거래일) — 단기는 매도 플래그 기간초과 92일(`sell_flags.MAX_HOLDING_DAYS["short"]`), 중기는 1년
HORIZONS = (("short", 92 / 365, 63), ("mid", 1.0, 252))
#: 표본 기간(년)
YEARS = 5
#: 이겼다고 볼 Diebold–Mariano t (양측 5%)
DM_T = 1.96
COUNTRIES = ("KR", "US")
# ---------------------------------------------


def qlike(forecast_sigma: float, realized_sigma: float) -> float:
    """QLIKE 손실 (Patton 2011). 분산 비 x = σ²/σ̂² 에 대해 x − ln x − 1."""
    x = (realized_sigma / forecast_sigma) ** 2
    return x - math.log(x) - 1


def realized(closes: list[float]) -> float | None:
    """연환산 실현 변동성 — 운영 `metrics.volatility` 와 같은 식(단순 수익률 표준편차 × √252)."""
    r = [b / a - 1 for a, b in zip(closes, closes[1:], strict=False) if a > 0 and b > 0]
    if len(r) < 2:
        return None
    mu = sum(r) / len(r)
    return math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(m.TRADING_DAYS_PER_YEAR)


def pair_losses(dates: list[str], closes: list[float], i: int, t: float, h: int) -> tuple[float, float] | None:
    """기준 인덱스 i(그날까지 안다)에서 (L 의 손실, σ(t) 의 손실). 자료가 모자라면 None."""
    if i + 1 < LONG_DAYS or i + h >= len(closes):
        return None
    L = realized(closes[i + 1 - LONG_DAYS : i + 1])
    s = pt.ewma_vol(dates[max(0, i + 1 - pt.EWMA_DAYS) : i + 1], closes[max(0, i + 1 - pt.EWMA_DAYS) : i + 1])
    ahead = realized(closes[i : i + h + 1])  # d 종가에서 d+h 종가까지 — d+1 부터의 수익률
    if L is None or s is None or ahead is None or L <= 0 or ahead <= 0:
        return None
    sig = vd.term_sigma(L, float(s["ewma"]), t)
    if sig is None or sig <= 0:
        return None
    return qlike(L, ahead), qlike(sig, ahead)


def dm_t(diffs_by_date: list[float], lag: int) -> float | None:
    """날짜별 평균 손실 차의 Diebold–Mariano t (Newey–West 분산, Bartlett 가중)."""
    n = len(diffs_by_date)
    if n < 3:
        return None
    mu = sum(diffs_by_date) / n
    d = [x - mu for x in diffs_by_date]
    var = sum(x * x for x in d) / n
    for k in range(1, min(lag, n - 1) + 1):
        var += 2 * (1 - k / (lag + 1)) * sum(d[j] * d[j - k] for j in range(k, n)) / n
    return None if var <= 0 else mu / math.sqrt(var / n)


def cell_verdict(diffs_by_date: list[float], h: int) -> dict:
    """한 칸 — 날짜별 평균 차(L 손실 − σ(t) 손실)의 평균과 DM t. 양수면 σ(t) 가 낫다."""
    n = len(diffs_by_date)
    mean = sum(diffs_by_date) / n if n else None
    t = dm_t(diffs_by_date, max(1, h // 21))
    return {"dates": n, "mean_diff": mean, "dm_t": t,
            "won": bool(mean is not None and mean > 0 and t is not None and t >= DM_T)}  # fmt: skip


def decide(cells: dict[str, dict]) -> dict:
    """사전 등록 판정 — 8칸 모두 이겨야 바꾼다. 바꿔도 줄이기만."""
    다 = len(cells) == len(COUNTRIES) * len(HORIZONS) * 2 and all(c["won"] for c in cells.values())
    return {"switch": 다, "rule": "8칸 모두 이겨야 · 이겨도 배수 = min(L 기준, σ(t) 기준) · 다시 돌리기·일부 채택 금지"}


def month_starts(dates: list[str]) -> list[str]:
    out, 앞 = [], ""
    for d in dates:
        if d[:7] != 앞:
            out.append(d)
            앞 = d[:7]
    return out


def run(path: str, end: str | None) -> dict:
    conn = sqlite3.connect(path)
    cells: dict[str, dict] = {}
    for country in COUNTRIES:
        rows = conn.execute(
            "SELECT p.stock_id, p.date, COALESCE(p.adj_close, p.close) FROM prices p JOIN stocks s ON s.id = p.stock_id"
            " WHERE s.country = ? AND COALESCE(s.asset_type, 'stock') = 'stock' AND COALESCE(p.adj_close, p.close) > 0"
            " AND (? IS NULL OR p.date <= ?) ORDER BY p.stock_id, p.date",
            [country, end, end],
        ).fetchall()
        series: dict[int, tuple[list[str], list[float]]] = {}
        for sid, d, c in rows:
            series.setdefault(sid, ([], []))[0].append(str(d))
            series[sid][1].append(float(c))
        모든날 = sorted({d for ds, _ in series.values() for d in ds})
        if not 모든날:
            continue
        끝 = 모든날[-1]
        시작 = f"{int(끝[:4]) - YEARS}{끝[4:]}"
        기준일 = [d for d in month_starts(모든날) if d >= 시작]
        반 = len(기준일) // 2
        for name, t, h in HORIZONS:
            by_date: dict[str, list[float]] = {}
            for ds, cs in series.values():
                index = {d: i for i, d in enumerate(ds)}
                for d in 기준일:
                    i = index.get(d)
                    p = None if i is None else pair_losses(ds, cs, i, t, h)
                    if p is not None:
                        by_date.setdefault(d, []).append(p[0] - p[1])
            for half, 날들 in (("first", 기준일[:반]), ("second", 기준일[반:])):
                diffs = [sum(by_date[d]) / len(by_date[d]) for d in 날들 if by_date.get(d)]
                cells[f"{country}/{name}/{half}"] = cell_verdict(diffs, h)
    return {"cells": cells, **decide(cells)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("replica")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()
    print(json.dumps(run(args.replica, args.end), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
