"""같은 로트 청산 대조 — 추적 매도 대 목표 전량 매도 (핵심 H 실행 기록 진단, docs/backtest.md 10장 "청산 대조",
docs/factors.md 12.35 32회차 P1, docs/infra.md 25.1552).

core_h 가 새로 산 보유 가운데 **목표에 닿은 것만** 본다. 목표에 닿기 전에는 두 규칙이 같다(손절·기간초과가 같고
추적은 목표 뒤에만 켜진다, `pp.trend_exit`) — 그래서 같은 보유를 두 매도 규칙에 걸었을 때의 차이만 남는다.
판 돈의 재투자 효과는 일부러 뺀다(계좌 짝 G−B 를 판정에서 뺀 까닭과 같다, 25.1544 [필수] 2).

- `core` 다리(x_C): core_h 의 매도 판정(`sell_flags.evaluate`)에 "목표도달" 이 처음 선 결정일(`DayLog.target_hit`)
  — core 규칙이면 그날 팔았다. **원가는 core_h 장부 그대로**(`Lot.unit_cost` — 수수료 포함,
  더 산 몫이 있으면 운영처럼 평균)
- `trend` 다리(x_T): core_h 가 **실제로** 낸 그 뒤 첫 매도 결정일(`DayLog.sells`). 그림자 청산을 다시 짜지 않는다 —
  32회차 검증 A: `pp.trend_exit` 를 직접 부르면 고점·목표 표·기간 기본값을 다시 짜야 해 운영과 어긋날 수 있다
- 값은 두 날의 체결가 = 그날 또는 그 뒤 첫 종가(엔진이 정지 종목 매도를 미루는 것과 같다).
  d = 100 × [ln(P_xT/P_xC) − ln(I_xT/I_xC)], I = KOSPI. 왕복 비용은 두 규칙이 같다
- 읽기 0 — 핵심 H 실행이 메모리에 든 하루 기록·종가·지수만. 결정 규칙은 결과를 보기 전에 고정한다(`decide`)
"""

from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from typing import Any

from batch.services import paper as pp
from batch.services import sell_flags as sf

#: 판정 가능 — 결정 대상(단기, 끝 절단 아님) 로트 수 하한(32회차 검증 B 문구)
EXIT_PAIR_MIN_LOTS = 30
#: 판정 가능 — x_C 28일 묶음 수 하한(32회차 검증 B 문구)
EXIT_PAIR_MIN_BLOCKS = 12
#: 뉴이–웨스트 시차 — 단기 창 x_C→x_T 는 첫 매수 뒤 92 달력일 + 기간초과 다음 거래일·연휴로 약 101일까지라
#: 28일 묶음 경계에 따라 넷 떨어진 묶음끼리도 겹친다(101 ÷ 28 ≈ 3.6 → 4).
#: `pp.block_bound` 의 시차 1 은 20 거래일 창용이라 여기선 표준오차가 작게 나온다
#: (32회차 검증 A·B 는 3, 구현 반박 검증 25.1556 이 4 로 — 결과 전, 더 보수적으로만)
EXIT_PAIR_NW_LAG = 4
#: 한쪽 유의수준 — 본페로니. 같은 첫 실행에서 규칙을 바꿀 수 있는 갈래가 둘(이것의 `target` 과 `holding_eq` 의 `equal`)
EXIT_PAIR_ALPHA = 0.025
#: 결정은 이 기간 로트만 — 중기(365일)·장기(끝 없음)는 창이 묶음 열둘을 넘겨 시차 3 으로도 못 덮는다. 사실로만 적는다
EXIT_PAIR_DECIDE_HORIZON = "short"
#: 끝 절단 여유(달력일) — 첫 매수 + 기간 한도 + 이것이 자료 끝을 넘으면 결정에서 뺀다. x_T 는 결정일이라 정지로
#: 미룬 매도도 결정일에 잡힌다 — 이 여유가 덮는 것은 기간초과 판정 다음 거래일까지의 주말·연휴다(25.1556 정정).
#: 나간 것만 세면 빨리 나간 로트(추적이 일찍 걸린 것)만 남아 한쪽으로 기운다(32회차 검증 A)
EXIT_PAIR_TAIL_DAYS = 14


def _first_close(rows: list[tuple[str, float]], day: str) -> tuple[str, float] | None:
    """그날 또는 그 뒤 첫 종가 — 엔진이 체결가 없는 날의 매도를 미루는 것과 같다. 그 뒤 종가가 없으면(상장폐지·자료 끝
    정지) 그날 이하 마지막 종가 — 엔진이 상장폐지 종목을 마지막 종가로 판 것과 같다(25.1556 [권고] 3: 빼면 그날 크게
    빠진 로트만 골라 빠져 `trend` 쪽으로 기울었다)"""
    k = bisect.bisect_left(rows, (day, -math.inf))
    if k < len(rows):
        return rows[k]
    return rows[-1] if rows else None


def _index_upto(days: list[str], vals: list[float], d: str) -> float | None:
    k = bisect.bisect_right(days, d)
    return vals[k - 1] if k else None


