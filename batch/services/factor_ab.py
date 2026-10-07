"""지표 교체 A/B — 백테스트 **한 실행 안에서** 현행(A)과 교체(B) 구성의 팩터 IC 를 나란히 잰다
(docs/factors.md 12.12, 10회차).

왜 한 실행 안인가. 판정은 "시장·달마다 첫 기본 실행 하나" 만 세고(12장 머리), 실행 사이에는 DB 가 바뀐다 — 코드를 바꾸기
전·후 두 실행을 견주면 무엇이 달라서 IC 가 달라졌는지 가를 수 없다(검증 B).

**기록 전용이다.** `IC_NAMES`·`verdict` 에 넣지 않고 실행 기록의 `factor_ab` 키에만 남긴다. 운영 점수는 바꾸지 않는다.
미리 고정한 채택 기준(12.12): 시장마다 B 의 IC 평균·t 가 모두 A 보다 높고, 앞·뒤 반 **각각에서 B − A 개선분이 양**.
`rule_met` 는 그 기준을 기계적으로 적은 것이다 — 기본 파라미터 실행의 기록만 판정에 센다(부르는 쪽 `params.default`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from batch.services import factor_ic as fic
from batch.services import scoring as sc

#: 에코 모멘텀 중복 진단 — 시장 안 스피어만으로 잴 짝 (12.12 제안 1, 9회차 진단 5 와 같은 방식)
ECHO_PAIRS = (("momentum_echo", "momentum_12_1"), ("momentum_echo", "momentum_6m"))
#: ΔOI/총자산 겹침 기록 — 같은 기준일 시장 안 상관 (12.12 제안 2, 검증 A 조건 3)
OI_PAIRS = (
    ("oi_change_assets", "piotroski_lite"), ("oi_change_assets", "operating_income_growth"),
    ("oi_change_assets", "sue"),  # 10회차 검증 A 조건 3 — 11회차에서 넣었다(12.13)
)
#: 12회차 겹침 기록 (12.14). `op_assets~roa` 의 나라 평균이 0.9 이상이면 `quality_op_assets` 는 판정하지 않는다(미리
#: 고정)
QUALITY_PAIRS = (("op_assets", "roa"), ("op_assets", "operating_margin"))
RISK_PAIRS = (("idio_volatility", "volatility_ann"), ("idio_volatility", "max_daily_return"))
#: 겹침 문 (12.14, 결과 보기 전 고정). 변형 → (상관 짝, 문턱). 시장별 평균의 단순 평균이 문턱 이상이면 나라 계열
#: 판정을 "판정 불가(겹침)" 로
OVERLAP_GATE = {"quality_op_assets": ("op_assets~roa", 0.9)}
#: 나라 전체(한 실행 = 한 나라) 계열의 이름 — 거래소별 IC 를 쓴 종목 수로 가중한 것. **판정은 이 계열로만** 한다 (12.13)
COUNTRY_KEY = "ALL"
#: 시장 안 상관을 낼 최소 종목 수 — IC 와 같은 하한
MIN_PAIR_STOCKS = fic.MIN_STOCKS


@dataclass
class AbAccumulator:
    """리밸런스마다 쌓는다. {변형: {시장: {"a": [...], "b": [...], "a_cov": [...], "b_cov": [...]}}} 와 상관 계열."""

    ics: dict[str, dict[str, dict[str, list[Any]]]] = field(default_factory=dict)
    corr: dict[str, dict[str, list[float | None]]] = field(default_factory=dict)

    def add_period(
        self,
        inputs: list[sc.StockInput],
        a_scores: dict[int, dict[str, float | None]],
        forward: dict[int, float | None],
        market_of: dict[int, str],
        extra: dict[str, dict[int, float | None]] | None = None,
    ) -> None:
        """한 리밸런스. `a_scores` 는 현행 구성의 {종목: {팩터: 점수}}(전략과 같은 점수).

        `extra` 는 입력 지표에 없는 값(예: sue)."""
        시장수: dict[str, int] = {}
        for i in inputs:
            시장수[i.market] = 시장수.get(i.market, 0) + 1
        for 이름, (팩터, 구성) in sc.AB_VARIANTS.items():
            b = {r.stock_id: r.score for r in sc.score_factors(inputs, factor_metrics={**sc.FACTOR_METRICS, 팩터: 구성})
                 if r.factor == 팩터}  # fmt: skip
            a = {sid: fs.get(팩터) for sid, fs in a_scores.items()}
            _, _, a갈래 = fic.period_ic_by_market(a, forward, market_of)
            _, _, b갈래 = fic.period_ic_by_market(b, forward, market_of)
            # 나라 계열(거래소별 가중, `verdict` 와 같은 방식) — 판정 단위 (12.13). 두 쪽 다 있는 달만
            a_ic_all, a_n_all, _ = fic.period_ic_by_market(a, forward, market_of)
            b_ic_all, b_n_all, _ = fic.period_ic_by_market(b, forward, market_of)
            # 공통 표본 — 두 점수가 모두 있는 종목만. 정의 효과와 표본 효과(흑자전환이 B 에만 들어옴)를 가르는 기록용
            # (12.13)
            공통 = [sid for sid in a if a.get(sid) is not None and b.get(sid) is not None]
            a_c, _, _ = fic.period_ic_by_market({k: a[k] for k in 공통}, forward, market_of)
            b_c, _, _ = fic.period_ic_by_market({k: b[k] for k in 공통}, forward, market_of)
            전체 = self.ics.setdefault(이름, {}).setdefault(
                COUNTRY_KEY, {"a": [], "b": [], "a_cov": [], "b_cov": [], "a_common": [], "b_common": []}
            )
            같이 = a_ic_all is not None and b_ic_all is not None
            전체["a"].append(a_ic_all if 같이 else None)
            전체["b"].append(b_ic_all if 같이 else None)
            전체["a_cov"].append(a_n_all / len(inputs) if inputs else 0.0)
            전체["b_cov"].append(b_n_all / len(inputs) if inputs else 0.0)
            공통같이 = a_c is not None and b_c is not None
            전체["a_common"].append(a_c if 공통같이 else None)
            전체["b_common"].append(b_c if 공통같이 else None)
            for 시장 in sorted(set(a갈래) | set(b갈래)):
                칸 = self.ics.setdefault(이름, {}).setdefault(시장, {"a": [], "b": [], "a_cov": [], "b_cov": []})
                a_ic, a_n = a갈래.get(시장, (None, 0))
                b_ic, b_n = b갈래.get(시장, (None, 0))
                # 두 쪽 다 있는 달만 견준다 — 한쪽만 있는 달을 섞으면 평균이 다른 달끼리의 비교가 된다
                같이 = a_ic is not None and b_ic is not None
                칸["a"].append(a_ic if 같이 else None)
                칸["b"].append(b_ic if 같이 else None)
                n = 시장수.get(시장, 0)
                칸["a_cov"].append(a_n / n if n else 0.0)
                칸["b_cov"].append(b_n / n if n else 0.0)
        for x, y in ECHO_PAIRS + OI_PAIRS + QUALITY_PAIRS + RISK_PAIRS:
            키 = f"{x}~{y}"
            for 시장, rho in _market_spearman(inputs, x, y, extra).items():
                self.corr.setdefault(키, {}).setdefault(시장, []).append(rho)

    def log(self) -> dict[str, Any]:
        """실행 기록 `factor_ab` 에 남길 모양. 소수 넷째 자리까지."""

        def r(v: float | None) -> float | None:
            return None if v is None else round(v, 4)

        def 요약(s: fic.IcSummary) -> dict[str, Any]:
            return {"months": s.months, "mean": r(s.mean), "t": r(s.t_stat), "coverage": r(s.coverage),
                    "first_half": r(s.first_half_mean), "second_half": r(s.second_half_mean)}  # fmt: skip

        out: dict[str, Any] = {"variants": {}, "corr": {}}
        for 이름, 시장들 in self.ics.items():
            팩터 = sc.AB_VARIANTS[이름][0]
            칸들: dict[str, Any] = {}
            for 시장, 칸 in sorted(시장들.items()):
                a = fic.summarize(칸["a"], 칸["a_cov"])
                b = fic.summarize(칸["b"], 칸["b_cov"])
                차 = fic.summarize(
                    [None if x is None or y is None else y - x for x, y in zip(칸["a"], 칸["b"], strict=True)]
                )
                칸들[시장] = {"a": 요약(a), "b": 요약(b), "b_minus_a": 요약(차), "rule_met": rule_met(a, b, 차),
                            "verdict": ab_verdict(a, b, 차)}  # fmt: skip
                if "a_common" in 칸:
                    공통차 = fic.summarize([None if x is None or y is None else y - x
                                            for x, y in zip(칸["a_common"], 칸["b_common"], strict=True)])  # fmt: skip
                    칸들[시장]["b_minus_a_common"] = 요약(공통차)  # 기록용 — 판정에 쓰지 않는다
            out["variants"][이름] = {"factor": 팩터, "metrics": [m.name for m in sc.AB_VARIANTS[이름][1]],
                                    "by_market": 칸들}  # fmt: skip
        for 키, 시장들 in sorted(self.corr.items()):
            out["corr"][키] = {시장: {"mean": r(fic._mean([v for v in vs if v is not None])),
                                     "months": sum(1 for v in vs if v is not None)}
                              for 시장, vs in sorted(시장들.items())}  # fmt: skip
        for 이름, (짝, 문턱) in OVERLAP_GATE.items():
            나라 = out["variants"].get(이름, {}).get("by_market", {}).get(COUNTRY_KEY)
            평균들 = [v["mean"] for v in out["corr"].get(짝, {}).values() if v["mean"] is not None]
            if 나라 is not None and 평균들 and (rho := sum(평균들) / len(평균들)) >= 문턱:
                나라["verdict"] = f"판정 불가(겹침 {짝} ρ={rho:.2f} ≥ {문턱:g})"
        return out


def ab_verdict(a: fic.IcSummary, b: fic.IcSummary, diff: fic.IcSummary) -> str:
    """"통과" · "탈락" · "판정 불가(사유)" (docs/factors.md 12.13, 11회차).

    표본이 모자라면 **탈락과 구별한다** — `factor_ic.verdict` 와 같은 문턱(`MIN_MONTHS`·`MIN_COVERAGE`)·같은 순서.
    예전 `rule_met` 는 3개월이어도 True 를 낼 수 있었다. 판정에 세는 것은 나라 계열(`COUNTRY_KEY`)의 값뿐이다.
    """
    if min(a.months, b.months) < fic.MIN_MONTHS:
        return f"판정 불가(IC {min(a.months, b.months)}개월 < {fic.MIN_MONTHS})"
    if any(c is None or c < fic.MIN_COVERAGE for c in (a.coverage, b.coverage)):
        return f"판정 불가(표본 비율 < {fic.MIN_COVERAGE:g})"
    return "통과" if rule_met(a, b, diff) else "탈락"


def rule_met(a: fic.IcSummary, b: fic.IcSummary, diff: fic.IcSummary) -> bool:
    """12.12 의 미리 고정한 기준: B 의 IC 평균·t 가 모두 A 보다 높고, 앞·뒤 반 각각에서 B − A 가 양."""
    if None in (a.mean, b.mean, a.t_stat, b.t_stat, diff.first_half_mean, diff.second_half_mean):
        return False
    return (
        b.mean > a.mean  # type: ignore[operator]
        and b.t_stat > a.t_stat  # type: ignore[operator]
        and diff.first_half_mean > 0  # type: ignore[operator]
        and diff.second_half_mean > 0  # type: ignore[operator]
    )


def _market_spearman(
    inputs: list[sc.StockInput], x: str, y: str, extra: dict[str, dict[int, float | None]] | None
) -> dict[str, float | None]:
    """시장 안, 두 값이 모두 있는 종목만으로 낸 스피어만."""

    def 값(i: sc.StockInput, name: str) -> float | None:
        if extra and name in extra:
            return extra[name].get(i.stock_id)
        return i.metrics.get(name)

    짝: dict[str, tuple[list[float], list[float]]] = {}
    for i in inputs:
        vx, vy = 값(i, x), 값(i, y)
        if vx is None or vy is None:
            continue
        xs, ys = 짝.setdefault(i.market, ([], []))
        xs.append(float(vx))
        ys.append(float(vy))
    return {시장: (fic.spearman(xs, ys) if len(xs) >= MIN_PAIR_STOCKS else None) for 시장, (xs, ys) in 짝.items()}
