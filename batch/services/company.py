"""모의 투자사의 부서 판단식과 투자위원회 절차 (docs/company.md 2·3장, docs/infra.md 25.1460). 순수 함수.

부서는 언어 모델이 아니라 **판단식**이다 — 같은 자료면 같은 의견서가 나온다(재현), 모든 의견에 근거 행이 붙는다(근거표).
판단식의 단일 정의처는 docs/company.md 2장 표이고, 이 파일의 문턱은 그 표의 숫자다
(`tests/test_doc_constants.py` 가 대조).
자료를 읽는 일은 `batch/jobs/paper.py` 가 하고 여기에는 **이미 읽은 값**(`Dossier`)만 들어온다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from batch.services.sell_flags import _criterion

#: 부서 — 표결하는 부서만. 트레이딩부·운용역은 표결하지 않는다 (docs/company.md 2장)
DEPARTMENTS: tuple[str, ...] = ("research", "quant", "broker", "flows", "governance", "sentiment", "risk", "macro")
DEPT_LABEL = {
    "research": "리서치부", "quant": "퀀트부", "broker": "증권사리서치부", "flows": "수급부",
    "governance": "공시·지배구조부", "sentiment": "뉴스·감성부", "risk": "리스크관리부", "macro": "시장전략부",
}  # fmt: skip

#: 점수 부서(리서치·퀀트·리스크)는 그날 그 시장 신호 종목 안의 백분위로 — 찬성·반대 문턱 (docs/company.md 2장)
PCT_FOR = 70
PCT_AGAINST = 30
#: 증권사리서치부 — 최근 90일, 목표가 있는 의견 2건 이상. 중앙 상승여력의 **신호 종목 안 백분위**로 판단한다
#: (점수 부서와 같은 문턱). 절대 문턱(+20%·+5%)은 소급 재생(25.1464)에서 의견 있는 종목을 늘 찬성으로 만들었다
#: — 국내 목표가 상승여력이 대부분 +20% 를 넘는다(25.1467)
BROKER_DAYS = 90
BROKER_MIN_N = 2
#: 수급부 — 5거래일 외국인+기관 순매수 대금 ÷ 20일 평균 거래대금
FLOW_DAYS = 5
FLOW_FOR = 0.5
FLOW_SPAN = 2.0
#: 공시·지배구조부 — 최근 1년, 전환사채 합(발행주식 대비 %) 문턱과 그때의 확신
GOV_DAYS = 365
CB_PCT = 10.0
CB_CONVICTION = 0.5
#: 뉴스·감성부 — 감성(−100~+100) 문턱과 확신 폭
SENT_FOR = 20.0
SENT_SPAN = 60.0
#: 시장전략부 — 약세 국면이면 이 확신으로 반대
MACRO_CONVICTION = 0.5
#: 위원회 — 가결에 필요한 찬성 부서 수(정족수), 헌장에서 한 부서의 가중이 전체에서 넘으면 안 되는 몫 (검증 [필수] 3)
QUORUM = 2
MAX_WEIGHT_SHARE = 0.40


@dataclass(frozen=True)
class Dossier:
    """한 종목의 조사 자료 — 부서들이 보는 원자료. (시장, 날짜, 종목)마다 한 벌이고 회사가 달라도 같다 (5장)."""

    stock_id: int
    ticker: str
    as_of: str  # 신호 기준일
    total_score: float | None = None
    factors: dict[str, float | None] = field(default_factory=dict)  # value·quality·growth·momentum
    #: 증권사 의견 — [(날짜, 증권사, 상승여력)] 목표가가 있고 그 날 종가를 잡은 것만 (부르는 쪽이 90일·발표일 종가로)
    broker_upsides: list[tuple[str, str, float]] = field(default_factory=list)
    #: 수급 — 5거래일 외국인+기관 순매수 대금 합, 20일 평균 거래대금(같은 단위), 모은 날 수, 마지막 날
    flow_sum: float | None = None
    turnover_20d: float | None = None
    flow_days: int = 0
    flow_date: str | None = None
    #: 공시·지배구조 — [(날짜, 종류)] 최근 1년, 전환사채 합(%)
    gov_events: list[tuple[str, str]] = field(default_factory=list)
    cb_pct: float | None = None
    domestic: bool = True  # 국내만 증권사·수급·공시 자료가 있다
    sentiment: float | None = None
    sentiment_date: str | None = None
    sentiment_error: str | None = None  # 감성을 **못 읽었다**(한도 등) — "아직 없다" 와 가른다 (25.164)
    gov_covered: bool = True  # 공시 사건 표가 기준일 앞 1년을 덮나 — 안 덮으면 "사건 없음" 이 아니라 "자료 없음"
    mdd: float | None = None  # 음수 비율 (−0.35 = 35% 하락)
    volatility: float | None = None
    perf_window: str | None = None
    perf_as_of: str | None = None
    regime: str | None = None  # bull · bear · unknown
    regime_index: str | None = None
    regime_date: str | None = None


@dataclass(frozen=True)
class Opinion:
    dept: str
    stance: int  # +1 찬성 · 0 중립 · −1 반대
    conviction: float  # 0~1
    evidence: list[dict[str, Any]]
    veto: bool = False  # 거부 조건에 걸렸나(헌장이 그 부서에 거부권을 줄 때만 효력)
    note: str | None = None  # "자료 없음" 등


def _없음(dept: str, 까닭: str) -> Opinion:
    return Opinion(dept, 0, 0.0, [], note=f"자료 없음 — {까닭}")


def percentile_ranks(values: dict[int, float | None]) -> dict[int, float]:
    """값이 있는 종목만 0~100 백분위(같으면 평균 순위). 한 종목뿐이면 50 (docs/company.md 2장)."""
    있음 = sorted((v, k) for k, v in values.items() if v is not None)
    n = len(있음)
    if n == 0:
        return {}
    if n == 1:
        return {있음[0][1]: 50.0}
    out: dict[int, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and 있음[j + 1][0] == 있음[i][0]:
            j += 1
        순위 = (i + j) / 2  # 0 부터의 평균 순위
        for k in range(i, j + 1):
            out[있음[k][1]] = 순위 / (n - 1) * 100
        i = j + 1
    return out


def _백분위_입장(pct: float) -> tuple[int, float]:
    입장 = 1 if pct >= PCT_FOR else -1 if pct <= PCT_AGAINST else 0
    return 입장, round(abs(pct - 50) / 50, 6)


def _혼합(factors: dict[str, float | None], mix: dict[str, float]) -> float | None:
    """가중 평균 — 빈 팩터는 빼고 남은 가중으로 다시 나눈다. 다 비면 None."""
    쓴 = [(float(factors[k]), w) for k, w in mix.items() if w > 0 and isinstance(factors.get(k), int | float)]  # type: ignore[arg-type]
    합 = sum(w for _v, w in 쓴)
    return sum(v * w for v, w in 쓴) / 합 if 합 > 0 else None


def research(
    dossiers: list[Dossier], mix: dict[str, float], population: list[Dossier] | None = None,
) -> dict[int, Opinion]:
    """리서치부 — 헌장의 밸류·퀄리티·성장 비율로 섞은 값의 **신호 종목 전체**(`population`) 안 백분위.

    안건 안에서 순위를 내면 안건 크기(회사마다 20·30)에 따라 같은 종목의 입장이 바뀌고, 점수 상위만 모인 안건의 아래 30%
    가 기계적으로 반대가 된다(25.1465 검증 [필수] 1). `population` 이 없으면 넘긴 것끼리(시험용)
    """
    섞음 = {d.stock_id: _혼합(d.factors, mix) for d in (population or dossiers)}
    pct = percentile_ranks(섞음)
    비율 = " · ".join(f"{k} {w:g}" for k, w in mix.items())
    out: dict[int, Opinion] = {}
    for d in dossiers:
        if d.stock_id not in pct:
            out[d.stock_id] = _없음("research", "밸류·퀄리티·성장 점수 없음")
            continue
        입장, 확신 = _백분위_입장(pct[d.stock_id])
        out[d.stock_id] = Opinion("research", 입장, 확신, [_criterion(
            "리서치 섞은 점수", f"{섞음[d.stock_id]:.1f} (신호 종목 안 {pct[d.stock_id]:.0f} 백분위)",
            f"찬성 ≥ {PCT_FOR} · 반대 ≤ {PCT_AGAINST} 백분위 (비율 {비율})", "scores", d.as_of)])  # fmt: skip
    return out


def quant(dossiers: list[Dossier], population: list[Dossier] | None = None) -> dict[int, Opinion]:
    """퀀트부 — 모멘텀 팩터의 신호 종목 전체 안 백분위."""
    값 = {d.stock_id: d.factors.get("momentum") for d in (population or dossiers)}
    pct = percentile_ranks(값)
    out: dict[int, Opinion] = {}
    for d in dossiers:
        if d.stock_id not in pct:
            out[d.stock_id] = _없음("quant", "모멘텀 점수 없음")
            continue
        입장, 확신 = _백분위_입장(pct[d.stock_id])
        out[d.stock_id] = Opinion("quant", 입장, 확신, [_criterion(
            "모멘텀 점수", f"{float(값[d.stock_id]):.1f} (신호 종목 안 {pct[d.stock_id]:.0f} 백분위)",  # type: ignore[arg-type]
            f"찬성 ≥ {PCT_FOR} · 반대 ≤ {PCT_AGAINST} 백분위", "scores", d.as_of)])  # fmt: skip
    return out


def risk(
    dossiers: list[Dossier], max_mdd: float | None, population: list[Dossier] | None = None, veto: bool = True,
) -> dict[int, Opinion]:
    """리스크관리부 — MDD·변동성 각각의 신호 종목 전체 안 위험 순위 평균. `max_mdd` 보다 나쁜 MDD 는 거부 조건.

    거부권이 없는 회사(`veto=False`)는 한도를 근거에 적기만 하고 입장을 덮어쓰지 않는다(25.1465 검증 권장)
    """
    # 위험 순위: MDD 는 더 음수일수록, 변동성은 클수록 위험 → 둘 다 "위험이 클수록 큰 값" 으로 맞춘다
    모집단 = population or dossiers
    mdd위험 = percentile_ranks({d.stock_id: (-d.mdd if d.mdd is not None else None) for d in 모집단})
    변동위험 = percentile_ranks({d.stock_id: d.volatility for d in 모집단})
    out: dict[int, Opinion] = {}
    for d in dossiers:
        쓴 = [x for x in (mdd위험.get(d.stock_id), 변동위험.get(d.stock_id)) if x is not None]
        if not 쓴:
            out[d.stock_id] = _없음("risk", "성과 지표(MDD·변동성) 없음")
            continue
        q = sum(쓴) / len(쓴)
        입장 = -1 if q >= PCT_FOR else 1 if q <= PCT_AGAINST else 0
        넘음 = max_mdd is not None and d.mdd is not None and d.mdd < max_mdd
        거부 = 넘음 and veto
        근거 = [_criterion(
            "위험 순위", f"{q:.0f} (MDD {('—' if d.mdd is None else f'{d.mdd * 100:.0f}%')} · 변동성 "
            f"{('—' if d.volatility is None else f'{d.volatility * 100:.0f}%')}, {d.perf_window or '창 모름'})",
            f"반대 ≥ {PCT_FOR} · 찬성 ≤ {PCT_AGAINST} (높을수록 위험)", "performance_metrics",
            d.perf_as_of)]  # fmt: skip
        if 넘음:
            한도 = f"헌장 한도 {max_mdd * 100:.0f}% 보다 나쁨" + (" — 거부 조건" if veto else "")  # type: ignore[operator]
            근거.append(_criterion("MDD 한도", f"{d.mdd * 100:.0f}%", 한도, "performance_metrics", d.perf_as_of))
        out[d.stock_id] = Opinion("risk", -1 if 거부 else 입장, 1.0 if 거부 else round(abs(q - 50) / 50, 6), 근거,
                                  veto=거부)  # fmt: skip
    return out


def _중앙여력(d: Dossier) -> float | None:
    return median(x for _d, _b, x in d.broker_upsides) if len(d.broker_upsides) >= BROKER_MIN_N else None


def broker(dossiers: list[Dossier], population: list[Dossier] | None = None) -> dict[int, Opinion]:
    """증권사리서치부 — 최근 90일 목표가 의견 중앙 상승여력의, 의견이 있는 신호 종목 안 백분위 (국내만)."""
    여력 = {d.stock_id: (_중앙여력(d) if d.domestic else None) for d in (population or dossiers)}
    pct = percentile_ranks(여력)
    out: dict[int, Opinion] = {}
    for d in dossiers:
        if not d.domestic:
            out[d.stock_id] = _없음("broker", "미국 종목 증권사 의견을 모으지 않는다")
            continue
        u = 여력.get(d.stock_id, _중앙여력(d))
        if u is None or d.stock_id not in pct:
            out[d.stock_id] = _없음(
                "broker", f"최근 {BROKER_DAYS}일 목표가 의견 {len(d.broker_upsides)}건(< {BROKER_MIN_N})")
            continue
        입장, 확신 = _백분위_입장(pct[d.stock_id])
        out[d.stock_id] = Opinion("broker", 입장, 확신, [_criterion(
            "증권사 중앙 상승여력",
            f"{u * 100:+.1f}% ({len(d.broker_upsides)}건, 신호 종목 안 {pct[d.stock_id]:.0f} 백분위)",
            f"찬성 ≥ {PCT_FOR} · 반대 ≤ {PCT_AGAINST} 백분위 (최근 {BROKER_DAYS}일)", "kr_opinions",
            max(x[0] for x in d.broker_upsides))])  # fmt: skip
    return out


def flows(d: Dossier) -> Opinion:
    """수급부 — 5거래일 외국인+기관 순매수 대금 ÷ 20일 평균 거래대금 (국내만)."""
    if not d.domestic:
        return _없음("flows", "미국 종목 수급을 모으지 않는다")
    if d.flow_days < FLOW_DAYS or d.flow_sum is None or not d.turnover_20d:
        return _없음("flows", f"수급 {d.flow_days}거래일(< {FLOW_DAYS}) 또는 거래대금 없음")
    f = d.flow_sum / d.turnover_20d
    입장 = 1 if f >= FLOW_FOR else -1 if f <= -FLOW_FOR else 0
    return Opinion("flows", 입장, round(min(1.0, abs(f) / FLOW_SPAN), 6), [_criterion(
        "외국인+기관 순매수", f"{FLOW_DAYS}일 합 ÷ 20일 평균 거래대금 = {f:+.2f}",
        f"찬성 ≥ +{FLOW_FOR:g} · 반대 ≤ −{FLOW_FOR:g}", "kr_flows", d.flow_date)])  # fmt: skip


def governance(d: Dossier) -> Opinion:
    """공시·지배구조부 — 지배구조 사건이면 반대 확신 1(거부 조건), 전환사채가 많으면 반대 0.5. 찬성은 하지 않는다."""
    if not d.domestic:
        return _없음("governance", "미국 종목 공시 사건을 모으지 않는다")
    if not d.gov_covered and not d.gov_events:
        # 표가 빈 날(덮개가 끊겨 다시 쓰는 중)·기준일 앞 1년을 덮지 않는 날 — 사건이 없는지 모른다.
        # 중립이면 거부권이 조용히 꺼진다
        return _없음("governance", f"공시 사건 표가 기준일 앞 {GOV_DAYS}일을 덮지 않음")
    if d.gov_events:
        return Opinion("governance", -1, 1.0, [_criterion(
            "지배구조 사건", ", ".join(f"{day} {kind}" for day, kind in d.gov_events[:3]),
            f"최근 {GOV_DAYS}일 1건 이상 — 거부 조건", "governance_events", max(x[0] for x in d.gov_events))],
            veto=True)  # fmt: skip
    if d.cb_pct is not None and d.cb_pct >= CB_PCT:
        return Opinion("governance", -1, CB_CONVICTION, [_criterion(
            "전환사채", f"발행주식 대비 {d.cb_pct:.1f}%", f"최근 {GOV_DAYS}일 합 ≥ {CB_PCT:g}%", "cb_issuances",
            d.as_of)])  # fmt: skip
    return Opinion("governance", 0, 0.0, [], note="사건 없음 — 중립(사건이 없는 것은 기본값)")


def sentiment(d: Dossier) -> Opinion:
    """뉴스·감성부 — `sentiment_scores` 의 감성(−100~+100)."""
    if d.sentiment_error:
        return _없음("sentiment", f"감성을 읽지 못함 ({d.sentiment_error[:60]})")
    if d.sentiment is None:
        return _없음("sentiment", "감성 없음(기사 부족·수집 전)")
    s = d.sentiment
    입장 = 1 if s >= SENT_FOR else -1 if s <= -SENT_FOR else 0
    return Opinion("sentiment", 입장, round(min(1.0, abs(s) / SENT_SPAN), 6), [_criterion(
        "뉴스 감성", f"{s:+.0f}" + ("" if d.sentiment_date else " (기준일 이하 며칠 안 최신)"),
        f"찬성 ≥ +{SENT_FOR:g} · 반대 ≤ −{SENT_FOR:g}", "sentiment_scores", d.sentiment_date or d.as_of)])  # fmt: skip


def macro(d: Dossier) -> Opinion:
    """시장전략부 — 그 종목 시장 지수가 200일 이동평균 아래(약세)면 반대."""
    if d.regime == "bear":
        return Opinion("macro", -1, MACRO_CONVICTION, [_criterion(
            "시장 국면", f"{d.regime_index} 약세(200일 이동평균 아래)", "약세 → 반대", "index_prices", d.regime_date)])
    if d.regime == "bull":
        return Opinion("macro", 0, 0.0, [_criterion(
            "시장 국면", f"{d.regime_index} 강세", "강세 → 중립(살지 말지를 정하지 않는다)", "index_prices",
            d.regime_date)])  # fmt: skip
    return _없음("macro", "국면 모름")


# ── 헌장 ──────────────────────────────────────────────────────────────────────


def charter_errors(charter: dict[str, Any]) -> list[str]:
    """헌장이 위원회 규칙에 맞나 — 틀리면 그 회사는 위원회를 열지 않고 판 1 로 돈다(부르는 쪽). 빈 목록이면 맞다."""
    틀림: list[str] = []
    try:
        가중 = {k: float(v) for k, v in (charter.get("weights") or {}).items() if float(v) > 0}
    except (TypeError, ValueError):
        return ["부서 가중에 숫자가 아닌 값이 있다"]
    if not 가중:
        return ["부서 가중이 없다"]
    모름 = sorted(set(가중) - set(DEPARTMENTS))
    if 모름:
        틀림.append(f"모르는 부서 {모름}")
    합 = sum(가중.values())
    넘음 = sorted(k for k, w in 가중.items() if w / 합 > MAX_WEIGHT_SHARE)
    if 넘음:
        틀림.append(f"한 부서 가중이 전체의 {MAX_WEIGHT_SHARE:.0%} 를 넘는다: {넘음}")
    if charter.get("lead") not in 가중:
        틀림.append("주관 부서가 표결 부서가 아니다")
    if sum(1 for _ in 가중) < QUORUM:
        틀림.append(f"표결 부서가 정족수({QUORUM})보다 적다")
    거부권 = list(charter.get("veto") or [])
    엉뚱 = sorted(set(거부권) - ({"governance", "risk"} & set(가중)))
    if 엉뚱:
        틀림.append(f"거부권은 가중이 있는 공시·지배구조·리스크관리 부서만 가질 수 있다: {엉뚱}")
    if "risk" in 거부권 and not isinstance(charter.get("max_mdd"), int | float):
        틀림.append("리스크관리 거부권에 max_mdd 가 없다 — 거부권이 소리 없이 꺼진다")
    for k in ("threshold", "agenda", "top_n"):
        if not isinstance(charter.get(k), int | float) or charter[k] <= 0:
            틀림.append(f"{k} 가 양수가 아니다")
    return 틀림


def charter_hash(charter: dict[str, Any]) -> str:
    """판단식 매개변수의 지문 — 부서 성적표가 회사마다 다른 리서치 비율을 섞지 않게 (docs/company.md 6장)."""
    return hashlib.sha256(json.dumps(charter, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def opinions(
    dossiers: list[Dossier], charter: dict[str, Any], population: list[Dossier] | None = None,
) -> dict[int, dict[str, Opinion]]:
    """헌장이 가중을 준 부서만 의견서를 낸다. {종목: {부서: 의견}}.

    점수 부서의 백분위는 `population`(그날 그 시장 신호 종목 전체 — 안건보다 넓다) 안에서 낸다(25.1465)
    """
    가중 = {k for k, v in (charter.get("weights") or {}).items() if float(v) > 0}
    표: dict[int, dict[str, Opinion]] = {d.stock_id: {} for d in dossiers}
    모집단 = list({d.stock_id: d for d in [*(population or []), *dossiers]}.values()) if population else None
    섞기 = charter.get("research_mix") or {"value": 1, "quality": 1, "growth": 1}
    단면 = {
        "research": lambda: research(dossiers, 섞기, 모집단),
        "quant": lambda: quant(dossiers, 모집단),
        "risk": lambda: risk(dossiers, charter.get("max_mdd"), 모집단, "risk" in (charter.get("veto") or [])),
        "broker": lambda: broker(dossiers, 모집단),
    }
    for 부서, 함수 in 단면.items():
        if 부서 in 가중:
            for sid, o in 함수().items():
                if sid in 표:
                    표[sid][부서] = o
    한종목 = {"flows": flows, "governance": governance, "sentiment": sentiment, "macro": macro}
    for d in dossiers:
        for 부서, f in 한종목.items():
            if 부서 in 가중:
                표[d.stock_id][부서] = f(d)
    return 표


# ── 투자위원회 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Item:
    """안건 하나의 회의 결과 — 회의록 한 줄."""

    stock_id: int
    ticker: str
    opinions: dict[str, Opinion]
    vote: float
    favor: int  # 찬성 부서 수
    issue: bool  # 찬성·반대가 함께 있었나(쟁점)
    passed: bool
    reason: str  # 가결 · 부결 까닭
    vetoed_by: tuple[str, ...] = ()
    minority: tuple[str, ...] = ()  # 최종 결정과 반대 입장이었던 부서


def meet(
    agenda: list[Dossier], charter: dict[str, Any], population: list[Dossier] | None = None,
) -> tuple[list[Item], list[Item]]:
    """투자위원회 (docs/company.md 3장). (모든 안건의 회의 결과, 가결 안건 — 표 순으로 top_n 까지).

    1 상정(부르는 쪽이 점수 상위 agenda 개) 2 의견서 3 쟁점 4 주관 부서 반대는 부결
    5 표결(표 ≥ threshold · 찬성 ≥ 정족수) 6 거부권 7 결정. 가중 40% 상한은 `charter_errors` 가 헌장을 읽을 때 본다.
    `population` 은 점수 부서의 백분위를 낼 그날 신호 종목 전체(`opinions`)
    """
    가중 = {k: float(v) for k, v in (charter.get("weights") or {}).items() if float(v) > 0}
    주관 = str(charter.get("lead"))
    거부권 = set(charter.get("veto") or [])
    문턱 = float(charter["threshold"])
    표 = opinions(agenda, charter, population)
    결과: list[Item] = []
    for d in agenda:
        의견 = 표[d.stock_id]
        득표 = round(sum(가중[k] * o.stance * o.conviction for k, o in 의견.items()), 6)
        찬성 = sum(1 for o in 의견.values() if o.stance > 0)
        쟁점 = 찬성 > 0 and any(o.stance < 0 for o in 의견.values())
        거부 = tuple(sorted(k for k, o in 의견.items() if o.veto and k in 거부권))
        주관반대 = 쟁점 and 의견.get(주관) is not None and 의견[주관].stance < 0
        if 거부:
            까닭, 가결 = "위원회: 거부권(" + "·".join(DEPT_LABEL[k] for k in 거부) + ")", False
        elif 주관반대:
            까닭, 가결 = f"위원회: 쟁점에서 주관 부서({DEPT_LABEL[주관]}) 반대", False
        elif 찬성 < QUORUM:
            까닭, 가결 = f"위원회: 찬성 부서 {찬성} < 정족수 {QUORUM}", False
        elif 득표 < 문턱:
            까닭, 가결 = f"위원회: 표 {득표:.2f} < 문턱 {문턱:g}", False
        else:
            까닭, 가결 = f"위원회 가결: 표 {득표:.2f} · 찬성 {찬성}", True
        소수 = tuple(sorted(k for k, o in 의견.items() if (o.stance < 0 if 가결 else o.stance > 0)))
        결과.append(Item(d.stock_id, d.ticker, 의견, 득표, 찬성, 쟁점, 가결, 까닭, 거부, 소수))
    가결들 = sorted((i for i in 결과 if i.passed), key=lambda i: (-i.vote, i.ticker))[: int(charter["top_n"])]
    return 결과, 가결들


def memo(company: str, items: list[Item], approved: list[Item]) -> str:
    """운용역 메모 — 템플릿 (docs/company.md 3장 9). 지어낸 말 없이 회의 결과의 수만."""
    부결 = [i for i in items if not i.passed]
    거부 = [i for i in 부결 if i.vetoed_by]
    쟁점 = [i for i in items if i.issue]
    글 = (f"{company} 위원회: 안건 {len(items)} · 가결 {len(approved)} · 부결 {len(부결) - len(거부)}"
          f" · 거부 {len(거부)} · 쟁점 {len(쟁점)}")
    if approved:
        t = approved[0]
        찬 = "·".join(DEPT_LABEL[k] for k, o in t.opinions.items() if o.stance > 0)
        반 = "·".join(DEPT_LABEL[k] for k, o in t.opinions.items() if o.stance < 0)
        글 += f". 가장 높은 표 {t.ticker} {t.vote:.2f} (찬성 {찬 or '없음'}" + (f" / 반대 {반}" if 반 else "") + ")"
    return 글


def minutes_json(items: list[Item], approved: list[Item], charter: dict[str, Any], memo_text: str) -> dict[str, Any]:
    """회의록 — 근거 행은 싣지 않는다(근거는 `paper_dossiers`, docs/company.md 5장).

    입장·확신·표·쟁점·거부·결정·소수의견
    """
    가결 = {i.stock_id for i in approved}
    return {
        "charter_hash": charter_hash(charter), "memo": memo_text,
        "items": [{
            "stock_id": i.stock_id, "ticker": i.ticker, "vote": i.vote, "favor": i.favor, "issue": i.issue,
            "passed": i.passed, "approved": i.stock_id in 가결, "reason": i.reason, "vetoed_by": list(i.vetoed_by),
            "minority": list(i.minority),
            "opinions": {k: [o.stance, o.conviction] + ([o.note] if o.note else []) for k, o in i.opinions.items()},
        } for i in items],
    }  # fmt: skip


#: 판 2 헌장 (docs/company.md 1·4장). 마이그레이션이 `paper_accounts.charter_json` 에 이 값을 적는다
#: — 바꾸면 판 번호를 올린다
CHARTERS: dict[str, dict[str, Any]] = {
    "A": {
        "sell_rule": "value",
        "name": "가치투자사", "philosophy": "싸고 튼튼한 회사를 산다. 손실이 커지거나 회사가 나빠지면 판다",
        "weights": {"research": 2, "quant": 0.5, "broker": 1, "flows": 0.5, "governance": 1, "sentiment": 0.5,
                    "risk": 1, "macro": 0.5},
        "research_mix": {"value": 0.5, "quality": 0.4, "growth": 0.1}, "lead": "research", "veto": ["governance"],
        "max_mdd": None, "threshold": 1.0, "agenda": 20, "top_n": 5,
    },
    "C": {
        "sell_rule": "momentum",
        "name": "모멘텀투자사", "philosophy": "오르는 것을 산다. 목표에서 팔지 않고, 손실·악재에는 바로 나온다",
        "weights": {"research": 0.5, "quant": 2, "broker": 1, "flows": 1.5, "governance": 0.5, "sentiment": 1,
                    "risk": 0.5, "macro": 1.5},
        "research_mix": {"value": 0.2, "quality": 0.3, "growth": 0.5}, "lead": "quant", "veto": [],
        "max_mdd": None, "threshold": 1.0, "agenda": 20, "top_n": 5,
    },
    "D": {
        "sell_rule": "time",
        "name": "장기보유투자사", "philosophy": "좋은 회사를 사서 기다린다. 흔들림에 팔지 않는다",
        "weights": {"research": 2, "quant": 0.5, "broker": 1, "flows": 0.5, "governance": 1, "sentiment": 0,
                    "risk": 1, "macro": 0},
        "research_mix": {"value": 0.2, "quality": 0.5, "growth": 0.3}, "lead": "research", "veto": ["governance"],
        "max_mdd": None, "threshold": 1.0, "agenda": 20, "top_n": 5,
    },
    "E": {
        "sell_rule": "core",
        "name": "안정투자사", "philosophy": "잃지 않는 것이 먼저다. 넓게 나누고, 위험한 회사·약세장에서는 새로 덜 산다",
        "weights": {"research": 1, "quant": 0.5, "broker": 0.5, "flows": 0.5, "governance": 1, "sentiment": 0.5,
                    "risk": 2, "macro": 1.5},
        "research_mix": {"value": 0.3, "quality": 0.5, "growth": 0.2}, "lead": "risk", "veto": ["governance", "risk"],
        "max_mdd": -0.60, "threshold": 0.8, "agenda": 30, "top_n": 10,
    },
}  # fmt: skip


# ── 부서 성적표 (docs/company.md 6장, 25.1470) ──────────────────────────────────

#: 같은 (부서, 판단식 매개변수, 종목)이 이 달력일 안에 되풀이되면 첫 판단 하나만 센다 — 신호가 며칠 이어지면
#: 20 거래일 창이 겹친 표본이 쌓여 종목 둘로 "표본 30" 을 넘겼다(설계 검증 [필수] 8).
#: 성과 분해(docs/paper.md 10장)의 28일 에피소드와 같은 값
SCORECARD_EPISODE_DAYS = 28
#: 이보다 적은 표본은 "참고" (docs/paper.md 10장 성과 분해와 같은 하한)
SCORECARD_MIN_SAMPLE = 30


def dept_param(dept: str, charter: dict[str, Any]) -> str:
    """판단식 매개변수의 열쇠 — 리서치(밸류·퀄리티·성장 비율)·리스크(한도·거부권)만 회사마다 다르다."""
    if dept == "research":
        return json.dumps(charter.get("research_mix") or {}, sort_keys=True)
    if dept == "risk":
        거부 = "risk" in (charter.get("veto") or [])
        return json.dumps({"max_mdd": charter.get("max_mdd"), "veto": 거부}, sort_keys=True)
    return ""


def scorecard(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """부서 성적표. `records` = [{dept, param, stock_id, day, stance, excess}].

    excess = 그 뒤 20 거래일 지수 대비 초과(소수). (부서, 매개변수)마다: 찬성·반대·중립 표본 수, 찬성 평균 초과,
    반대 평균 초과, **가름 = 찬성 평균 − 반대 평균**(클수록 잘 가린다), 찬성 적중(초과 > 0 비율)·반대 적중
    (초과 < 0 비율). 자료 없음(입장 None)·성과 없음은 뺀다. 에피소드로 겹친 창을 하나로 센다
    """
    from datetime import date as _date

    마지막: dict[tuple[str, str, int], _date] = {}
    모음: dict[tuple[str, str], dict[int, list[float]]] = {}
    for r in sorted(records, key=lambda x: (str(x["day"]), x["dept"], str(x["param"]), int(x["stock_id"]))):
        if r.get("stance") is None or r.get("excess") is None:
            continue
        열쇠 = (str(r["dept"]), str(r["param"]), int(r["stock_id"]))
        날 = _date.fromisoformat(str(r["day"])[:10])
        if 열쇠 in 마지막 and (날 - 마지막[열쇠]).days < SCORECARD_EPISODE_DAYS:
            continue
        마지막[열쇠] = 날
        모음.setdefault((열쇠[0], 열쇠[1]), {1: [], 0: [], -1: []})[int(r["stance"])].append(float(r["excess"]))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for k, by in 모음.items():
        def 평균(xs: list[float]) -> float | None:
            return sum(xs) / len(xs) if xs else None

        찬, 반 = 평균(by[1]), 평균(by[-1])
        out[k] = {
            "n_for": len(by[1]), "n_against": len(by[-1]), "n_neutral": len(by[0]),
            "avg_for": 찬, "avg_against": 반, "spread": (찬 - 반) if 찬 is not None and 반 is not None else None,
            "hit_for": sum(x > 0 for x in by[1]) / len(by[1]) * 100 if by[1] else None,
            "hit_against": sum(x < 0 for x in by[-1]) / len(by[-1]) * 100 if by[-1] else None,
            "enough": len(by[1]) >= SCORECARD_MIN_SAMPLE and len(by[-1]) >= SCORECARD_MIN_SAMPLE,
        }  # fmt: skip
    return out


def records_from(
    day: str, items: list[Item], charter: dict[str, Any], excess: dict[int, float],
) -> list[dict[str, Any]]:
    """한 회의의 부서 입장 → 성적표 행. 자료 없음(note)은 입장 None 으로(성적표가 뺀다)."""
    return [
        {"dept": k, "param": dept_param(k, charter), "stock_id": i.stock_id, "day": day,
         "stance": None if o.note and o.note.startswith("자료 없음") else o.stance, "excess": excess.get(i.stock_id)}
        for i in items for k, o in i.opinions.items()
    ]  # fmt: skip


# ── 주간 위원회 복기 (docs/company.md 7장, 25.1473) ─────────────────────────────

#: 위원회 복기에 올리는 "결정이 틀렸고 소수의견이 옳았던" 문턱 — 20 거래일 지수 대비 이만큼(%p) 넘게 빗나간 것만
REVIEW_MISS_PCT = 5.0
#: 복기에 올리는 건수
REVIEW_TOP = 3


def review_lines(
    meetings: list[dict[str, Any]], decisions: list[dict[str, Any]], names: dict[int, str],
    charters: dict[str, dict[str, Any]],
) -> list[str]:
    """주간 요약의 투자사 절 — 회사마다 이번 주 회의·가결·집행, 그리고 **소수의견이 옳았던 결정** 상위 셋.

    `meetings` = 이번 주 `paper_meetings` 행(minutes_json), `decisions` = 20 거래일 성과가 찬 결정 원장 행
    (detail_json 에 committee). 소수의견이 옳았다 = 부결(위원회)했는데 지수보다 `REVIEW_MISS_PCT` 넘게 올랐다
    (찬성한 소수가 옳음), 또는 사서 그만큼 넘게 밑돌았다(반대한 소수가 옳음). 수치는 결정 원장 그대로
    """
    if not meetings and not decisions:
        return []
    out = ["[모의 투자사 — 투자위원회]"]
    for aid in sorted(charters):
        록들 = [m for m in meetings if m["account_id"] == aid]
        항목 = [i for m in 록들 for i in (json.loads(m.get("minutes_json") or "{}").get("items") or [])]
        out.append(f"  {aid} {charters[aid].get('name', '')}: 회의 {len(록들)} · 안건 {len(항목)}"
                   f" · 가결 {sum(bool(i.get('passed')) for i in 항목)}"
                   f" · 집행 {sum(bool(i.get('approved')) for i in 항목)}"
                   f" · 쟁점 {sum(bool(i.get('issue')) for i in 항목)}")  # fmt: skip
    빗나감: list[tuple[float, str]] = []
    for d in decisions:
        try:
            위 = (json.loads(d.get("detail_json") or "{}") or {}).get("committee")
        except ValueError:
            continue
        if not 위 or d.get("ret_20d") is None or d.get("bench_20d") is None:
            continue
        초과 = float(d["ret_20d"]) - float(d["bench_20d"])
        산것 = d.get("kind") == "buy"
        부결 = d.get("kind") == "skip" and str(d.get("reason") or "").startswith("위원회")
        소수 = [DEPT_LABEL.get(k, k) for k in 위.get("minority") or []]
        if not 소수:
            continue
        if 부결 and 초과 > REVIEW_MISS_PCT:
            글 = f"부결했는데 {초과:+.1f}%p — 소수의견(찬성: {'·'.join(소수)})이 옳았다"
        elif 산것 and 초과 < -REVIEW_MISS_PCT:
            글 = f"샀는데 {초과:+.1f}%p — 소수의견(반대: {'·'.join(소수)})이 옳았다"
        else:
            continue
        이름 = names.get(int(d["stock_id"]), str(d["stock_id"]))
        빗나감.append((abs(초과), f"  {d['account_id']} {d['decided_on']} {이름}: {글}"))
    if 빗나감:
        out.append(f"  소수의견이 옳았던 결정 (20 거래일 지수 대비 {REVIEW_MISS_PCT:g}%p 넘게 빗나감,"
                   f" 큰 순 {REVIEW_TOP}):")
        out += [글 for _x, 글 in sorted(빗나감, key=lambda t: -t[0])[:REVIEW_TOP]]
    return out
