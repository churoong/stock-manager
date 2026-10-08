"""예측 성적표 — 그날 낸 예측을 쌓고, 기간이 지나면 실제 가격과 견준다 (docs/analysis.md 11장, docs/infra.md 25.1037).

2026-10-08 사용자: "이 메뉴만큼은 좀 더 진취적이었으면 좋겠다". 예측을 과감하게 내려면 **맞혔는지를 함께 보여야** 한다.
모델마다(CAPM·증권사 목표가·비슷한 국면 …) 같은 잣대로 센다. 계산만 한다 — DB 도 시각도 모른다.

성적 한 칸은 정수·실수 목록 `[n, n_range, in68, in90, dir_n, dir_hit, abs_err_sum]`:
  n         평가한 예측 수
  n_range   범위(68%·90%)가 있던 예측 수 — 증권사 목표가는 범위가 없다
  in68·in90 실제 수익률이 그 범위 안에 든 수
  dir_n     방향을 견줄 수 있던 수(예상·실제 수익률이 둘 다 0 이 아님)
  dir_hit   방향(오름·내림)이 맞은 수
  abs_err_sum  |ln(1+실제) − ln(1+예상)| 의 합 — 로그 수익률 오차. 평균은 합 ÷ n
"""

from __future__ import annotations

import calendar
import math
from datetime import date
from typing import Any

#: 성적 한 칸의 자리 이름 — 웹 `lib/analysis.ts` `TRACK_FIELDS` 와 같은 순서
FIELDS = ("n", "n_range", "in68", "in90", "dir_n", "dir_hit", "abs_err_sum")
#: 모델 이름 (화면 글자) — 웹 `MODEL_LABEL` 과 같다
MODELS = {"capm": "CAPM(시장·베타)", "consensus": "증권사 목표가", "analog": "비슷한 국면"}
#: 성적을 말할 최소 표본 — 이보다 적으면 "표본 n건" 만 적고 비율을 말하지 않는다(우연이 크다).
#: 비율 하나의 표준오차가 √(p(1−p)/n) ≈ 0.5/√n 이라 20건이면 ±11%p — 그 아래는 숫자가 거의 뜻이 없다
MIN_SAMPLE = 20


def months_back(d: date, months: int) -> date:
    """달력으로 `months` 달 앞. 그달에 그날이 없으면 그달 마지막 날(3-31 의 1달 앞 = 2-28/29)."""
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def log_models(outlook: dict | None) -> dict[str, dict[str, dict[str, float]]]:
    """오늘 낸 예측을 **수익률로** (기준 종가 대비). 진단(`verdict.outlook`)에 있는 것만 — 새로 계산하지 않는다."""
    o = outlook or {}
    close = o.get("close")
    if not isinstance(close, (int, float)) or close <= 0:
        return {}
    out: dict[str, dict[str, dict[str, float]]] = {}
    f = o.get("forecast") or {}
    capm: dict[str, dict[str, float]] = {}
    for h in f.get("horizons") or []:
        칸 = {"exp": h["expected"] / close - 1}
        for k in ("low68", "high68", "low90", "high90"):
            if isinstance(h.get(k), (int, float)):
                칸[k.replace("low", "lo").replace("high", "hi")] = h[k] / close - 1
        capm[str(h["months"])] = 칸
    if capm:
        out["capm"] = capm
    c = o.get("consensus") or {}
    if isinstance(c.get("median"), (int, float)) and c["median"] > 0:
        # 증권사 목표가는 보통 12개월 목표다 [확인필요: 증권사마다 기간 표기 — KIS 의견에는 기간 열이 없다]
        out["consensus"] = {"12": {"exp": c["median"] / close - 1}}
    a = o.get("analog") or {}
    analog: dict[str, dict[str, float]] = {}
    for h in a.get("horizons") or []:
        if isinstance(h.get("median"), (int, float)):
            칸 = {"exp": h["median"]}
            if isinstance(h.get("p16"), (int, float)) and isinstance(h.get("p84"), (int, float)):
                칸["lo68"], 칸["hi68"] = h["p16"], h["p84"]
            if isinstance(h.get("p05"), (int, float)) and isinstance(h.get("p95"), (int, float)):
                칸["lo90"], 칸["hi90"] = h["p05"], h["p95"]
            analog[str(h["months"])] = 칸
    if analog:
        out["analog"] = analog
    return out


