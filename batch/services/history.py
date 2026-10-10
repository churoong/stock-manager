"""이 종목 자신의 시세 이력에서 낸 사실들 — 낙폭 회복·최악의 한 달·계절성·신고가 뒤 (docs/analysis.md 31~34장).

주간 성과 지표 작업이 이미 읽은 5년 수정주가(13장 국면표와 같은 계열)로 낸다 — 추가 읽기 없음.
결과는 `price_patterns.stats_json` 의 키 하나씩(drawdown·tail·season·breakout)으로 쌓이고,
일일 의견이 진단 줄과 화면에 싣는다.
**문턱으로 판정하지 않는다** — 사실(횟수·분포)만. 계산만 한다 — DB 도 시각도 모른다.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
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


def _달이_끝났나(last: str) -> bool:
    """마지막 날이 그달 마지막 평일 근처인가 — 다음 날이 다음 달이거나, 금요일이고 다음 월요일이 다음 달이면.
    휴장 달력은 보지 않는다(월말 하루 이틀이 휴장이면 끝난 달을 덜 끝난 것으로 봐 한 표본을 버린다 — 보수적인 쪽)."""
    d = date.fromisoformat(last)
    return (d + timedelta(days=1)).month != d.month or (d.weekday() == 4 and (d + timedelta(days=3)).month != d.month)


def season(dates: list[str], closes: list[float]) -> dict[str, Any] | None:
    """계절성 달력 (33장). 달마다(그달 마지막 종가) 수익률을 달력의 달(1~12월)로 모은다. 2년이 안 되면 None.
    달마다 횟수·오른 횟수·평균. `SEASON_MIN` 번 미만인 달은 비운다. 우연이 크다 — 판정에 쓰지 않는다."""
    끝 = pt._month_ends([str(d) for d in dates], [float(x) for x in closes])
    달 = sorted(끝)
    if 달 and not _달이_끝났나(str(dates[-1])[:10]):
        # **아직 안 끝난 이번 달은 넣지 않는다** (25.1085). 10-08 기준이면 10월 6거래일 수익이 "한 달" 표본으로
        # 들어갔고, 화면 줄은 바로 그 이번 달을 보여 준다
        달 = 달[:-1]
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


#: 매물대 (45장) — 지난 `PROFILE_DAYS` 거래일 거래대금을 수정주가 범위 `PROFILE_BINS` 칸으로 나눈다.
#: 20칸이면 1년 가격 범위의 5% 씩 — 사다리 한 칸의 가격으로 쓰기에 충분히 좁고 칸마다 날이 충분하다
PROFILE_DAYS = 252
PROFILE_BINS = 20
#: 사다리에 올리는 칸 수 — 거래대금이 가장 많은 순서. 비율 문턱이 아니라 순위만 쓴다
PROFILE_TOP = 3


def volume_profile(dates: list[str], closes: list[float], values: list[float | None]) -> dict[str, Any] | None:
    """매물대 (45장, 25.1066) — 지난 1년 어느 가격대에서 거래가 많았나.
    가격은 수정주가, 무게는 그날 거래대금(분할이 있어도 그대로인 값). 칸은 마지막 수정주가에 대한 **비율**로 적는다 —
    일일 의견이 오늘 종가에 곱해 가격으로 옮긴다(수정주가와 원 종가의 축이 달라도 맞는다)."""
    쌍 = [(c, v) for c, v in zip(closes[-PROFILE_DAYS:], values[-PROFILE_DAYS:], strict=False)
         if c and c > 0 and v and v > 0]  # fmt: skip
    if len(쌍) < PROFILE_DAYS // 2:
        return None
    lo, hi = min(c for c, _ in 쌍), max(c for c, _ in 쌍)
    if hi <= lo:
        return None
    폭 = (hi - lo) / PROFILE_BINS
    무게 = [0.0] * PROFILE_BINS
    for c, v in 쌍:
        무게[min(PROFILE_BINS - 1, int((c - lo) / 폭))] += v
    합 = sum(무게)
    끝 = float(closes[-1] or 0)
    if 끝 <= 0:
        # 마지막 종가가 0·빈 값이면 비율을 낼 수 없다 — 이 종목만 빠진다 (25.1085). 예전엔 0 으로 나눠 **주간 지표
        # 작업 전체**가 저장 전에 죽었다(종목 반복에 종목별 예외 처리가 없다). 25.203 뒤로는 적재가 막지만 옛 0 행이
        # 남을 수 있다
        return None
    bins = [{"lo": round((lo + i * 폭) / 끝, 4), "hi": round((lo + (i + 1) * 폭) / 끝, 4), "share": round(w / 합, 4)}
            for i, w in enumerate(무게)]  # fmt: skip
    순 = sorted(range(PROFILE_BINS), key=lambda i: -무게[i])[:PROFILE_TOP]
    return {"since": str(dates[-len(closes[-PROFILE_DAYS:])]), "until": str(dates[-1]), "days": len(쌍),
            "bins": bins, "top": 순, "now": min(PROFILE_BINS - 1, int((끝 - lo) / 폭))}  # fmt: skip


#: 움직임 분해 (44장) — 지난 20·60거래일
MOVE_DAYS = (20, 60)
#: 업종 몫을 낼 최소 같은 업종 종목 수(나 빼고) — 이보다 적으면 업종 평균이 한두 종목의 움직임이다
MOVE_MIN_PEERS = 3
#: 베타를 재는 최근 거래일 — 1년
MOVE_BETA_DAYS = 252


def move_inputs(dates: list[str], closes: list[float], index: dict[str, float]) -> dict[str, Any] | None:
    """움직임 분해의 재료 (44장, 25.1065) — 종목 하나의 창별 수익·지수 수익·1년 베타.
    업종 몫은 모든 종목을 모은 뒤 낸다."""
    쌍 = [(d, c) for d, c in zip(dates, closes, strict=False) if c and c > 0 and index.get(str(d))]
    if len(쌍) < max(MOVE_DAYS) + 1:
        return None
    out: dict[str, Any] = {"until": 쌍[-1][0], "w": {}, "last": 쌍[-1][1]}
    for n in MOVE_DAYS:
        (d0, c0), (d1, c1) = 쌍[-1 - n], 쌍[-1]
        out["w"][str(n)] = {"since": d0, "stock": c1 / c0 - 1, "market": index[d1] / index[d0] - 1}
    끝 = 쌍[-(MOVE_BETA_DAYS + 1) :]
    xs = [index[b] / index[a] - 1 for (a, _), (b, _) in zip(끝, 끝[1:], strict=False)]
    ys = [c1 / c0 - 1 for (_, c0), (_, c1) in zip(끝, 끝[1:], strict=False)]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    out["beta"] = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / var if var > 0 else None
    return out


def move_parts(mine: dict, peers: list[dict]) -> dict[str, Any] | None:
    """움직임 분해 (44장) — 창마다 종목 수익 = 시장 몫(베타 × 지수) + 업종 몫(같은 업종 평균 − 지수)
    + 이 종목만의 몫(나머지). peers = 같은 업종 다른 종목의 `move_inputs`.
    업종 종목이 `MOVE_MIN_PEERS` 미만이면 업종 몫 없이 나머지를 이 종목 몫으로."""
    if not mine or mine.get("beta") is None:
        return None
    out: dict[str, Any] = {"until": mine["until"], "beta": round(mine["beta"], 3), "w": {}}
    for n, x in mine["w"].items():
        시장 = mine["beta"] * x["market"]
        같은 = [p["w"][n]["stock"] for p in peers if n in (p.get("w") or {}) and p["w"][n]["since"] == x["since"]]
        업종 = (sum(같은) / len(같은) - x["market"]) if len(같은) >= MOVE_MIN_PEERS else None
        out["w"][n] = {"since": x["since"], "stock": round(x["stock"], 4), "market": round(시장, 4),
                       "sector": round(업종, 4) if 업종 is not None else None, "peers": len(같은),
                       "own": round(x["stock"] - 시장 - (업종 or 0.0), 4)}  # fmt: skip
    return out


def move_lines(m: dict | None) -> list[str]:
    """움직임 분해 진단 줄 (44장) — 창마다 한 줄. 업종 대형주(48장)가 있으면 한 줄 더."""
    줄 = []
    ld = (m or {}).get("leaders") or {}
    for n, x in (ld.get("w") or {}).items():
        나 = " (이 종목도 대형주)" if ld.get("self_leader") else ""
        줄.append(f"업종 대형주(시총 상위 {ld['n']}/{ld['of']}종목, 지난 {n}거래일): 평균 {_p(x['leaders'])} · "
                  f"이 종목 {_p(x['stock'])}{나}")  # fmt: skip
    for n, x in ((m or {}).get("w") or {}).items():
        업종 = f" + 업종 {_p(x['sector'])}(같은 업종 {x['peers']}종목)" if x.get("sector") is not None else ""
        줄.append(f"움직임 분해(지난 {n}거래일, {x['since']}~{m['until']}): 종목 {_p(x['stock'])} = "
                  f"시장 {_p(x['market'])}(베타 {m['beta']:.2f}){업종} + 이 종목만 {_p(x['own'])}")  # fmt: skip
    return 줄


#: 업종 대형주 (48장, 15회차 S5) — 같은 업종 시가총액 상위 이 몫. Hou(2007)의 "큰 회사가 먼저 움직인다" 를 사실 줄로만
LEADER_SHARE = 0.3


def leaders(sid: int, mine: dict | None, peers: dict[int, dict], caps: dict[int, float]) -> dict[str, Any] | None:
    """업종 대형주의 움직임 (48장, 25.1069) — 같은 업종에서 시총 상위 `LEADER_SHARE`(나 포함, 올림) 종목의
    창별 평균 수익과 이 종목 수익. 업종 종목(나 포함)이 `MOVE_MIN_PEERS` + 1 미만이면 None.
    이 종목이 그 대형주에 들면 `self_leader`.
    예측이라 부르지 않는다 — 두 검증 모두 IC 로 잴 수 없다고 봤다(docs/factors.md 12.17)."""
    if not mine:
        return None
    무리 = {k: v for k, v in peers.items() if v and k in caps} | ({sid: mine} if sid in caps else {})
    if len(무리) < MOVE_MIN_PEERS + 1:
        return None
    n = max(1, math.ceil(len(무리) * LEADER_SHARE))
    큰 = sorted(무리, key=lambda k: -caps[k])[:n]
    out: dict[str, Any] = {"n": n, "of": len(무리), "self_leader": sid in 큰, "w": {}}
    for w, x in mine["w"].items():
        같은 = [무리[k]["w"][w]["stock"] for k in 큰 if w in 무리[k]["w"] and 무리[k]["w"][w]["since"] == x["since"]]
        if 같은:
            out["w"][w] = {"leaders": round(sum(같은) / len(같은), 4), "stock": round(x["stock"], 4)}
    return out if out["w"] else None



#: 변동성 국면 (51장) — 최근 한 달(21거래일) 일간 수익의 연환산 표준편차를 자기 이력에서 셋으로 나눈다
VOL_WINDOW = 21
#: 변동성 국면 뒤를 재는 기간(개월) — 13장과 같은 거래일 환산
VOL_REGIME_MONTHS = (1, 3)


def vol_regime(dates: list[str], closes: list[float]) -> dict[str, Any] | None:
    """변동성 국면 성적 (51장, 25.1071) — 날마다 최근 `VOL_WINDOW` 거래일 변동성(연환산)을 내고, 자기 이력에서 삼분위로
    나눈 칸마다 그 뒤 1·3개월 수익 분포. 지금 변동성이 자기 이력 몇 % 분위인지와 지금 칸. 칸 표본은 13장과 같은 하한."""
    c = [float(x) for x in closes if x and x > 0]
    if len(c) < MIN_DAYS + VOL_WINDOW:
        return None
    r = [math.log(b / a) for a, b in zip(c, c[1:], strict=False)]
    vol: list[tuple[int, float]] = []  # (종가 자리, 변동성)
    for i in range(VOL_WINDOW, len(r) + 1):
        창 = r[i - VOL_WINDOW : i]
        m = sum(창) / VOL_WINDOW
        vol.append((i, math.sqrt(sum((x - m) ** 2 for x in 창) / (VOL_WINDOW - 1) * 252)))
    값 = sorted(v for _, v in vol)
    a, b = statistics.quantiles(값, n=3)
    def 칸(v: float) -> int:
        return 0 if v <= a else 1 if v <= b else 2

    모음: dict[int, dict[str, list[float]]] = {k: {str(m): [] for m in VOL_REGIME_MONTHS} for k in range(3)}
    for i, v in vol:
        for m in VOL_REGIME_MONTHS:
            d = pt.HORIZON_DAYS[m]
            if i + d < len(c):
                모음[칸(v)][str(m)].append(c[i + d] / c[i] - 1)
    지금 = vol[-1][1]
    return {"since": str(dates[-len(c)]), "until": str(dates[-1]), "now": round(지금, 4),
            "pct": round(sum(1 for x in 값 if x <= 지금) / len(값), 4), "edges": [round(a, 4), round(b, 4)],
            "bucket": 칸(지금),
            "buckets": {str(k): {m: pt.dist(v) if len(v) >= pt.MIN_DAYS else None for m, v in 기간.items()}
                        for k, 기간 in 모음.items()}}  # fmt: skip


VOL_LABEL = ("조용한 편(하위 1/3)", "보통(가운데 1/3)", "시끄러운 편(상위 1/3)")


def vol_regime_line(v: dict | None) -> str | None:
    if not v:
        return None
    지금 = (v.get("buckets") or {}).get(str(v["bucket"])) or {}
    한달 = 지금.get("1")
    뒤 = f" — 이 칸에서 1개월 뒤 중앙값 {_p(한달['median'])}·오른 비율 {한달['up'] * 100:.0f}%" if 한달 else ""
    return (f"변동성 국면: 지금 한 달 변동성 {v['now'] * 100:.0f}%(자기 이력 {v['pct'] * 100:.0f}% 분위, "
            f"{VOL_LABEL[v['bucket']]}){뒤}")


#: 함께 움직이는 종목 (52장) — 최근 1년(252거래일), 시장 몫을 뺀 일간 수익의 상관
COMOVE_DAYS = 252
#: 상관을 낼 최소 관측일 — 그보다 적으면 상관이 잡음이다
COMOVE_MIN_DAYS = 200
#: 종목마다 남기는 짝 수
COMOVE_TOP = 5


def comovers(series: dict[int, dict[str, float]], market: dict[str, float],
             sector_of: dict[int, Any], top: int = COMOVE_TOP,
             issuer_of: dict[int, str] | None = None) -> dict[int, list[dict]]:  # fmt: skip
    """함께 움직이는 종목 (52장, 25.1071). series = 종목 → {날짜: 일간 로그 수익},
    market = {날짜: 지수 일간 로그 수익}. 종목마다 베타 × 지수를 빼 잔차를 내고(업종·테마처럼 시장 말고 함께 움직이는
    것만 남게), 잔차끼리 상관이 큰 `COMOVE_TOP` 개. 관측일이 `COMOVE_MIN_DAYS` 미만인 종목은 뺀다.
    빈 날은 표준화 뒤 0(정보 없음)으로 둔다. 같은 업종인지 함께 적는다."""
    import numpy as np

    날 = sorted(market)[-COMOVE_DAYS:]
    if len(날) < COMOVE_MIN_DAYS:
        return {}
    m = np.asarray([market[d] for d in 날])
    ids, 행 = [], []
    for sid, by in series.items():
        x = np.asarray([by.get(d, np.nan) for d in 날], dtype=float)
        ok = ~np.isnan(x)
        if ok.sum() < COMOVE_MIN_DAYS:
            continue
        mv = m[ok] - m[ok].mean()
        var = float((mv * mv).sum())
        beta = float(((x[ok] - x[ok].mean()) * mv).sum() / var) if var > 0 else 0.0
        e = np.where(ok, x - beta * m, np.nan)
        e = e - np.nanmean(e)
        sd = float(np.nanstd(e))
        if sd <= 0:
            continue
        ids.append(sid)
        행.append(np.nan_to_num(e / sd, nan=0.0))
    if len(ids) < top + 1:
        return {}
    z = np.asarray(행, dtype=np.float32)
    corr = (z @ z.T) / len(날)
    np.fill_diagonal(corr, -np.inf)
    # 같은 발행사의 다른 주식(보통주·우선주, 이중 클래스)은 기계적인 짝이라 뺀다 (12.18 검증 B)
    if issuer_of:
        같은: dict[str, list[int]] = {}
        for i, sid in enumerate(ids):
            if issuer_of.get(sid):
                같은.setdefault(issuer_of[sid], []).append(i)
        for idx in 같은.values():
            for a in idx:
                for b in idx:
                    corr[a][b] = -np.inf
    out: dict[int, list[dict]] = {}
    n_top = top
    for i, sid in enumerate(ids):
        top_i = np.argpartition(-corr[i], n_top)[:n_top]
        top_i = top_i[np.isfinite(corr[i][top_i])]
        top = top_i[np.argsort(-corr[i][top_i])]
        out[sid] = [{"stock_id": int(ids[j]), "corr": round(float(corr[i][j]), 3),
                     "same_sector": sector_of.get(sid) is not None and sector_of.get(sid) == sector_of.get(ids[j])}
                    for j in top]  # fmt: skip
    return out
