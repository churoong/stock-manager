"""종목 분석 — 결론 한 줄·근거·반대 목소리 (docs/analysis.md, docs/infra.md 25.1016).

새 분석을 만들지 않는다. 이미 있는 규칙의 결과(신호·판정표·매도 플래그·보유·엇갈림·공시 반응)를 한 종목에 모아 말한다.
**새 문턱이 없다** — 결론은 규칙이 낸 사실(신호가 났나, 보유인가, 플래그가 걸렸나)로만 정한다(docs/analysis.md 2장).
계산만 한다. DB 도 시각도 모른다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from batch.services import insights, signals

#: 결론 키 → 이름 (위에서부터 처음 맞는 것, docs/analysis.md 3장)
VERDICTS = {
    "check_holding": "보유 점검",
    "consider_buy": "매수 검토",
    "hold": "보유 유지",
    "reference": "참고 분석",
    # 예전 이름 "신호 대기" — 2026-10-08 사용자 "신호대기하지 말고 그냥 그대로 분석을 해" (docs/analysis.md 10.2)
    "waiting": "종합 분석",
    "undecided": "판단 보류",
}
HORIZON = {"short": "단기", "mid": "중기", "long": "장기"}
HORIZON_ORDER = ("short", "mid", "long")
FACTOR = {"value": "밸류", "quality": "퀄리티", "growth": "성장", "momentum": "모멘텀", "risk": "리스크"}
LEVEL = {"red": "적색", "yellow": "황색", "green": "녹색"}
#: 최근 공시로 보는 날 수 (docs/analysis.md 5장)
RECENT_DISCLOSURE_DAYS = 14
#: 참고 점수의 출처 — 유니버스 밖 종목의 점수는 `scores` 에 쓰지 않는다 (docs/analysis.md 8장, 25.1019)
REFERENCE_SOURCE = "참고 계산(유니버스 + 이 종목)"


#: 증권사 목표가를 모으는 날 수 (docs/analysis.md 9.3). 목표가는 보통 분기 실적마다 다시 낸다 — 한 분기 남짓
CONSENSUS_DAYS = 90
#: 장기 신호의 밴드 문턱 — 새 문턱이 아니라 그 값을 그대로 쓴다 (docs/signals.md 1.3)
BAND_ENTRY_PERCENTILE = signals.BAND_ENTRY_PERCENTILE


def _won(x: Any, currency: str) -> str:
    """가격 + 단위 (원·달러)."""
    return f"{_price(x, currency)}{'원' if currency == 'KRW' else '달러'}"


def _pct(x: Any, digits: int = 1, sign: bool = True) -> str:
    return f"{x * 100:{'+' if sign else ''}.{digits}f}%" if isinstance(x, (int, float)) else "-"


#: 시장 기대수익률을 재는 지수 이력의 최대 길이(년) (docs/analysis.md 10.1). 길수록 한 국면에 덜 끌린다
MARKET_YEARS = 10
#: 예상 주가를 내는 기간 (개월). 6개월은 2026-10-08 사용자 요청으로 더함 (25.1025)
FORECAST_MONTHS = (1, 3, 6, 12)
#: 범위의 z — 68%·90% (정규분포)
FORECAST_Z = {"68": 1.0, "90": 1.645}


def market_return(first: tuple[str, float] | None, last: tuple[str, float] | None) -> dict | None:
    """지수의 첫·마지막 종가로 연환산 수익률. 1년이 안 되면 None (한 국면이라 뜻이 없다)."""
    if not first or not last or first[1] <= 0 or last[1] <= 0:
        return None
    년 = (date.fromisoformat(last[0][:10]) - date.fromisoformat(first[0][:10])).days / 365.25
    if 년 < 1:
        return None
    return {"annual": (last[1] / first[1]) ** (1 / 년) - 1, "years": 년, "since": first[0][:10], "until": last[0][:10]}


#: 확률에서 말하는 하락 폭 — "1년 뒤 −20% 이하일 확률". 약세장의 흔한 정의(고점 대비 −20%)를 빌렸다 —
#: 판정 문턱이 아니라 표시 기준
DROP_LEVEL = -0.20
#: 가격 사다리의 도달 확률 기간 (개월)
TOUCH_MONTHS = (3, 12)


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _drift(er: float, sigma: float) -> float:
    """로그 가격의 연 기대 변화 ν = ln(1 + E[r]) − σ²/2 (10.1 의 범위와 같은 가정)."""
    return math.log(1 + er) - sigma * sigma / 2


def prob_above(ratio: float, er: float, sigma: float, t: float) -> float:
    """t 년 뒤 가격 ÷ 지금 > ratio 일 확률 (로그정규, docs/analysis.md 12.1)."""
    nu = _drift(er, sigma)
    return 1 - _phi((math.log(ratio) - nu * t) / (sigma * math.sqrt(t)))


def touch_prob(ratio: float, er: float, sigma: float, t: float) -> float:
    """t 년 **안에** 한 번이라도 가격 ÷ 지금 = ratio 에 닿을 확률 — 표류 있는 브라운 운동의 첫 도달
    (docs/analysis.md 12.2).

    위 장벽(ratio > 1): Φ((−b + νt)/σ√t) + e^{2νb/σ²}·Φ((−b − νt)/σ√t),  b = ln ratio
    아래 장벽(ratio < 1): Φ((b − νt)/σ√t) + e^{2νb/σ²}·Φ((b + νt)/σ√t).
    연속으로 지켜본다는 가정이라 종가로만 보는 실제보다 조금 크게 나온다."""
    if ratio <= 0:
        return 0.0
    b = math.log(ratio)
    if b == 0:
        return 1.0
    nu, s = _drift(er, sigma), sigma * math.sqrt(t)
    if b > 0:
        a, c = (-b + nu * t) / s, (-b - nu * t) / s
    else:
        a, c = (b - nu * t) / s, (b + nu * t) / s
    # 두 번째 항은 로그로 곱한다 — e^{2νb/σ²} 가 넘치는 자리(σ 가 아주 작음)에서 Φ(c) 는 0 에 가깝다.
    # 예전에는 넘치면 1 로 두어 도달 확률이 100% 가 됐다 (25.1042, 교차검증 감사)
    로그 = 2 * nu * b / (sigma * sigma) + _log_phi(c)
    뒤 = 0.0 if 로그 < -745 else math.exp(min(로그, 0.0))
    return max(0.0, min(1.0, _phi(a) + 뒤))


def _log_phi(x: float) -> float:
    """ln Φ(x). 아주 작은 x 에서는 꼬리 근사 −x²/2 − ln(−x) − ½ln(2π) (Φ 가 0 으로 내려앉기 전에)."""
    if x > -30:
        p = _phi(x)
        return math.log(p) if p > 0 else -745.0
    return -x * x / 2 - math.log(-x) - 0.5 * math.log(2 * math.pi)


def race_prob(up_ratio: float, down_ratio: float, er: float, sigma: float) -> float | None:
    """위(목표가)가 아래(손절가)보다 **먼저** 닿을 확률 — 기간 제한 없이 (docs/analysis.md 12.3).

    척도 함수 s(x) = e^{−2νx/σ²} 로 P = (1 − e^{2νd/σ²}) / (e^{−2νu/σ²} − e^{2νd/σ²}),  u = ln 위, d = −ln 아래.
    표류가 0 이면 d/(u+d) — 거리의 비만으로 정해진다."""
    if up_ratio <= 1 or not 0 < down_ratio < 1:
        return None
    u, d = math.log(up_ratio), -math.log(down_ratio)
    k = 2 * _drift(er, sigma) / (sigma * sigma)
    if abs(k) < 1e-9:
        return d / (u + d)
    try:
        return max(0.0, min(1.0, (1 - math.exp(k * d)) / (math.exp(-k * u) - math.exp(k * d))))
    except (OverflowError, ZeroDivisionError):
        return None


def forecast(*, close: float | None, beta: float | None, sigma: float | None, market: dict | None,
             rf: float | None) -> dict | None:  # fmt: skip
    """1·3·6·12개월 예상 주가와 범위 (docs/analysis.md 10.1). CAPM 기대수익 + 로그정규 범위. 재료가 모자라면 None."""
    if close is None or close <= 0 or not market:
        return None
    rf0 = rf if isinstance(rf, (int, float)) else 0.0
    b = beta if isinstance(beta, (int, float)) else 1.0
    er = rf0 + b * (market["annual"] - rf0)
    if er <= -1:
        return None
    s = sigma if isinstance(sigma, (int, float)) and sigma > 0 else None
    기간 = []
    for 달 in FORECAST_MONTHS:
        t = 달 / 12
        행: dict[str, Any] = {"months": 달, "expected": close * (1 + er) ** t}
        if s is not None:
            중심 = math.log(1 + er) - s * s / 2
            for 이름, z in FORECAST_Z.items():
                행[f"low{이름}"] = close * math.exp(중심 * t - z * s * math.sqrt(t))
                행[f"high{이름}"] = close * math.exp(중심 * t + z * s * math.sqrt(t))
            # 확률로 말하기 (docs/analysis.md 12.1, 25.1038) — 같은 로그정규 가정
            행["p_up"] = prob_above(1.0, er, s, t)
            행["p_drop"] = 1 - prob_above(1 + DROP_LEVEL, er, s, t)
        기간.append(행)
    return {"horizons": 기간, "er": er, "beta": b, "beta_given": isinstance(beta, (int, float)), "sigma": s,
            "rf": rf0, "rf_given": isinstance(rf, (int, float)), "market": market}  # fmt: skip


def consensus(opinions: list[dict], today: date) -> dict | None:
    """증권사마다 가장 최근 목표가 하나 (지난 `CONSENSUS_DAYS` 일). 없으면 None."""
    since = (today - timedelta(days=CONSENSUS_DAYS)).isoformat()
    최근: dict[str, dict] = {}
    for o in opinions:
        d, t = str(o.get("date") or ""), o.get("target_price")
        if d < since or not isinstance(t, (int, float)) or t <= 0:
            continue
        b = str(o.get("broker"))
        if b not in 최근 or d > str(최근[b]["date"]):
            최근[b] = {"date": d, "target": float(t)}
    if not 최근:
        return None
    값 = sorted(x["target"] for x in 최근.values())
    n = len(값)
    중앙 = 값[n // 2] if n % 2 else (값[n // 2 - 1] + 값[n // 2]) / 2
    return {"brokers": n, "median": 중앙, "low": 값[0], "high": 값[-1], "since": since,
            "latest": max(x["date"] for x in 최근.values())}  # fmt: skip


def outlook(*, close: float | None, close_date: str | None, currency: str, momentum: dict | None,
            risk: dict | None, band: dict | None, opinions: list[dict], today: date,
            band_note: str | None = None, market: dict | None = None, rf: float | None = None,
            analog: dict | None = None, fundamentals: dict | None = None) -> dict:  # fmt: skip
    """가격·가치 진단 (docs/analysis.md 9장). 예측이 아니라 근거 있는 기준점 — 모든 값은 DB 행에서 온다.

    momentum: momentum_3m·momentum_6m·momentum_12_1·high_52w_proximity·as_of (factors.raw_json)
    risk: volatility_ann·mdd·window·as_of_date (performance_metrics)
    band: p20·p50·p80·current_value·band_rank·band_close·price_date (valuation_bands, band_close 는 그날 분할 반영 종가)
    """
    줄: list[str] = []
    evidence: list[dict] = []
    m = momentum or {}
    out: dict[str, Any] = {"close": close, "close_date": close_date}
    # 9.1 현재 주가 위치
    if close is not None:
        조각 = [f"종가 {_won(close, currency)}({close_date})"]
        evidence.append(_row("종가", _price(close, currency), "—", "prices", close_date))
        for k, 이름 in (("momentum_3m", "3개월"), ("momentum_6m", "6개월"), ("momentum_12_1", "12-1개월")):
            if isinstance(m.get(k), (int, float)):
                조각.append(f"{이름} {_pct(m[k])}")
                evidence.append(_row(f"{이름} 수익률", _pct(m[k]), "—", "factors.momentum", m.get("as_of")))
        if isinstance(m.get("high_52w_proximity"), (int, float)):
            조각.append(f"52주 고점의 {m['high_52w_proximity'] * 100:.0f}%")
            evidence.append(_row("52주 고점 대비", f"{m['high_52w_proximity'] * 100:.0f}%", "—", "factors.momentum",
                                 m.get("as_of")))  # fmt: skip
        r = risk or {}
        if isinstance(r.get("volatility_ann"), (int, float)):
            조각.append(f"변동성 {_pct(r['volatility_ann'], 0, False)}·최대 낙폭 {_pct(r.get('mdd'), 0)}"
                        f"({r.get('window') or '-'})")  # fmt: skip
            evidence.append(_row(f"변동성·최대 낙폭({r.get('window') or '-'})",
                                 f"{_pct(r['volatility_ann'], 1, False)} · {_pct(r.get('mdd'), 1)}", "—",
                                 "performance_metrics", r.get("as_of_date")))  # fmt: skip
        줄.append(" · ".join(조각))
        out["momentum"] = {k: m.get(k) for k in ("momentum_3m", "momentum_6m", "momentum_12_1", "high_52w_proximity")}
        out["risk"] = None if not risk else {k: r.get(k) for k in ("volatility_ann", "mdd", "window")}
    # 9.2 가치 기준 가격
    b = band or {}
    cur, bc = b.get("current_value"), b.get("band_close")
    if isinstance(cur, (int, float)) and cur > 0 and isinstance(bc, (int, float)) and bc > 0 and b.get("p50"):
        bps = bc / cur
        가격 = {k: (float(b[k]) * bps if isinstance(b.get(k), (int, float)) else None) for k in ("p20", "p50", "p80")}
        순위 = b.get("band_rank")
        문턱 = (f"(장기 신호 문턱 {BAND_ENTRY_PERCENTILE}% 이하)"
                if isinstance(순위, (int, float)) and 순위 <= BAND_ENTRY_PERCENTILE else "")  # fmt: skip
        위치 = f"자기 3년 PBR 밴드의 {순위:.0f}% 지점{문턱} — " if isinstance(순위, (int, float)) else ""
        줄.append(f"{위치}PBR {cur:.2f}배. 밴드 기준 가격: 20% {_won(가격['p20'], currency)} · "
                  f"중앙값 {_won(가격['p50'], currency)} · 80% {_won(가격['p80'], currency)}"
                  " (자본·주식 수가 그대로일 때 PBR 이 그 분위면 — 예측이 아님)")  # fmt: skip
        evidence.append(_row("PBR 밴드 위치", f"{순위:.0f}% 지점" if isinstance(순위, (int, float)) else "-",
                             f"장기 신호 ≤ {BAND_ENTRY_PERCENTILE}%", "valuation_bands",
                             b.get("price_date")))  # fmt: skip
        evidence.append(_row("밴드 중앙값 기준 가격", _price(가격["p50"], currency), "PBR p50 × 주당순자산",
                             "valuation_bands", b.get("price_date")))  # fmt: skip
        out["band"] = {"rank": 순위, "pbr": cur, "prices": 가격, "price_date": b.get("price_date")}
    elif band_note:
        줄.append(f"가치 밴드는 내지 못했습니다 — {band_note}")
    # 9.3 증권사 목표가
    c = consensus(opinions, today)
    if c:
        괴리 = (c["median"] / close - 1) if close else None
        범위 = "" if c["brokers"] == 1 else f"(범위 {_won(c['low'], currency)}~{_won(c['high'], currency)})"
        줄.append(f"증권사 {c['brokers']}곳 목표가 중앙값 {_won(c['median'], currency)}{범위}"
                  + (f" — 종가 대비 {_pct(괴리)}" if 괴리 is not None else "") + " (증권사의 예측)")  # fmt: skip
        evidence.append(_row(f"증권사 목표가 중앙값({c['brokers']}곳)", _price(c["median"], currency),
                             f"최근 {CONSENSUS_DAYS}일 · 증권사마다 최신 1건", "kr_opinions", c["latest"]))  # fmt: skip
        out["consensus"] = {**c, "upside": 괴리}
    # 10. 예상 주가 — CAPM 기대수익 + 변동성 범위
    f = forecast(close=close, beta=(risk or {}).get("beta"), sigma=(risk or {}).get("volatility_ann"), market=market,
                 rf=rf)  # fmt: skip
    if f:
        조각 = []
        for h in f["horizons"]:
            이름 = "1년" if h["months"] == 12 else f"{h['months']}개월"
            범위 = (f"(68% {_won(h['low68'], currency)}~{_won(h['high68'], currency)})" if "low68" in h else "")
            조각.append(f"{이름} {_won(h['expected'], currency)}{범위}")
        줄.append("예상 주가: " + " · ".join(조각) + f" — 기대 연수익 {_pct(f['er'])}(CAPM, 베타 {f['beta']:.2f})")
        m2 = f["market"]
        창 = f"{m2.get('index') or '지수'} {m2['years']:.1f}년 연환산"
        evidence.append(_row("시장 기대수익률", _pct(m2["annual"]), 창, "index_prices", m2["until"]))
        evidence.append(_row("베타", f"{f['beta']:.2f}" + ("" if f["beta_given"] else " (없음 — 1 로 둠)"), "—",
                             "performance_metrics", (risk or {}).get("as_of_date")))  # fmt: skip
        무위험 = _pct(f["rf"], 2, False) + ("" if f["rf_given"] else " (설정 없음 — 0 으로 둠)")
        evidence.append(_row("무위험수익률", 무위험, "—", "settings.risk_free_manual", None))
        evidence.append(_row("기대 연수익률", _pct(f["er"]), "rf + β(E[rm] − rf)", "계산 (docs/analysis.md 10.1)",
                             close_date))  # fmt: skip
        if f["sigma"] is None:
            줄[-1] += " · 변동성이 없어 범위는 내지 못했습니다"
        out["forecast"] = f
    # 13. 비슷한 국면 — 그 종목 자신의 과거
    a = analog or {}
    if a.get("horizons"):
        삼 = next((h for h in a["horizons"] if h["months"] == 3), a["horizons"][0])
        이름 = "1년" if 삼["months"] == 12 else f"{삼['months']}개월"
        평소 = 삼.get("base") or {}
        비교 = (f" (평소 {_pct(평소['median'])}·{평소['up'] * 100:.0f}%)"
                if isinstance(평소.get("median"), (int, float)) else "")  # fmt: skip
        줄.append(f"비슷한 국면({a.get('since')}~ 자기 과거): {a['label']} — "
                  f"{a.get('days')}일(국면 {a.get('episodes')}번), "
                  f"{이름} 뒤 중앙값 {_pct(삼['median'])}·오른 비율 {삼['up'] * 100:.0f}%{비교}")  # fmt: skip
        evidence.append(_row("비슷한 국면", f"{a['label']} · {a.get('days')}일",
                             "3개월 수익률 × 52주 고점 근접, 자기 삼분위",
                             "price_patterns (주간 성과 지표 작업)", a.get("until")))  # fmt: skip
        out["analog"] = a
    elif a.get("empty"):
        줄.append(f"비슷한 국면: {a.get('label')} — 자기 과거에 이 상태가 드물어(표본 모자람) 분포를 내지 않습니다")
    # 14. 1년 시나리오 — 순자산 성장 × PBR 밴드 분위
    s = scenario(close=close, band=out.get("band"), fundamentals=fundamentals)
    if s:
        줄.append(f"1년 시나리오(순자산 {_pct(s['growth'])} 성장 × PBR 밴드): 약세 {_won(s['bear'], currency)} · "
                  f"기본 {_won(s['base'], currency)} · 강세 {_won(s['bull'], currency)} · "
                  f"PBR 그대로면 {_won(s['hold'], currency)}")  # fmt: skip
        evidence.append(_row("순자산 성장률(1년)", _pct(s["growth"]), "ROE − 배당 ÷ 자본 (클린 서플러스)",
                             "factors.quality·value", s.get("as_of")))  # fmt: skip
        evidence.append(_row("시나리오 기본 가격", _price(s["base"], currency), "PBR 밴드 중앙값 × 1년 뒤 주당순자산",
                             "계산 (docs/analysis.md 14장)", s.get("as_of")))  # fmt: skip
        out["scenario"] = s
    # 15. 역DCF — 지금 가격에 담긴 영구 이익 성장률 (할인율 = CAPM 기대수익)
    fu = fundamentals or {}
    rd = insights.reverse_dcf(ep=fu.get("ep"), discount=(out.get("forecast") or {}).get("er"))
    if rd:
        과거 = []
        if isinstance(fu.get("revenue_cagr_3y"), (int, float)):
            과거.append(f"지난 3년 매출 연평균 {_pct(fu['revenue_cagr_3y'])}")
        if isinstance(fu.get("operating_income_growth"), (int, float)):
            과거.append(f"지난해 영업이익 {_pct(fu['operating_income_growth'])}")
        rd["history"] = {k: fu.get(k) for k in ("revenue_cagr_3y", "operating_income_growth", "revenue_growth")}
        줄.append(f"역DCF: PER {rd['per']:.1f}배는 영구 이익 성장 연 {_pct(rd['implied_growth'])}을 가정한 가격"
                  f"(할인율 {_pct(rd['discount'])})" + (" — 실제는 " + " · ".join(과거) if 과거 else ""))  # fmt: skip
        evidence.append(_row("가격에 담긴 영구 성장률", _pct(rd["implied_growth"]),
                             "g = (r − E/P)/(1 + E/P), 고든 모형",
                             "계산 (docs/analysis.md 15장)", fu.get("as_of")))  # fmt: skip
        out["reverse_dcf"] = rd
    # 19. 1년 예상의 네 눈 — 모델 합의
    out["agreement"] = insights.agreement(out)
    out["lines"] = 줄
    out["evidence"] = evidence
    return out


def scenario(*, close: float | None, band: dict | None, fundamentals: dict | None) -> dict | None:
    """1년 시나리오 (docs/analysis.md 14장). 1년 뒤 주당순자산 = 지금 × (1 + ROE − 배당 ÷ 자본),
    약세·기본·강세 = 자기 3년 PBR 밴드 20%·중앙값·80% × 그 주당순자산. "PBR 그대로" = 종가 × (1 + 순자산 성장).

    배당 ÷ 자본 = 배당수익률 ÷ 순자산수익률(bp) — 둘 다 시가총액이 분모라 나누면 자본 기준이 된다.
    재료가 모자라면 None."""
    f = fundamentals or {}
    b = band or {}
    roe = f.get("roe")
    pbr, 가격 = b.get("pbr"), b.get("prices") or {}
    if not isinstance(close, (int, float)) or close <= 0 or not isinstance(roe, (int, float)):
        return None
    if not isinstance(pbr, (int, float)) or pbr <= 0:
        return None
    if not all(isinstance(가격.get(k), (int, float)) for k in ("p20", "p50", "p80")):
        return None
    dy, bp = f.get("dividend_yield"), f.get("bp")
    배당몫 = dy / bp if isinstance(dy, (int, float)) and isinstance(bp, (int, float)) and bp > 0 else 0.0
    g = roe - 배당몫
    if 1 + g <= 0:  # 자본이 1년 안에 다 사라지는 셈 — 가격이 0 이하가 된다. 시나리오로 말할 수 없다 (25.1042)
        return None
    return {"growth": g, "roe": roe, "payout_part": 배당몫, "bear": 가격["p20"] * (1 + g),
            "base": 가격["p50"] * (1 + g),
            "bull": 가격["p80"] * (1 + g), "hold": close * (1 + g), "as_of": f.get("as_of")}  # fmt: skip


@dataclass
class Inputs:
    name: str
    ticker: str
    score: dict | None = None  # as_of, total, rank, ranked, factors{}, skip_reason
    signals: list[dict] = field(default_factory=list)  # horizon, as_of, buy_zone_low/high, target_price, stop_price
    checks: list[dict] = field(default_factory=list)  # horizon, as_of, passed, failed_count, rows[]
    position: dict | None = None  # quantity, pnl_pct, price_date
    flags: list[dict] = field(default_factory=list)  # level, rationale_text, as_of
    against: list[dict] = field(default_factory=list)  # text, evidence(dict)
    currency: str = "KRW"
    #: 유니버스 밖이면 그 제외 사유 — 점수는 "유니버스 + 이 종목" 으로 낸 참고 점수다 (docs/analysis.md 8장, 25.1018)
    excluded_reason: str | None = None
    #: 가격·가치 진단 (`outlook`, docs/analysis.md 9장, 25.1023)
    outlook: dict | None = None


def _row(label: str, display: str | None, threshold: str, source: str, as_of: str | None) -> dict:
    """근거표 한 행 — 다른 근거표(`signals._criterion` 등)와 같은 모양. 통과·탈락이 아니라 사실이라 `passed` 는 None."""
    return {"label": label, "display": display, "threshold": threshold, "source": source, "as_of": as_of,
            "passed": None}  # fmt: skip


def _n(x: Any, digits: int = 0) -> str:
    return f"{x:,.{digits}f}" if isinstance(x, (int, float)) else "-"


def _price(x: Any, currency: str) -> str:
    return _n(x, 2 if currency == "USD" else 0)


def nearest(checks: list[dict]) -> dict | None:
    """신호가 안 난 기간 가운데 탈락 기준이 가장 적은 것. 같으면 단기 → 중기 → 장기."""
    후보 = [c for c in checks if not c.get("passed") and c.get("failed_count") is not None]
    if not 후보:
        return None
    return min(후보, key=lambda c: (int(c["failed_count"]), HORIZON_ORDER.index(c["horizon"])
                                    if c["horizon"] in HORIZON_ORDER else 9))  # fmt: skip


def _요약(sc: dict, outlook: dict | None, currency: str, *, reference: bool) -> list[str]:
    """종합 분석 문장의 조각 (docs/analysis.md 10.2) — 점수 순위·3개월 수익률·밴드 위치·증권사 괴리·1년 예상 주가.
    모두 근거 줄·근거표에 같은 값이 있다. 없는 것은 빠진다."""
    조각: list[str] = []
    rank, ranked = sc.get("rank"), sc.get("ranked")
    if reference:
        순위 = f"(유니버스 기준 {rank:,}위 상당/{ranked:,})" if rank and ranked else ""
        조각.append(f"참고 점수 {_n(sc['total'], 1)}{순위}")
    else:
        순위 = f"(시장 {rank:,}위/{ranked:,}, 상위 {rank / ranked * 100:.0f}%)" if rank and ranked else ""
        조각.append(f"점수 {_n(sc['total'], 1)}{순위}")
    o = outlook or {}
    m3 = (o.get("momentum") or {}).get("momentum_3m")
    if isinstance(m3, (int, float)):
        조각.append(f"3개월 {_pct(m3)}")
    밴드 = o.get("band") or {}
    if isinstance(밴드.get("rank"), (int, float)):
        조각.append(f"PBR 밴드 {밴드['rank']:.0f}% 지점")
    c = o.get("consensus") or {}
    if isinstance(c.get("upside"), (int, float)):
        조각.append(f"증권사 목표가 {_pct(c['upside'])}")
    f = o.get("forecast") or {}
    일년 = next((h for h in f.get("horizons") or [] if h.get("months") == 12), None)
    if 일년:
        범위 = (f"(68% {_won(일년['low68'], currency)}~{_won(일년['high68'], currency)})" if "low68" in 일년 else "")
        조각.append(f"1년 예상 {_won(일년['expected'], currency)}{범위}")
        if isinstance(일년.get("p_up"), (int, float)):
            조각.append(f"1년 뒤 오를 확률 {일년['p_up'] * 100:.0f}%")
    return 조각


def ladder(*, close: float | None, currency: str, outlook: dict | None, signals: list[dict],
           checks: list[dict]) -> dict | None:  # fmt: skip
    """가격 사다리 (docs/analysis.md 12.2, 25.1038) — 이 종목에 걸린 가격들을 한 줄로 세우고
    지금에서의 거리와 도달 확률을 붙인다.

    모두 이미 있는 값이다: 52주 고점·PBR 밴드 기준 가격·증권사 목표가·1년 68% 범위·신호의 매수 구간·목표·손절·
    판정표 기준을 가격으로 푼 것(`signals.price_levels`). 도달 확률은 10장의 CAPM 기대수익·변동성 가정으로(12.2)."""
    o = outlook or {}
    if not isinstance(close, (int, float)) or close <= 0:
        return None
    f = o.get("forecast") or {}
    er, s = f.get("er"), f.get("sigma")
    확률 = isinstance(er, (int, float)) and isinstance(s, (int, float)) and s > 0
    items: list[dict] = []

    def 더하기(label: str, price: Any, kind: str, source: str, **extra: Any) -> None:
        if not isinstance(price, (int, float)) or price <= 0:
            return
        칸: dict[str, Any] = {"label": label, "price": float(price), "kind": kind, "source": source,
                             "dist": price / close - 1, **extra}  # fmt: skip
        if 확률 and abs(price / close - 1) > 1e-9:
            칸["touch"] = {str(m): touch_prob(price / close, er, s, m / 12) for m in TOUCH_MONTHS}
        items.append(칸)

    prox = (o.get("momentum") or {}).get("high_52w_proximity")
    if isinstance(prox, (int, float)) and 0 < prox < 1:
        더하기("52주 고점(종가)", close / prox, "high", "factors.momentum")
    밴드 = (o.get("band") or {}).get("prices") or {}
    for k, 이름 in (("p20", "PBR 밴드 20% 가격"), ("p50", "PBR 밴드 중앙값 가격"), ("p80", "PBR 밴드 80% 가격")):
        더하기(이름, 밴드.get(k), "band", "valuation_bands")
    c = o.get("consensus") or {}
    더하기(f"증권사 목표가 중앙값({c.get('brokers')}곳)", c.get("median"), "consensus", "kr_opinions")
    일년 = next((h for h in f.get("horizons") or [] if h.get("months") == 12), None) or {}
    더하기("1년 예상 68% 하단", 일년.get("low68"), "range", "계산 (10.1)")
    더하기("1년 예상 68% 상단", 일년.get("high68"), "range", "계산 (10.1)")
    if signals:
        g = signals[0]
        기간 = HORIZON.get(g.get("horizon"), g.get("horizon"))
        더하기(f"{기간} 매수 구간 상단", g.get("buy_zone_high"), "signal", "signals")
        더하기(f"{기간} 매수 구간 하단", g.get("buy_zone_low"), "signal", "signals")
        더하기(f"{기간} 신호 목표가", g.get("target_price"), "target", "signals")
        더하기(f"{기간} 신호 손절가", g.get("stop_price"), "stop", "signals")
    for ch in checks:
        for lv in ch.get("levels") or []:
            need = lv.get("need")
            p = lv.get("price")
            if isinstance(p, (int, float)) and need in ("above", "below"):
                더하기(str(lv.get("label")), p, "criterion", "signal_checks", need=need,
                       met=(close > p) if need == "above" else (close <= p))  # fmt: skip
    if not items:
        return None
    items.sort(key=lambda x: -x["price"])
    out: dict[str, Any] = {"close": close, "items": items, "touch_months": list(TOUCH_MONTHS), "assumed": 확률}
    if 확률 and signals and signals[0].get("target_price") and signals[0].get("stop_price"):
        g = signals[0]
        p = race_prob(float(g["target_price"]) / close, float(g["stop_price"]) / close, er, s)
        if p is not None:
            out["race"] = {"target": float(g["target_price"]), "stop": float(g["stop_price"]), "p": p}
    return out


def build(inp: Inputs) -> dict:
    """결론·근거·반대 목소리·근거표. 반환은 `stock_verdicts` 한 행의 재료."""
    reasons: list[str] = []
    evidence: list[dict] = []
    sc = inp.score

    # 근거 (4장) — 점수와 팩터. 문턱 없이 사실만
    if sc and sc.get("total") is not None:
        if sc.get("rank") and sc.get("ranked"):
            순위 = (f" · 유니버스 기준 {sc['rank']:,}위 상당/{sc['ranked']:,}" if inp.excluded_reason
                    else f" · 시장 {sc['rank']:,}위/{sc['ranked']:,}")  # fmt: skip
        else:
            순위 = ""
        이름 = "참고 점수(유니버스 + 이 종목으로 계산)" if inp.excluded_reason else "종합 점수"
        reasons.append(f"{이름} {_n(sc['total'], 1)}{순위} ({sc['as_of']})")
        # 참고 점수는 `scores` 에 없다 — 출처를 그대로 적는다 (25.1019)
        출처 = REFERENCE_SOURCE if inp.excluded_reason else "scores"
        evidence.append(_row("종합 점수", _n(sc["total"], 1), "—", 출처, sc["as_of"]))
        f = {k: v for k, v in (sc.get("factors") or {}).items() if k in FACTOR and isinstance(v, (int, float))}
        if len(f) >= 2:
            hi, lo = max(f, key=f.get), min(f, key=f.get)  # type: ignore[arg-type]
            reasons.append(f"가장 강한 팩터 {FACTOR[hi]} {_n(f[hi])} · 가장 약한 팩터 {FACTOR[lo]} {_n(f[lo])}")
            for k, v in f.items():
                evidence.append(_row(f"팩터 {FACTOR[k]}", _n(v), "—",
                                     출처 if inp.excluded_reason else "scores.factor_scores", sc["as_of"]))

    # 결론 (3장)
    붉은 = [x for x in inp.flags if x.get("level") in ("red", "yellow")]
    near = nearest(inp.checks)
    if inp.position and 붉은:
        key = "check_holding"
        x = 붉은[0]
        headline = f"보유 점검 — 매도 플래그 {LEVEL.get(x['level'], x['level'])}: {x['rationale_text']}"
        for x in 붉은:
            evidence.append(_row(f"매도 플래그({LEVEL.get(x['level'], x['level'])})", x["rationale_text"],
                                 "docs/sell_flags.md", "sell_flags", x.get("as_of")))
    elif inp.signals:
        key = "consider_buy"
        s = inp.signals[0]
        기간 = " · ".join(HORIZON.get(g["horizon"], g["horizon"]) for g in inp.signals)
        목표 = f" · 목표 {_price(s['target_price'], inp.currency)}" if s.get("target_price") else ""
        손절 = f" · 손절 {_price(s['stop_price'], inp.currency)}" if s.get("stop_price") else ""
        headline = (f"매수 검토 — {기간} 매수 신호 ({s['as_of']}). 권장 매수 구간 "
                    f"{_price(s['buy_zone_low'], inp.currency)}~{_price(s['buy_zone_high'], inp.currency)}{목표}{손절}"
                    + (" · 이미 보유 중(추가 매수라면 비중 상한 확인)" if inp.position else ""))  # fmt: skip
        for g in inp.signals:
            구간 = f"{_price(g['buy_zone_low'], inp.currency)}~{_price(g['buy_zone_high'], inp.currency)}"
            evidence.append(_row(f"{HORIZON.get(g['horizon'], g['horizon'])} 매수 신호", 구간, "docs/signals.md",
                                 "signals", g["as_of"]))
    elif inp.position:
        key = "hold"
        손익 = f" · 평가손익률 {inp.position['pnl_pct']:+.1f}%" if inp.position.get("pnl_pct") is not None else ""
        headline = f"보유 유지 — 매도 플래그 없음{손익}" + (
            f" ({inp.position['price_date']})" if inp.position.get("price_date") else "")
    elif inp.excluded_reason and sc and sc.get("total") is not None:
        key = "reference"
        headline = (f"참고 분석 — 유니버스 밖({inp.excluded_reason})이라 추천·신호 대상이 아닙니다. "
                    + " · ".join(_요약(sc, inp.outlook, inp.currency, reference=True)))
        # 참고 판정표 — 같은 신호 규칙을 이 종목에 돌린 결과. 신호(구간·금액)는 내지 않는다 (25.1019)
        충족 = [c for c in inp.checks if c.get("passed")]
        if 충족:
            기간 = "·".join(HORIZON.get(c["horizon"], c["horizon"]) for c in 충족)
            headline += f" · 참고 판정표: {기간} 신호 조건 충족"
            reasons.append(f"참고 판정표: {기간} 신호 조건을 모두 충족 ({충족[0].get('as_of')}) — 유니버스 밖이라"
                           " 매수 구간·금액은 내지 않습니다")  # fmt: skip
        elif near:
            기간 = HORIZON.get(near["horizon"], near["horizon"])
            headline += f" · 참고 판정표: {기간} 기준 {near['failed_count']}개 남음"
            reasons.append(f"참고 판정표: {HORIZON.get(near['horizon'], near['horizon'])} 신호까지 기준 "
                           f"{near['failed_count']}개 남음 ({near.get('as_of')})")  # fmt: skip
            for r in [r for r in near.get("rows") or [] if r.get("passed") is False]:
                reasons.append(f"빠진 기준: {r.get('label')} — 지금 {r.get('display')} · 문턱 {r.get('threshold')}")
                evidence.append(_row(str(r.get("label")), r.get("display"), str(r.get("threshold")),
                                     str(r.get("source")), r.get("as_of")))
    elif sc and sc.get("total") is not None:
        key = "waiting"
        # 종합 분석 — 사실을 먼저 말하고 신호까지 남은 것은 끝에 (docs/analysis.md 10.2, 25.1024)
        headline = "종합 분석 — " + " · ".join(_요약(sc, inp.outlook, inp.currency, reference=False))
        if near:
            빠진 = [r for r in near.get("rows") or [] if r.get("passed") is False]
            headline += (f" · 매수 신호까지 {HORIZON.get(near['horizon'], near['horizon'])} 기준 "
                         f"{near['failed_count']}개 남음 ({near['as_of']})")  # fmt: skip
            for r in 빠진:
                reasons.append(f"빠진 기준: {r.get('label')} — 지금 {r.get('display')} · 문턱 {r.get('threshold')}")
                evidence.append(_row(str(r.get("label")), r.get("display"), str(r.get("threshold")),
                                     str(r.get("source")), r.get("as_of")))
        else:
            headline += " · 신호 판정표 없음(오늘 신호 계산 전이거나 대상 밖)"
    else:
        key = "undecided"
        사유 = (sc or {}).get("skip_reason") or "점수가 없습니다(유니버스 밖이거나 ETF)"
        headline = f"판단 보류 — {사유}"

    # 가격 사다리와 목표·손절 경주 (docs/analysis.md 12.2·12.3, 25.1038)
    사다리 = ladder(close=(inp.outlook or {}).get("close"), currency=inp.currency, outlook=inp.outlook,
                 signals=inp.signals, checks=inp.checks)  # fmt: skip
    if 사다리 and 사다리.get("race"):
        r = 사다리["race"]
        reasons.append(f"목표가 {_won(r['target'], inp.currency)}가 손절가 {_won(r['stop'], inp.currency)}보다"
                       " 먼저 닿을 확률 "
                       f"{r['p'] * 100:.0f}% (CAPM 기대수익·변동성 가정, 기간 제한 없음)")  # fmt: skip
        evidence.append(_row("목표가 먼저 닿을 확률", f"{r['p'] * 100:.0f}%", "표류 브라운 운동의 두 장벽",
                             "계산 (docs/analysis.md 12.3)", (inp.outlook or {}).get("close_date")))  # fmt: skip
    for it in (사다리 or {}).get("items") or []:
        if it["kind"] == "criterion":
            상태 = "충족" if it.get("met") else ("넘으면 충족" if it.get("need") == "above" else "밑돌면 충족")
            evidence.append(_row(f"가격 기준: {it['label']}", _price(it["price"], inp.currency),
                                 f"종가 {상태} · 지금에서 {_pct(it['dist'])}", "signal_checks.levels_json",
                                 (inp.outlook or {}).get("close_date")))  # fmt: skip

    against = list(inp.against)
    for a in against:
        if a.get("evidence"):
            evidence.append(a["evidence"])
    if key == "consider_buy" and against:
        headline += f" · 반대 목소리 {len(against)}개"
    if inp.outlook:
        evidence.extend(inp.outlook.get("evidence") or [])
    return {
        "verdict": key,
        "label": VERDICTS[key],
        "headline": headline,
        # 9.4 종합 의견 — 결론 다음에 가격·가치 진단 문장을 잇는다
        "outlook": None if not inp.outlook else {**{k: v for k, v in inp.outlook.items() if k != "evidence"},
                                                 "ladder": 사다리},
        "reasons": reasons,
        "against": [a["text"] for a in against],
        "nearest": None if not near else {"horizon": near["horizon"], "failed_count": near["failed_count"],
                                          "as_of": near.get("as_of")},  # fmt: skip
        "evidence": evidence,
    }