def score_one(pred: dict[str, float], realized: float) -> list[float]:
    """예측 한 칸과 실제 수익률 하나 → 성적 한 칸."""
    exp = float(pred["exp"])
    칸 = [1, 0, 0, 0, 0, 0, 0.0]
    if "lo68" in pred and "hi68" in pred:
        칸[1] = 1
        칸[2] = int(pred["lo68"] <= realized <= pred["hi68"])
        if "lo90" in pred and "hi90" in pred:
            칸[3] = int(pred["lo90"] <= realized <= pred["hi90"])
    if exp != 0 and realized != 0:
        칸[4] = 1
        칸[5] = int((exp > 0) == (realized > 0))
    if exp > -1 and realized > -1:
        칸[6] = abs(math.log1p(realized) - math.log1p(exp))
    return 칸


def evaluate(models: dict, months: int, base_close: float, now_close: float) -> dict[str, list[float]]:
    """한 종목의 그날 예측 가운데 `months` 개월 것을 실제와 견준다. 모델 → 성적 한 칸."""
    if base_close <= 0 or now_close <= 0:
        return {}
    realized = now_close / base_close - 1
    out: dict[str, list[float]] = {}
    for model, 기간 in (models or {}).items():
        pred = (기간 or {}).get(str(months))
        if pred and isinstance(pred.get("exp"), (int, float)):
            out[model] = score_one(pred, realized)
    return out


def add(track: dict, model: str, months: int, cell: list[float]) -> None:
    """누계 `track[model][months]` 에 성적 한 칸을 더한다."""
    m = track.setdefault(model, {})
    cur = m.get(str(months)) or [0, 0, 0, 0, 0, 0, 0.0]
    m[str(months)] = [a + b for a, b in zip(cur, cell, strict=True)]


def rates(cell: list[float] | None) -> dict[str, Any] | None:
    """누계 한 칸 → 비율. 표본이 없으면 None."""
    if not cell or not cell[0]:
        return None
    n, nr, i68, i90, dn, dh, err = cell
    return {
        "n": int(n),
        "in68": i68 / nr if nr else None,
        "in90": i90 / nr if nr else None,
        "dir": dh / dn if dn else None,
        "err": err / n,
        "enough": n >= MIN_SAMPLE,
    }


def line(track: dict, *, scope: str) -> str | None:
    """성적표 한 줄 (근거 줄). 표본이 가장 많은 모델·기간 하나를 말한다. 없으면 None."""
    best: tuple[int, str, str] | None = None
    for model, 기간 in (track or {}).items():
        for 달, cell in 기간.items():
            if cell and (best is None or cell[0] > best[0]):
                best = (int(cell[0]), model, 달)
    if not best:
        return None
    _, model, 달 = best
    r = rates(track[model][달])
    if not r:
        return None
    이름 = "1년" if 달 == "12" else f"{달}개월"
    머리 = f"{scope} {MODELS.get(model, model)} {이름} 예측 {r['n']}건"
    if not r["enough"]:
        return f"{머리} — 표본이 {MIN_SAMPLE}건 미만이라 비율을 말하지 않습니다"
    조각 = []
    if r["in68"] is not None:
        조각.append(f"68% 범위 안 {r['in68'] * 100:.0f}%(기대 68%)")
    if r["dir"] is not None:
        조각.append(f"방향 적중 {r['dir'] * 100:.0f}%")
    return f"{머리}: " + " · ".join(조각) if 조각 else 머리
