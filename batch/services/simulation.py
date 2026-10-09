"""실제 수익률로 그린 1년 경로 — 블록 부트스트랩 (docs/analysis.md 43장, docs/infra.md 25.1064).

예상 주가(10장)·도달 확률(12장)은 로그정규(정규분포) 가정이다. 주가의 실제 하루 수익은 꼬리가 두껍고 흔들림이 몰려 온다.
그래서 이 종목 자신의 지난 일간 로그 수익률을 **열흘 묶음째** 다시 뽑아 이어 붙인 1년 경로 수천 개로
같은 질문에 다시 답한다.

- 평균을 빼(표류 0) **흔들림의 모양만** 남긴다 — 지난 5년이 크게 올랐다고 앞으로도 그만큼 오른다고 보지 않는다.
  기대(중심)는 일일 의견이 CAPM 의 로그 표류로 옮긴다(끝 분포는 상수를 더하면 정확히 옮겨진다)
- 묶음(열흘)은 흔들림이 몰려 오는 성질(변동성 군집)을 조금 남긴다 (Künsch 1989 의 이동 블록 부트스트랩)
- 씨앗은 종목 번호 — 같은 입력이면 같은 결과(주마다 숫자가 까닭 없이 흔들리지 않는다)
계산만 한다 — DB 도 시각도 모른다.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

#: 부트스트랩에 쓰는 최근 거래일 — 약 5년. 주간 작업이 읽는 시세 창과 같다
BOOT_DAYS = 1260
#: 이보다 짧은 이력이면 내지 않는다 — 1년이 안 되면 한 국면을 되풀이할 뿐이다
BOOT_MIN_DAYS = 252
#: 묶음 길이(거래일) — 흔들림이 몰려 오는 성질을 남길 만큼, 묶음 수가 충분할 만큼(5년이면 약 125 묶음)
BOOT_BLOCK = 10
#: 경로 수 — 5% 분위의 표본 오차가 약 ±0.5%p(√(0.05·0.95/2000))
BOOT_PATHS = 2000
#: 경로 길이(거래일) — 1년
BOOT_HORIZON = 252
#: 끝 분포를 내는 거래일 — 예상 주가의 1·3·6·12개월(13장과 같은 환산)
BOOT_AT = (21, 63, 126, 252)
#: 끝 분포의 분위 — 90%·68%·50% 범위의 양끝과 중앙값
BOOT_Q = (0.05, 0.16, 0.25, 0.5, 0.75, 0.84, 0.95)
#: 도달 확률용 경로 최고·최저의 분위 격자 — 2.5% 간격 41칸(사이는 선형 보간)
TOUCH_GRID = 41
#: 도달 확률을 내는 거래일 — 사다리의 3개월·1년
TOUCH_AT = (63, 252)


def bootstrap(closes: list[float], seed: int) -> dict[str, Any] | None:
    """닫힌 계열(수정주가, 날짜 오름차순) → 1년 경로의 끝 분포·경로 최고/최저 분포(로그 수익). 모자라면 None."""
    c = np.asarray([x for x in closes if x and x > 0][-(BOOT_DAYS + 1) :], dtype=float)
    if len(c) < BOOT_MIN_DAYS + 1:
        return None
    r = np.diff(np.log(c))
    r = r - r.mean()  # 표류 0 — 흔들림의 모양만
    n_blocks = math.ceil(BOOT_HORIZON / BOOT_BLOCK)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(r) - BOOT_BLOCK + 1, size=(BOOT_PATHS, n_blocks))
    idx = (starts[:, :, None] + np.arange(BOOT_BLOCK)[None, None, :]).reshape(BOOT_PATHS, -1)[:, :BOOT_HORIZON]
    path = np.cumsum(r[idx], axis=1)  # 로그 수익 경로
    grid = np.linspace(0, 1, TOUCH_GRID)
    out: dict[str, Any] = {
        "days_used": int(len(r)), "sigma": round(float(r.std(ddof=1) * math.sqrt(252)), 4),
        "q": list(BOOT_Q), "terminal": {}, "max": {}, "min": {},
    }  # fmt: skip
    for d in BOOT_AT:
        out["terminal"][str(d)] = [round(float(x), 4) for x in np.quantile(path[:, d - 1], BOOT_Q)]
    for d in TOUCH_AT:
        hi = np.maximum(0.0, path[:, :d].max(axis=1))
        lo = np.minimum(0.0, path[:, :d].min(axis=1))
        out["max"][str(d)] = [round(float(x), 4) for x in np.quantile(hi, grid)]
        out["min"][str(d)] = [round(float(x), 4) for x in np.quantile(lo, grid)]
    return out


def _cdf(qs: list[float], x: float) -> float:
    """분위 격자(0~1 등간격) qs 에서 P(X ≤ x) — 선형 보간. 격자 밖은 0·1."""
    n = len(qs) - 1
    if x < qs[0]:
        return 0.0
    if x >= qs[-1]:
        return 1.0
    for i in range(n):
        a, b = qs[i], qs[i + 1]
        if a <= x < b:
            return (i + (x - a) / (b - a)) / n if b > a else (i + 1) / n
    return 1.0


def touch(boot: dict, ratio: float, days: int) -> float | None:
    """표류 0 에서 `days` 거래일 안에 가격이 지금의 `ratio` 배에 한 번이라도 닿을 확률(경로 최고·최저 분포에서)."""
    if not boot or ratio <= 0 or abs(ratio - 1) < 1e-12:
        return None
    x = math.log(ratio)
    if x > 0:
        qs = (boot.get("max") or {}).get(str(days))
        return None if not qs else round(1 - _cdf(qs, x - 1e-12), 4)
    qs = (boot.get("min") or {}).get(str(days))
    return None if not qs else round(_cdf(qs, x), 4)
