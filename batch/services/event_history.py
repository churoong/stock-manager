"""재무·수급 사건과 그 뒤 주가 — 실적 발표 반응·상승의 출처·공매도 급증 뒤 (docs/analysis.md 40~42장).

주간 성과 지표 작업이 종목마다 이미 읽은 5년 수정주가에,
나라마다 한 번 몰아 읽은 재무(`financials`)·배당(`stock_dividends`)·
수급(`kr_flows`) 이력을 맞춘다. 국내만(재무·수급 이력이 국내에만 있다). 문턱으로 판정하지 않는다 — 사건과 숫자만.
계산만 한다 — DB 도 시각도 모른다.
"""

from __future__ import annotations

import bisect
import math
import statistics
from typing import Any

from batch.services import patterns as pt
from batch.sources import dart

#: 실적 발표 반응을 재는 거래일 — 발표 전날 종가에서 1·5거래일 뒤 (40장)
REACTION_DAYS = (1, 5)
#: 화면에 싣는 최근 발표 수
REACTION_SHOW = 8
#: 상승의 출처를 재는 햇수 — 이 해 전 사업보고서에서 최근 사업보고서까지 (41장). 짧으면 한 해의 이익 출렁임이 전부다
SOURCE_YEARS = 3
#: 공매도 급증 (42장) — 그날 공매도 비중이 앞 `SURGE_BASE` 거래일 중앙값의 `SURGE_X` 배 이상.
#: 2배는 "평소의 두 배" 라는 표시 기준이다 [확인필요: 근거 문헌 없음 — 사건 수와 그 뒤 분포를 함께 보이므로 판정에
#: 쓰지 않는다]
SURGE_BASE = 20
SURGE_X = 2.0
#: 급증 사건 뒤 같은 사건으로 보는 거래일 — 이어지는 급증을 여러 번 세지 않게
SURGE_COOLDOWN = 5
#: 공매도 급증 뒤를 재는 거래일
SURGE_DAYS = (5, 20)


def _기준_하나(rows: list[dict]) -> list[dict]:
    """재무 행에서 기준 하나 — 가장 최근 보고서의 연결(있으면)·별도. 같은 (연도, 보고서)는 가장 늦게 접수된 것."""
    if not rows:
        return []
    최근 = max(rows, key=lambda r: (str(r.get("report_date") or ""), int(r.get("consolidated") or 0)))
    연결 = int(최근.get("consolidated") or 0)
    고름: dict[tuple[int, str], dict] = {}
    for r in rows:
        if int(r.get("consolidated") or 0) != 연결:
            continue
        k = (int(r["fiscal_year"]), str(r["report_code"]))
        if k not in 고름 or str(r.get("report_date") or "") > str(고름[k].get("report_date") or ""):
            고름[k] = r
    return sorted(고름.values(), key=lambda r: str(r.get("report_date") or ""))


def _앞날(dates: list[str], d: str) -> int | None:
    """`d` 보다 앞선 마지막 거래일의 자리. 없으면 None."""
    i = bisect.bisect_left(dates, d) - 1
    return i if i >= 0 else None


