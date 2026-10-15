"""리포트 자기 채점 — 지난 리포트 1부에 실렸던 종목이 그 뒤 어땠나, 오늘 리포트 머리에 (docs/reports.md 3.4, 25.951).

신호 성적표(docs/signals.md 10장)는 **모든 신호**의 성적이고 웹에만 있다. 리포트는 그 가운데 근거표를 갖춘 상위 다섯만
싣는다 — 사용자가 실제로 본 것은 그 다섯이다. 그 다섯이 그 뒤 어땠는지를 리포트가 스스로 머리에 적는다:

    지난 추천 자기 채점: 5일 뒤 평균 +0.8% · 이긴 54% (31건, 09-17~09-26) · 20일 뒤 아직 셀 수 없음 (0건)

왜 머리인가: 1부를 읽기 전에 "이 리포트의 최근 성적" 을 먼저 보게 한다. 추천을 더 믿게 하려는 것이 아니라 **덜 믿어야
할 때 덜 믿게** 하려는 것이다 — 성적이 나쁜 주에는 그 말이 맨 위에 있다.

센다 = report_items(recommend) ⋈ daily_reports(이 시장, 오늘 전) ⋈ signal_outcomes(종목·신호 기준일
`payload.as_of_date`). 같은 종목·같은 리포트 날은 한 건(기간별 항목이 셋이어도 진입가는 하나다). 수익률은 성적표와
같은 정의(진입 = 기준일 다음 거래일 종가, 창 = 5·20 거래일, docs/signals.md 10장). 창이 안 찬 건은 그 창에서 빠진다.
5건 미만이면 평균을 내지 않는다(성적표와 같은 하한).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from batch.core.rounding import fixed

#: 평균을 내는 표본 하한 — 성적표(`outcomes.summarize`)와 같은 값
MIN_N = 5
#: 보는 창(거래일). 60일은 리포트가 쌓인 뒤에 뜻이 있다 — 머리 한 줄에 셋은 길다
WINDOWS = (5, 20)


#: 지수 대비를 내는 창 — 굳은 성적표에 지수 칸이 20일만 있다(`signal_outcomes.bench_ret_20d`, 25.702)
BENCH_WINDOW = 20


@dataclass(frozen=True)
class WindowGrade:
    window: int
    n: int
    avg_ret: float | None
    win_rate: float | None
    #: 서로 다른 종목 수 (27회차 P6, docs/infra.md 25.1404) — 연속으로 실린 종목은 여러 건으로 센다
    stocks: int = 0
    #: 지수 대비 평균(수익 − 같은 창 시장 지수 수익). 20일 창만, 지수가 있는 건이 95% 이상일 때만
    excess: float | None = None
    bench_n: int = 0


@dataclass(frozen=True)
class SelfGrade:
    windows: tuple[WindowGrade, ...]
    first_date: str | None
    last_date: str | None


def grade(rows: list[dict[str, Any]]) -> SelfGrade:
    """행 — {stock_id, trade_date, ret_5d, ret_20d} — 종목·리포트 날마다 한 건. 창마다 평균·이긴 비율."""
    from batch.services.outcomes import EXCESS_SAMPLE_SHARE

    seen: set[tuple[int, str]] = set()
    rets: dict[int, list[float]] = {w: [] for w in WINDOWS}
    종목: dict[int, set[int]] = {w: set() for w in WINDOWS}
    초과: list[float] = []
    dates: list[str] = []
    for r in rows:
        sid, d = r.get("stock_id"), r.get("trade_date")
        if sid is None or not d:
            continue
        key = (int(sid), str(d))
        if key in seen:
            continue
        seen.add(key)
        dates.append(str(d))
        for w in WINDOWS:
            v = r.get(f"ret_{w}d")
            if isinstance(v, int | float) and v == v:
                rets[w].append(float(v))
                종목[w].add(int(sid))
                b = r.get(f"bench_ret_{w}d") if w == BENCH_WINDOW else None
                if isinstance(b, int | float) and b == b:
                    초과.append(float(v) - float(b))
    out = []
    for w in WINDOWS:
        xs = rets[w]
        n = len(xs)
        k = len(종목[w])
        if n < MIN_N:
            out.append(WindowGrade(w, n, None, None, k))
            continue
        # 지수 대비는 덧붙이기만 한다 — 원 수익·"이긴" 정의는 그대로(27회차 검증 B). 덮임은 성적표와 같은 95%
        덮임 = w == BENCH_WINDOW and len(초과) >= n * EXCESS_SAMPLE_SHARE
        out.append(WindowGrade(w, n, sum(xs) / n, sum(1 for x in xs if x > 0) / n, k,
                               sum(초과) / len(초과) if 덮임 else None, len(초과) if w == BENCH_WINDOW else 0))
    return SelfGrade(tuple(out), min(dates) if dates else None, max(dates) if dates else None)


def _pct(v: float) -> str:
    return f"{fixed(v * 100, 1, sign=True)}%"


def line(g: SelfGrade) -> str | None:
    """머리 한 줄. 지난 리포트가 하나도 없으면 None — 첫 리포트에 "0건" 을 적지 않는다."""
    if g.first_date is None:
        return None
    parts = []
    for w in g.windows:
        # 서로 다른 종목 수를 함께 (27회차 P6, 25.1404) — "31건" 이 사실 9종목일 수 있다
        건 = f"{w.n}건·{w.stocks}종목" if w.stocks else f"{w.n}건"
        if w.avg_ret is None or w.win_rate is None:
            parts.append(f"{w.window}일 뒤 아직 셀 수 없음 ({건})")
        else:
            # 지수 대비는 원 수익 옆에만 — 원 수익을 빼고 지수 대비만 보이면 약세장의 낮은 베타가 실력처럼 보인다
            대비 = f" · 시장 지수 대비 {fixed(w.excess * 100, 1, sign=True)}%p" if w.excess is not None else ""
            parts.append(f"{w.window}일 뒤 평균 {_pct(w.avg_ret)}{대비} · 이긴 {fixed(w.win_rate * 100, 0)}% ({건})")
    # 같은 날이면 한 번만 (25.959 — 10-06 리포트에 "10-01~10-01 리포트" 가 찍혔다)
    기간 = (
        "" if not (g.first_date and g.last_date)
        else g.first_date[5:] if g.first_date == g.last_date
        else f"{g.first_date[5:]}~{g.last_date[5:]}"
    )
    return f"지난 추천 자기 채점: {' · '.join(parts)}" + (f" — {기간} 리포트" if 기간 else "")


def stats(g: SelfGrade) -> dict[str, Any]:
    """실행 기록용 수 (25.1404) — 20일 창의 건수·종목 수·지수 덮임"""
    w = next((x for x in g.windows if x.window == BENCH_WINDOW), None)
    if w is None:
        return {}
    return {"n20": w.n, "k20": w.stocks, "bench_cov": round(w.bench_n / w.n, 3) if w.n else None}