def episodes(logs: list[Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """core_h 하루 기록 → 로트 에피소드들과 센 수.

    로트 = 판단 직전 들지 않은 종목을 산 날(`buys` 에 있고 `held` 에 없음)부터 판 뒤 사라질 때까지. 산 다음 날 `held` 에
    없으면 엔진이 못 산 것(뺀다). 매도 결정 없이 사라지면 상장폐지 — 목표에 닿은 뒤면 마지막 종가로 둔다(엔진과 같다)
    """
    열림: dict[int, dict[str, Any]] = {}
    끝: list[dict[str, Any]] = []
    센 = {"lots": 0, "vanished": 0, "delisted": 0, "added_before_touch": 0, "added_after_touch": 0}
    앞날: str | None = None
    for log in logs:
        for sid in list(열림):
            ep = 열림[sid]
            if sid in log.held or ep["start"] == log.day:
                continue
            del 열림[sid]
            if ep["xt"] is not None:
                끝.append(ep)
            elif ep["xc"] is not None:
                ep["xt"], ep["delisted"] = 앞날, True  # 매도 결정 없이 사라짐 — 마지막 종가(그 앞 거래일까지)
                센["delisted"] += 1
                끝.append(ep)
            elif ep["start"] == 앞날:
                센["vanished"] += 1  # 산 날 체결가가 없어 엔진이 못 샀다
                센["lots"] -= 1
        for sid, h in log.target_hit.items():
            ep = 열림.get(sid)
            # 손절 매도를 정지로 미루는 사이 닿은 것은 아니다
            if ep is not None and ep["xc"] is None and ep["xt"] is None:
                ep["xc"], ep["horizon"] = log.day, h
        for sid in log.sells:
            ep = 열림.get(sid)
            if ep is not None and ep["xt"] is None and log.sells[sid] != ["미룬 매도"]:
                ep["xt"] = log.day
        for sid in log.buys:
            if sid in log.held:
                ep = 열림.get(sid)
                if ep is not None and ep["xc"] is None:
                    ep["added"] = True
                elif ep is not None and ep["xt"] is None:
                    # 목표에 닿은 뒤 더 샀다 — 원가가 올라 추적이 꺼지거나 늦게 켜져 x_T 가 바뀐다.
                    # core 규칙이었다면 x_C 에 팔아 이 매수는 없었다 → "같은 보유에 매도 규칙만 다르게" 가
                    # 깨져 결정에서 뺀다(25.1556 [필수] 1)
                    ep["added_after"] = True
                continue
            if sid in 열림:
                continue
            열림[sid] = {"sid": int(sid), "start": log.day, "xc": None, "xt": None, "horizon": None, "added": False,
                        "added_after": False, "delisted": False}  # fmt: skip
            센["lots"] += 1
        앞날 = log.day
    끝.extend(열림.values())  # 자료 끝까지 열린 것 — 목표에 닿았으면 끝 절단으로 센다
    센["added_before_touch"] = sum(1 for ep in 끝 if ep["xc"] is not None and ep["added"])
    센["added_after_touch"] = sum(1 for ep in 끝 if ep["added_after"])
    return 끝, 센


def nw_bound(values: dict[int, float], lag: int = EXIT_PAIR_NW_LAG, alpha: float = EXIT_PAIR_ALPHA) -> dict[str, Any]:
    """묶음 값들 → {n_blocks, mean, se, lower, upper}. 뉴이–웨스트 시차 `lag`(바틀렛 가중 1 − l/(lag+1)),
    보정은 분산을 키울 때만 쓴다(`pp.block_bound` 와 같은 처리). 묶음 2개 미만이면 se·하한·상한 None"""
    xs = list(values.values())
    n = len(xs)
    if n < 2:
        return {"n_blocks": n, "mean": xs[0] if xs else None, "se": None, "lower": None, "upper": None}
    m = sum(xs) / n
    분산 = sum((x - m) ** 2 for x in xs) / (n - 1)
    보정 = 0.0
    for l in range(1, lag + 1):  # noqa: E741 — 시차 기호
        보정 += (1 - l / (lag + 1)) * sum((values[a] - m) * (values[a + l] - m) for a in values if a + l in values)
    분산 = max(분산, 분산 + 2 * 보정 / (n - 1))
    se = 분산**0.5 / n**0.5
    t = pp.t_quantile(1 - alpha, n - 1)
    return {"n_blocks": n, "mean": m, "se": se, "lower": m - t * se, "upper": m + t * se}


def summarize(logs: list[Any], closes: Any, kospi: dict[str, float]) -> dict[str, Any]:
    """core_h 하루 기록·종가(`core_evidence.Closes`)·KOSPI → 실행 기록 `exit_pair`."""
    eps, 센 = episodes(logs)
    마지막 = logs[-1].day if logs else None
    지수날 = sorted(kospi)
    지수값 = [float(kospi[d]) for d in 지수날]
    out: dict[str, Any] = dict(센)
    out |= {"touched": 0, "truncated": 0, "no_price": 0, "excluded_added_after": 0}
    기간별: dict[str, list[float]] = {}
    절단별: dict[str, int] = {}
    값: list[tuple[str, int, float]] = []
    for ep in eps:
        if ep["xc"] is None:
            continue
        out["touched"] += 1
        기간 = ep["horizon"] if ep["horizon"] in sf.DEFAULT_TARGETS else "long"
        한도 = sf.MAX_HOLDING_DAYS.get(기간)
        절단 = ep["xt"] is None or 마지막 is None or 한도 is None or (
            date.fromisoformat(ep["start"][:10]) + timedelta(days=한도 + EXIT_PAIR_TAIL_DAYS)
            > date.fromisoformat(마지막[:10]))  # fmt: skip
        rows = closes.rows(ep["sid"])
        c = _first_close(rows, ep["xc"])
        # 끝까지 안 나간 것(끝 절단)은 마지막 종가로 — 사실(기간별)에만 들어간다
        t = _first_close(rows, ep["xt"]) if ep["xt"] is not None else (rows[-1] if rows else None)
        if ep["delisted"] and ep["xt"] is not None:
            k = bisect.bisect_right(rows, (ep["xt"], math.inf))
            t = rows[k - 1] if k else None
        ic = _index_upto(지수날, 지수값, c[0]) if c else None
        it = _index_upto(지수날, 지수값, t[0]) if t else None
        if not c or not t or not ic or not it:
            out["no_price"] += 1
            continue
        d = 100 * (math.log(t[1] / c[1]) - math.log(it / ic)) if t[0] > c[0] else 0.0
        기간별.setdefault(기간, []).append(d)  # 사실 — 절단된 것도 마지막 날로 평가해 함께(기간마다 n·평균)
        if 절단:
            out["truncated"] += 1
            절단별[기간] = 절단별.get(기간, 0) + 1
            continue
        if 기간 == EXIT_PAIR_DECIDE_HORIZON:
            if ep["added_after"]:
                out["excluded_added_after"] += 1
                continue
            값.append((ep["xc"], ep["sid"], d))
    # 앞·뒤 반은 (x_C, 종목) 순 — d 값으로 정렬하면 같은 날 로트가 작은 d 부터 앞 반에 들어갔다(25.1556 [권고] 2)
    값.sort(key=lambda v: (v[0], v[1]))
    블록: dict[int, list[float]] = {}
    for day, _sid, x in 값:
        블록.setdefault(pp._묶음(day), []).append(x)
    bb = nw_bound({k: sum(v) / len(v) for k, v in 블록.items()})
    반 = len(값) // 2
    앞, 뒤 = [x for _d, _s, x in 값[:반]], [x for _d, _s, x in 값[반:]]
    out |= {
        "by_horizon": {h: {"n": len(v), "mean": _r(sum(v) / len(v)), "truncated": 절단별.get(h, 0)}
                       for h, v in sorted(기간별.items())},  # fmt: skip
        "horizon": EXIT_PAIR_DECIDE_HORIZON, "n": len(값), "n_blocks": bb["n_blocks"], "mean": _r(bb["mean"]),
        "se": _r(bb["se"]), "lower": _r(bb["lower"]), "upper": _r(bb["upper"]), "lag": EXIT_PAIR_NW_LAG,
        "alpha": EXIT_PAIR_ALPHA, "first_half": _r(sum(앞) / len(앞)) if 앞 else None,
        "second_half": _r(sum(뒤) / len(뒤)) if 뒤 else None,
    }  # fmt: skip
    out["verdict"] = decide(out)
    return out


def decide(s: dict[str, Any]) -> str:
    """사전 등록 결정 규칙(2026-10-19 고정 — 첫 실행 결과 전, 32회차 검증 B 문구).

    - 결정 대상: 단기·끝 절단 아님·**목표 뒤 더 사지 않은** 로트(25.1556)
    - 판정 불가: 결정 대상 로트 < `EXIT_PAIR_MIN_LOTS` 또는 묶음 < `EXIT_PAIR_MIN_BLOCKS` → 지금대로
    - trend: 한쪽 97.5% 하한 > 0 이고 앞·뒤 반 평균 모두 > 0 → 지금 G·H 의 추적 매도 그대로(근거)
    - target: 대칭 상한 < 0 이고 앞·뒤 반 모두 < 0 → **H 만** 매도를 core 로 바꾸는 판 후보(G 는 추적 대조로 남긴다)
    - none: 그 밖 → 지금대로. n·비율만 infra 에 적고 다시 보지 않는다
    """
    if (s.get("n") or 0) < EXIT_PAIR_MIN_LOTS or (s.get("n_blocks") or 0) < EXIT_PAIR_MIN_BLOCKS:
        return "undecidable"
    앞, 뒤 = s.get("first_half"), s.get("second_half")
    if s.get("lower") is not None and s["lower"] > 0 and 앞 is not None and 뒤 is not None and 앞 > 0 and 뒤 > 0:
        return "trend"
    if s.get("upper") is not None and s["upper"] < 0 and 앞 is not None and 뒤 is not None and 앞 < 0 and 뒤 < 0:
        return "target"
    return "none"


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None and math.isfinite(x) else None