def earnings_reactions(fin: list[dict], dates: list[str], closes: list[float],
                       index: dict[str, float]) -> dict[str, Any] | None:  # fmt: skip
    """실적 발표 반응 이력 (40장, 25.1063). 보고서(분기·반기·사업)마다 접수일 전날 종가 → 1·5거래일 뒤 종목·지수 수익,
    그리고 전년 같은 보고서 대비 영업이익 증가율. 요약: 증가율이 + 였던 발표와 − 였던 발표의 평균 초과 수익."""
    if len(dates) < 30:
        return None
    행 = _기준_하나(fin)
    앞해 = {(int(r["fiscal_year"]), str(r["report_code"])): r for r in 행}
    사건 = []
    for r in 행:
        d = str(r.get("report_date") or "")
        i = _앞날(dates, d)
        if i is None or i + max(REACTION_DAYS) >= len(dates) or closes[i] <= 0:
            continue
        p = 앞해.get((int(r["fiscal_year"]) - 1, str(r["report_code"])))
        oi, oi0 = r.get("operating_income"), (p or {}).get("operating_income")
        yoy = (oi / oi0 - 1) if isinstance(oi, (int, float)) and isinstance(oi0, (int, float)) and oi0 > 0 else None
        칸: dict[str, Any] = {"date": d, "fiscal_year": int(r["fiscal_year"]), "report_code": str(r["report_code"]),
                             "yoy": round(yoy, 4) if yoy is not None else None}  # fmt: skip
        for n in REACTION_DAYS:
            s = closes[i + n] / closes[i] - 1
            a, b = index.get(dates[i]), index.get(dates[i + n])
            칸[f"r{n}"] = round(s, 4)
            칸[f"x{n}"] = round(s - (b / a - 1), 4) if a and b else None
        사건.append(칸)
    if not 사건:
        return None
    n5 = f"x{max(REACTION_DAYS)}"

    def 평균(xs: list[dict]) -> dict | None:
        v = [x[n5] for x in xs if x.get(n5) is not None]
        return {"n": len(v), "avg": round(sum(v) / len(v), 4), "up": sum(1 for x in v if x > 0)} if v else None

    return {"basis": "연결" if int((행[-1]).get("consolidated") or 0) else "별도", "events": len(사건),
            "recent": 사건[-REACTION_SHOW:][::-1],
            "grew": 평균([x for x in 사건 if x["yoy"] is not None and x["yoy"] > 0]),
            "shrank": 평균([x for x in 사건 if x["yoy"] is not None and x["yoy"] <= 0])}  # fmt: skip


def return_sources(fin: list[dict], divs: list[dict], dates: list[str], closes: list[float],
                   shares: float | None) -> dict[str, Any] | None:  # fmt: skip
    """상승의 출처 (41장, 25.1064) — `SOURCE_YEARS` 년 전 사업보고서 접수일부터 지금까지 주가 변화를
    이익(순이익) 성장 × 배수(PER) 변화로 나누고, 그 사이 현금배당을 따로 더한다.

    1 + 주가 변화 = (1 + 이익 변화) × (1 + 배수 변화) — 주식 수가 그대로라고 본다
    (유상증자·자사주 소각이 있으면 이익 변화가 주당 이익 변화와 어긋난다).
    배당 몫 = 그 사이 회계연도 현금배당 합 ÷ (시작 주가 × 지금 상장주식수). 순이익이 두 끝 모두 + 일 때만."""
    연간 = [r for r in _기준_하나(fin) if str(r["report_code"]) == dart.ANNUAL_REPORT_CODE]
    if not 연간 or not dates:
        return None
    끝 = 연간[-1]
    시작 = next((r for r in 연간 if int(r["fiscal_year"]) == int(끝["fiscal_year"]) - SOURCE_YEARS), None)
    if not 시작:
        return None
    ni0, ni1 = 시작.get("net_income"), 끝.get("net_income")
    if not (isinstance(ni0, (int, float)) and isinstance(ni1, (int, float)) and ni0 > 0 and ni1 > 0):
        return None
    i = bisect.bisect_left(dates, str(시작.get("report_date") or ""))
    if i >= len(dates) - 1 or closes[i] <= 0 or closes[-1] <= 0:
        return None
    주가 = closes[-1] / closes[i] - 1
    이익 = ni1 / ni0 - 1
    배수 = (1 + 주가) / (1 + 이익) - 1
    배당 = None
    해들 = range(int(시작["fiscal_year"]) + 1, int(끝["fiscal_year"]) + 1)
    현금 = [d.get("cash_dividend_total") for d in divs if int(d.get("fiscal_year") or 0) in 해들]
    if shares and shares > 0 and 현금 and all(isinstance(x, (int, float)) for x in 현금):
        배당 = sum(현금) / (closes[i] * shares)
    return {"since": dates[i], "until": dates[-1], "from_year": int(시작["fiscal_year"]),
            "to_year": int(끝["fiscal_year"]),
            "price": round(주가, 4), "earnings": round(이익, 4), "multiple": round(배수, 4),
            "dividends": round(배당, 4) if 배당 is not None else None,
            "earnings_share": round(math.log1p(이익) / math.log1p(주가), 4) if abs(주가) > 1e-9 else None}  # fmt: skip


