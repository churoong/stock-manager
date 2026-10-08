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

from batch.services import signals

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
            band_note: str | None = None, market: dict | None = None, rf: float | None = None) -> dict:  # fmt: skip
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
    out["lines"] = 줄
    out["evidence"] = evidence
    return out


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
    return 조각


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
        "outlook": None if not inp.outlook else {k: v for k, v in inp.outlook.items() if k != "evidence"},
        "reasons": reasons,
        "against": [a["text"] for a in against],
        "nearest": None if not near else {"horizon": near["horizon"], "failed_count": near["failed_count"],
                                          "as_of": near.get("as_of")},  # fmt: skip
        "evidence": evidence,
    }
