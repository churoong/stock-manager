"""이 종목 자신의 시세 이력에서 낸 사실들 — 낙폭 회복·최악의 한 달·계절성·신고가 뒤 (docs/analysis.md 31~34장).

주간 성과 지표 작업이 이미 읽은 5년 수정주가(13장 국면표와 같은 계열)로 낸다 — 추가 읽기 없음.
결과는 `price_patterns.stats_json` 의 키 하나씩(drawdown·tail·season·breakout)으로 쌓이고,
일일 의견이 진단 줄과 화면에 싣는다.
**문턱으로 판정하지 않는다** — 사실(횟수·분포)만. 계산만 한다 — DB 도 시각도 모른다.
"""

from __future__ import annotations

from typing import Any

from batch.services import patterns as pt

#: 낙폭 회복 시간표의 깊이 단계 (31장). 흔히 쓰는 구분(−10% 조정·−20% 약세장)과 그 아래 한 칸을 빌린 **표시 기준** —
#: 판정이 아니다
DD_LEVELS = (0.10, 0.20, 0.30)
#: 낙폭·최악의 한 달·신고가를 낼 최소 거래일 — 1년이 안 되면 한 국면뿐이다
MIN_DAYS = 252
#: 최악의 한 달 (32장) — 꼬리 확률 5%, 한 달 = 21거래일
TAIL_P = 0.05
MONTH_DAYS = 21
#: 계절성 (33장) — 달마다 표본이 이보다 적으면 그 달을 비운다. 5년 이력이면 달마다 4~5번이다
SEASON_MIN = 3
#: 신고가 뒤 (34장) — 한 사건의 뒤 1개월 창이 겹치지 않게, 앞 사건에서 이 거래일이 지나야 새 사건으로 센다
BREAKOUT_COOLDOWN = 21
#: 신고가 뒤를 잴 기간(개월) — 13장과 같은 거래일 환산
BREAKOUT_MONTHS = (1, 3)


def drawdowns(dates: list[str], closes: list[float]) -> dict[str, Any] | None:
    """낙폭 회복 시간표 (31장). 고점(그때까지 최고 종가)에서 빠졌다가 그 고점을 되찾기까지를 한 번으로 센다.

    단계마다: 그만큼 이상 빠진 횟수, **그 깊이를 처음 지난 날부터** 고점을 되찾기까지 거래일의 중앙값,
    아직 못 되찾은 횟수.
    지금: 고점 대비 낙폭·고점 날짜·고점 뒤 거래일. 가장 깊었던 세 번."""
    쌍 = [(str(d), float(c)) for d, c in zip(dates, closes, strict=False) if c and c > 0]
    if len(쌍) < MIN_DAYS:
        return None
    peak, peak_i = 쌍[0][1], 0
    eps: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for i, (_, c) in enumerate(쌍):
        if c >= peak:
            if cur:
                cur["rec_i"] = i
                eps.append(cur)
                cur = None
            peak, peak_i = c, i
            continue
        dd = c / peak - 1
        if cur is None:
            cur = {"peak_i": peak_i, "trough_i": i, "depth": dd, "cross": {}}
        if dd < cur["depth"]:
            cur["depth"], cur["trough_i"] = dd, i
        for lv in DD_LEVELS:
            if dd <= -lv and lv not in cur["cross"]:
                cur["cross"][lv] = i
    모두 = eps + ([cur] if cur else [])
    levels: dict[str, Any] = {}
    for lv in DD_LEVELS:
        닿음 = [e for e in 모두 if lv in e["cross"]]
        회복 = sorted(e["rec_i"] - e["cross"][lv] for e in 닿음 if "rec_i" in e)
        levels[f"{lv:.2f}"] = {
            "n": len(닿음), "recovered": len(회복), "open": len(닿음) - len(회복),
            "median_days": pt._q([float(x) for x in 회복], 0.5) if 회복 else None,
        }  # fmt: skip

    def 한번(e: dict) -> dict:
        return {"peak": 쌍[e["peak_i"]][0], "trough": 쌍[e["trough_i"]][0], "depth": round(e["depth"], 4),
                "recovered": 쌍[e["rec_i"]][0] if "rec_i" in e else None,
                "days": (e["rec_i"] - e["peak_i"]) if "rec_i" in e else None}  # fmt: skip

    now = 쌍[-1][1] / peak - 1
    return {
        "since": 쌍[0][0], "until": 쌍[-1][0], "now": round(now, 4), "peak_date": 쌍[peak_i][0],
        "days_since_peak": len(쌍) - 1 - peak_i, "levels": levels,
        "worst": [한번(e) for e in sorted(모두, key=lambda e: e["depth"])[:3]],
    }  # fmt: skip