def short_surges(flows: list[dict], dates: list[str], closes: list[float]) -> dict[str, Any] | None:
    """공매도 급증 뒤 (42장, 25.1065). flows = 한 종목의 {date, short_vol_pct} (날짜 오름차순 아니어도 된다).
    사건 = 공매도 비중이 앞 `SURGE_BASE` 거래일 중앙값의 `SURGE_X` 배 이상(중앙값 > 0),
    앞 사건 뒤 `SURGE_COOLDOWN` 거래일
    안은 같은 사건. 사건 날 종가에서 5·20거래일 뒤 수익 분포와, 수급이 있는 모든 날의 같은 분포(평소)."""
    줄 = sorted((str(f["date"]), float(f["short_vol_pct"])) for f in flows
               if isinstance(f.get("short_vol_pct"), (int, float)))  # fmt: skip
    if len(줄) <= SURGE_BASE:
        return None
    자리 = {d: i for i, d in enumerate(dates)}
    사건: list[str] = []
    마지막 = -10**9
    for k in range(SURGE_BASE, len(줄)):
        med = statistics.median(v for _, v in 줄[k - SURGE_BASE : k])
        if med > 0 and 줄[k][1] >= SURGE_X * med and k - 마지막 > SURGE_COOLDOWN:
            사건.append(줄[k][0])
            마지막 = k
    out: dict[str, Any] = {"since": 줄[0][0], "until": 줄[-1][0], "days": len(줄), "events": len(사건),
                           "last": 사건[-1] if 사건 else None, "h": {}, "base": {}}  # fmt: skip
    for n in SURGE_DAYS:
        def 뒤(ds: list[str], n: int = n) -> list[float]:
            return [closes[자리[d] + n] / closes[자리[d]] - 1 for d in ds
                    if d in 자리 and 자리[d] + n < len(closes) and closes[자리[d]] > 0]  # fmt: skip

        out["h"][str(n)] = pt.dist(뒤(사건))
        out["base"][str(n)] = pt.dist(뒤([d for d, _ in 줄]))
    return out


def _p(x: float) -> str:
    return f"{x * 100:+.1f}%"


REPORT_NAME = {"11013": "1분기", "11012": "반기", "11014": "3분기", dart.ANNUAL_REPORT_CODE: "사업"}


def lines(h: dict | None) -> list[str]:
    """진단 줄 (40~42장). 있는 것만."""
    h = h or {}
    줄: list[str] = []
    e = h.get("earnings") or {}
    if e.get("events"):
        조각 = [f"{이름} {x['n']}번 평균 {_p(x['avg'])}(시장 대비, {max(REACTION_DAYS)}거래일)"
              for 이름, x in (("영업이익이 늘어난 발표", e.get("grew")), ("줄어든 발표", e.get("shrank")))
              if x]  # fmt: skip
        if 조각:
            줄.append(f"실적 발표 반응({e['basis']}, {e['events']}번): " + " · ".join(조각))
    s = h.get("sources") or {}
    if s.get("price") is not None:
        배당 = f" · 그 사이 배당 {_p(s['dividends'])}" if s.get("dividends") is not None else ""
        줄.append(f"상승의 출처({s['from_year']}→{s['to_year']} 사업보고서, {s['since']}~): 주가 {_p(s['price'])} = "
                  f"순이익 {_p(s['earnings'])} × 배수(PER) {_p(s['multiple'])}{배당}")  # fmt: skip
    q = h.get("short") or {}
    a = (q.get("h") or {}).get(str(SURGE_DAYS[0]))
    if q.get("events") and a:
        b = (q.get("base") or {}).get(str(SURGE_DAYS[0])) or {}
        평소 = f" (평소 {_p(b['median'])}·{b['up'] * 100:.0f}%)" if b else ""
        줄.append(f"공매도 급증 뒤({q['since']}~, {q['events']}번): {SURGE_DAYS[0]}거래일 뒤 중앙값 {_p(a['median'])}·"
                  f"오른 비율 {a['up'] * 100:.0f}%{평소}")  # fmt: skip
    return 줄
