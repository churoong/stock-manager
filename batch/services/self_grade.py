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

#: 평균을 내는 표본 하한 — 성적표(`outcomes.summarize`)와 같은 값
MIN_N = 5
#: 보는 창(거래일). 60일은 리포트가 쌓인 뒤에 뜻이 있다 — 머리 한 줄에 셋은 길다
WINDOWS = (5, 20)


@dataclass(frozen=True)
class WindowGrade:
    window: int
    n: int
    avg_ret: float | None
    win_rate: float | None


@dataclass(frozen=True)
class SelfGrade:
    windows: tuple[WindowGrade, ...]
    first_date: str | None
    last_date: str | None


def grade(rows: list[dict[str, Any]]) -> SelfGrade:
    """행 — {stock_id, trade_date, ret_5d, ret_20d} — 종목·리포트 날마다 한 건. 창마다 평균·이긴 비율."""
    seen: set[tuple[int, str]] = set()
    rets: dict[int, list[float]] = {w: [] for w in WINDOWS}
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
    out = []
    for w in WINDOWS:
        xs = rets[w]
        n = len(xs)
        if n < MIN_N:
            out.append(WindowGrade(w, n, None, None))
        else:
            out.append(WindowGrade(w, n, sum(xs) / n, sum(1 for x in xs if x > 0) / n))
    return SelfGrade(tuple(out), min(dates) if dates else None, max(dates) if dates else None)


def _pct(v: float) -> str:
    return f"{round(v * 100, 1) + 0.0:+.1f}%"


def line(g: SelfGrade) -> str | None:
    """머리 한 줄. 지난 리포트가 하나도 없으면 None — 첫 리포트에 "0건" 을 적지 않는다."""
    if g.first_date is None:
        return None
    parts = []
    for w in g.windows:
        if w.avg_ret is None or w.win_rate is None:
            parts.append(f"{w.window}일 뒤 아직 셀 수 없음 ({w.n}건)")
        else:
            parts.append(f"{w.window}일 뒤 평균 {_pct(w.avg_ret)} · 이긴 {w.win_rate * 100:.0f}% ({w.n}건)")
    # 같은 날이면 한 번만 (25.959 — 10-06 리포트에 "10-01~10-01 리포트" 가 찍혔다)
    기간 = (
        "" if not (g.first_date and g.last_date)
        else g.first_date[5:] if g.first_date == g.last_date
        else f"{g.first_date[5:]}~{g.last_date[5:]}"
    )
    return f"지난 추천 자기 채점: {' · '.join(parts)}" + (f" — {기간} 리포트" if 기간 else "")
