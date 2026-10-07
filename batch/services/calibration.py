"""점수 보정표 — "종합 72점은 실제로 몇 % 였나" (docs/signals.md 10.1, docs/infra.md 25.949).

신호 성적표(10장)는 **기간·창별 평균**이다. "단기 20일 뒤 평균 +3.2%". 그것으로는 점수가 일을 하는지 모른다 — 72점과
58점이 같은 평균에 섞여 있다. 여기서는 신호 날의 **종합 점수**와 그 뒤 **수익률**을 짝지어 둘을 묻는다:
1. 점수 10점 구간마다 평균 수익·이긴 비율 (보정표). 표본 5건 미만 구간은 평균을 내지 않는다
2. 점수가 높을수록 수익이 높았나 — 스피어만 순위 상관 ρ. 표본 30건 미만이면 "아직 셀 수 없다"

왜 순위 상관인가: 수익률은 꼬리가 길어 피어슨은 한 건이 좌우한다. 묻는 것은 "순서가 맞나" 다.
왜 ±0.1 인가: ρ 는 −1~1 이고 n=30 에서 0.1 은 소음 안이다(표준오차 ≈ 1/√n ≈ 0.18). 그래서 |ρ| ≤ 0.1 은 "무관" 으로
읽는다 — 단정하지 않는 쪽이다. `[확인필요: 표본 100 을 넘기면 문턱을 좁힌다]`

점수는 저장된 `scores.total_score`(신호 기준일·그날의 가장 새 판), 수익은 `signal_outcomes.ret_*d` — 둘 다 DB 의 실제
수치다. 계산은 배치(주 1회 성적표 작업)에서 하고 결과는 `batch_runs.step_log.calibration` 에 남긴다. 웹은 읽기만 한다.
표를 새로 만들지 않는다 — 마이그레이션은 되돌릴 수 없는 일이라 사용자 결정 몫이다(docs/handoff.md). 쌓인 것을 보고 표로
옮긴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

#: 점수 구간 폭(점). 0~100 을 열 칸으로
BUCKET = 10
#: 구간 평균을 내는 표본 하한 — 성적표(`outcomes.summarize`)와 같은 값
MIN_BUCKET_N = 5
#: 순위 상관을 내는 표본 하한. 그 아래는 "아직 셀 수 없다"
MIN_CORR_N = 30
#: |ρ| 가 이 안이면 "점수와 수익이 무관했다" 로 읽는다 (머리말)
FLAT_RHO = 0.1
#: 이 아래 표본에서는 판정 뒤에 "단정하지 않음" 을 붙인다. 첫 실행(2026-10-04)이 5일 창 36건으로 ρ −0.29 를 냈는데,
#: 그 36건은 09-16~09-26 열흘치라 같은 장세를 여러 번 센 것이다 — 30건은 "셀 수 있다" 의 하한이지 "믿을 수 있다" 가
#: 아니다
CAUTION_N = 100


@dataclass(frozen=True)
class Bucket:
    lo: int
    hi: int
    n: int
    avg_ret: float | None
    win_rate: float | None


@dataclass
class Calibration:
    window: int
    n: int
    buckets: list[Bucket] = field(default_factory=list)
    rho: float | None = None
    verdict: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "n": self.n,
            "rho": None if self.rho is None else round(self.rho, 3),
            "verdict": self.verdict,
            "buckets": [
                {"lo": b.lo, "hi": b.hi, "n": b.n,
                 "avg_ret": None if b.avg_ret is None else round(b.avg_ret, 5),
                 "win_rate": None if b.win_rate is None else round(b.win_rate, 4)}
                for b in self.buckets
            ],
        }  # fmt: skip


def _ranks(values: list[float]) -> list[float]:
    """평균 순위(동률은 평균). 1부터."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """순위 상관. 동률이 있어도 맞게(순위의 피어슨). 한쪽이 전부 같으면 None."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    return sxy / math.sqrt(sxx * syy)


