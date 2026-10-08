"""종목 분석 — 결론 한 줄·근거·반대 목소리 (docs/analysis.md, docs/infra.md 25.1016).

새 분석을 만들지 않는다. 이미 있는 규칙의 결과(신호·판정표·매도 플래그·보유·엇갈림·공시 반응)를 한 종목에 모아 말한다.
**새 문턱이 없다** — 결론은 규칙이 낸 사실(신호가 났나, 보유인가, 플래그가 걸렸나)로만 정한다(docs/analysis.md 2장).
계산만 한다. DB 도 시각도 모른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 결론 키 → 이름 (위에서부터 처음 맞는 것, docs/analysis.md 3장)
VERDICTS = {
    "check_holding": "보유 점검",
    "consider_buy": "매수 검토",
    "hold": "보유 유지",
    "reference": "참고 분석",
    "waiting": "신호 대기",
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
                    f"참고 점수 {_n(sc['total'], 1)}"
                    + (f"(유니버스 기준 {sc['rank']:,}위 상당/{sc['ranked']:,})" if sc.get("rank") and sc.get("ranked")
                       else ""))  # fmt: skip
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
        if near:
            빠진 = [r for r in near.get("rows") or [] if r.get("passed") is False]
            headline = (f"신호 대기 — {HORIZON.get(near['horizon'], near['horizon'])} 신호까지 기준 "
                        f"{near['failed_count']}개 남음 ({near['as_of']})")  # fmt: skip
            for r in 빠진:
                reasons.append(f"빠진 기준: {r.get('label')} — 지금 {r.get('display')} · 문턱 {r.get('threshold')}")
                evidence.append(_row(str(r.get("label")), r.get("display"), str(r.get("threshold")),
                                     str(r.get("source")), r.get("as_of")))
        else:
            headline = "신호 대기 — 판정표가 없습니다(오늘 신호 계산 전이거나 대상 밖)"
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
    return {
        "verdict": key,
        "label": VERDICTS[key],
        "headline": headline,
        "reasons": reasons,
        "against": [a["text"] for a in against],
        "nearest": None if not near else {"horizon": near["horizon"], "failed_count": near["failed_count"],
                                          "as_of": near.get("as_of")},  # fmt: skip
        "evidence": evidence,
    }