def tail(dates: list[str], closes: list[float]) -> dict[str, Any] | None:
    """최악의 하루·한 달 (32장). 자기 이력의 실제 수익률 분포에서 — 정규분포를 가정하지 않는다(꼬리가 그대로).

    var = 하위 `TAIL_P` 분위, cvar = 그 분위 이하 수익률의 평균("가장 나쁜 5% 의 경우 평균"), worst = 가장 나빴던 값.
    한 달은 21거래일 수익률을 날마다 겹쳐 잰다(창이 겹쳐 표본이 서로 독립이 아니다 — 문서에 적음)."""
    c = [float(x) for x in closes if x and x > 0]
    if len(c) < MIN_DAYS:
        return None
    out: dict[str, Any] = {"since": str(dates[0]), "until": str(dates[-1])}
    for 이름, d in (("d1", 1), ("m1", MONTH_DAYS)):
        r = sorted(c[i + d] / c[i] - 1 for i in range(len(c) - d))
        var = pt._q(r, TAIL_P)
        꼬리 = [x for x in r if x <= var]
        out[이름] = {"n": len(r), "var": round(var, 4), "cvar": round(sum(꼬리) / len(꼬리), 4),
                    "worst": round(r[0], 4)}  # fmt: skip
    return out


def season(dates: list[str], closes: list[float]) -> dict[str, Any] | None:
    """계절성 달력 (33장). 달마다(그달 마지막 종가) 수익률을 달력의 달(1~12월)로 모은다. 2년이 안 되면 None.
    달마다 횟수·오른 횟수·평균. `SEASON_MIN` 번 미만인 달은 비운다. 우연이 크다 — 판정에 쓰지 않는다."""
    끝 = pt._month_ends([str(d) for d in dates], [float(x) for x in closes])
    달 = sorted(끝)
    if len(달) < 25:
        return None
    모음: dict[str, list[float]] = {}
    for a, b in zip(달, 달[1:], strict=False):
        모음.setdefault(b[5:7], []).append(끝[b] / 끝[a] - 1)
    months = {m: {"n": len(v), "up": sum(1 for x in v if x > 0), "avg": round(sum(v) / len(v), 4)}
              for m, v in sorted(모음.items()) if len(v) >= SEASON_MIN}  # fmt: skip
    return {"since": 달[0], "until": 달[-1], "months": months} if months else None


def breakout(dates: list[str], closes: list[float]) -> tuple[dict[str, Any] | None, dict[str, list[float]]]:
    """52주 신고가 뒤 (34장). 종가가 앞 252거래일 최고 종가를 넘은 날을 사건으로
    (앞 사건 뒤 `BREAKOUT_COOLDOWN` 거래일 안은 같은 사건).
    사건 뒤 1·3개월 수익 분포와 평소(모든 날) 분포. 두 번째 값은 시장 전체로 모을 날 수익 목록(저장하지 않는다)."""
    c = [float(x) for x in closes if x and x > 0]
    raw: dict[str, list[float]] = {str(m): [] for m in BREAKOUT_MONTHS}
    if len(c) < MIN_DAYS + 1:
        return None, raw
    사건: list[int] = []
    from collections import deque

    덱: deque[int] = deque()  # 앞 252일 최고값 — 단조 덱
    for i in range(len(c)):
        if i >= pt.HIGH_DAYS:
            while 덱 and 덱[0] < i - pt.HIGH_DAYS:
                덱.popleft()
            if 덱 and c[i] > c[덱[0]] and (not 사건 or i - 사건[-1] > BREAKOUT_COOLDOWN):
                사건.append(i)
        while 덱 and c[덱[-1]] <= c[i]:
            덱.pop()
        덱.append(i)
    h: dict[str, Any] = {}
    base: dict[str, Any] = {}
    for m in BREAKOUT_MONTHS:
        d = pt.HORIZON_DAYS[m]
        뒤 = [c[i + d] / c[i] - 1 for i in 사건 if i + d < len(c)]
        raw[str(m)] = 뒤
        h[str(m)] = pt.dist(뒤)
        base[str(m)] = pt.dist([c[i + d] / c[i] - 1 for i in range(pt.HIGH_DAYS, len(c) - d)])
    return {"events": len(사건), "last": str(dates[사건[-1]]) if 사건 else None, "h": h, "base": base}, raw