def _verdict(n: int, rho: float | None) -> str:
    if n < MIN_CORR_N:
        return f"아직 셀 수 없다 (표본 {n}건, {MIN_CORR_N}건부터)"
    if rho is None:
        # 표본은 찼는데 점수나 수익이 전부 같아 순위 상관이 정의되지 않는다 (25.977) — "30건부터" 는 틀린 까닭이었다
        return f"점수나 수익이 모두 같아 상관을 낼 수 없다 (n={n})"
    주의 = f" — 표본 {CAUTION_N}건 미만, 단정하지 않음" if n < CAUTION_N else ""
    if rho > FLAT_RHO:
        return f"점수가 높을수록 수익이 높았다 (ρ {rho:+.2f}, n={n}){주의}"
    if rho < -FLAT_RHO:
        return f"점수가 높을수록 수익이 **낮았다** (ρ {rho:+.2f}, n={n}) — 점수를 의심할 것{주의}"
    return f"점수와 수익이 무관했다 (ρ {rho:+.2f}, n={n}){주의}"


def calibrate(pairs: list[tuple[float, float]], window: int) -> Calibration:
    """(종합 점수, 수익률) 짝으로 보정표 하나. 짝이 없으면 n=0 의 빈 표."""
    cal = Calibration(window=window, n=len(pairs))
    if not pairs:
        cal.verdict = _verdict(0, None)
        return cal
    by: dict[int, list[float]] = {}
    for score, ret in pairs:
        lo = min(100 - BUCKET, max(0, int(score // BUCKET) * BUCKET))
        by.setdefault(lo, []).append(ret)
    for lo in sorted(by):
        rets = by[lo]
        n = len(rets)
        if n < MIN_BUCKET_N:
            cal.buckets.append(Bucket(lo, lo + BUCKET, n, None, None))
        else:
            cal.buckets.append(Bucket(lo, lo + BUCKET, n, sum(rets) / n, sum(1 for r in rets if r > 0) / n))
    cal.rho = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= MIN_CORR_N else None
    cal.verdict = _verdict(len(pairs), cal.rho)
    return cal


def pairs_for(
    outcomes: list[Any], scores: dict[tuple[int, str], float], window: int
) -> list[tuple[float, float]]:
    """성적(`outcomes.Outcome`)마다 그 신호 날의 점수를 찾아 짝짓는다. 점수나 그 창의 수익이 없으면 뺀다.

    같은 종목·같은 날의 기간별 신호(단기·중기·장기)는 **한 짝만** 센다 — 점수는 하나인데 세 번 세면 그 날이 세 표를
    가진다.
    """
    seen: set[tuple[int, str]] = set()
    out: list[tuple[float, float]] = []
    for o in outcomes:
        key = (int(o.stock_id), str(o.as_of_date))
        if key in seen:
            continue
        ret = o.rets.get(window)
        score = scores.get(key)
        if ret is None or score is None:
            continue
        seen.add(key)
        out.append((float(score), float(ret)))
    return out


def render_lines(cal: dict[str, Any] | Calibration) -> list[str]:
    """글 몇 줄. payload(dict)와 Calibration 둘 다 받는다 — 배치 출력과 웹이 같은 말을 하게."""
    d = cal.as_payload() if isinstance(cal, Calibration) else cal
    lines = [f"{d.get('window')}일 뒤: {d.get('verdict', '')}"]
    for b in d.get("buckets") or []:
        if b.get("avg_ret") is None:
            lines.append(f"  {b['lo']}~{b['hi']}점: 표본 {b['n']}건 — 평균 안 냄")
        else:
            평균, 이긴 = f"{b['avg_ret'] * 100:+.1f}%", f"{b['win_rate'] * 100:.0f}%"
            lines.append(f"  {b['lo']}~{b['hi']}점: 평균 {평균} · 이긴 {이긴} (n={b['n']})")
    return lines