def market_key(country: str) -> str:
    """시장 전체 신고가 뒤 분포의 설정 키"""
    return f"breakout_{country}"


def market_breakout(raws: list[dict[str, list[float]]]) -> dict[str, Any] | None:
    """시장 전체 신고가 뒤 (34장) — 종목마다의 사건 뒤 수익을 한데 모은 분포."""
    out = {}
    for m in BREAKOUT_MONTHS:
        모음 = [x for r in raws for x in r.get(str(m), [])]
        if 모음:
            out[str(m)] = pt.dist(모음)
    return out or None


def _p(x: float) -> str:
    return f"{x * 100:+.1f}%"


def lines(h: dict | None, market_bo: dict | None, today_month: int) -> list[str]:
    """진단 줄 (31~34장). 있는 것만."""
    h = h or {}
    줄: list[str] = []
    dd = h.get("drawdown") or {}
    if dd.get("levels"):
        깊이 = -float(dd["now"])
        lv = max([x for x in DD_LEVELS if 깊이 >= x], default=DD_LEVELS[0])
        s = dd["levels"].get(f"{lv:.2f}") or {}
        앞 = f"낙폭 회복({dd['since'][:4]}~ 자기 이력): 지금 고점({dd['peak_date']}) 대비 {_p(dd['now'])}"
        if s.get("n"):
            회복 = (f"{s['recovered']}번은 그 깊이에서 고점까지 중앙값 {s['median_days']:.0f}거래일"
                    if s.get("recovered") else "아직 한 번도 고점을 되찾지 못함")  # fmt: skip
            줄.append(f"{앞} — 과거 −{lv * 100:.0f}% 이상 빠진 {s['n']}번 중 {회복}"
                      + (f", {s['open']}번은 아직 못 되찾음"
                         if s.get("open") and s.get("recovered") else ""))  # fmt: skip
        else:
            줄.append(f"{앞} — 이 이력에서 −{lv * 100:.0f}% 이상 빠진 적이 없다")
    t = (h.get("tail") or {}).get("m1")
    if t:
        줄.append(f"최악의 한 달(자기 이력 {t['n']}개 창): 가장 나쁜 {TAIL_P * 100:.0f}% 의 한 달 평균 {_p(t['cvar'])}"
                  f"(그 문턱 {_p(t['var'])}, 가장 나빴던 한 달 {_p(t['worst'])})")  # fmt: skip
    se = (h.get("season") or {}).get("months") or {}
    조각 = []
    for m in (today_month, today_month % 12 + 1):
        x = se.get(f"{m:02d}")
        if x:
            조각.append(f"{m}월 {x['n']}번 중 {x['up']}번 오름·평균 {_p(x['avg'])}")
    if 조각:
        줄.append("계절성(우연이 큼 — 판정에 쓰지 않음): " + " · ".join(조각))
    bo = h.get("breakout") or {}
    b1 = (bo.get("h") or {}).get("1")
    if b1:
        평소 = (bo.get("base") or {}).get("1") or {}
        시장 = (market_bo or {}).get("1") or {}
        견줌 = [f"{이름} {_p(x['median'])}·{x['up'] * 100:.0f}%"
                for 이름, x in (("이 종목 평소", 평소), ("시장 전체 신고가 뒤", 시장)) if x]  # fmt: skip
        줄.append(f"52주 신고가 뒤(지난 {bo['events']}번): 1개월 중앙값 {_p(b1['median'])}·"
                  f"오른 비율 {b1['up'] * 100:.0f}%" + (f" ({'; '.join(견줌)})" if 견줌 else ""))  # fmt: skip
    return 줄
