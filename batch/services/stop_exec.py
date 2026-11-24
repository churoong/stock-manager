"""손절 매도 집행 판정 — 사전 등록 둘의 순수 함수 (docs/factors.md 12.53, docs/infra.md 25.2270·25.2271).

2026-11-24 사용자 지시("폭락장에 손절가에 다 던져버려서 … 이런건 어떻게 해결해야할까") → 50회차 발굴. **운영 매도 규칙은
바꾸지 않는다** — 앞으로 쌓이는 손절 매도를 두 가지로 한 번씩 판정할 뿐이다.

- `stop_exec_pair`(원장 #45): 같은 손절 매도의 시가 다리(지금 체결가) 대 종가 다리(체결일 종가). 갭 하락 사건만 판정
- `stop_cluster_defer`(원장 #46): 같은 날 손절 종목이 둘 이상인 날의 손절분을 5거래일 더 들었다면(Δ5), 홑날과 견줌

**문이 차기 전에는 수만 낸다.** 값은 문을 처음 넘긴 실행에서 한 번 — 그 날짜를 `JUDGED` 에 적어 커밋하면 다시
내지 않는다.
계산만 한다(DB·시각·달력을 모른다 — 달력·일정으로 정한 칸은 부르는 쪽 `jobs/stop_judge` 이 사건에 붙여 준다).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from batch.services import adjust, risk_gate
from batch.services import paper as pp

#: 표본 시작(이 날부터 — 이 모듈 커밋의 KST 날짜 다음 날). 그 전은 '본 구간'(판정·문 세기 밖). 고치지 않는다
SAMPLE_FROM = "2026-11-25"
#: 등록 때 고정한 계좌 집합 — 그때 열려 있고 매도 원칙이 '손절' 을 따르는 계좌. 뒤에 연 계좌는 넣지 않는다
ACCOUNTS: tuple[str, ...] = ("A", "B", "C", "E", "G", "H", "R1", "R2", "ZL", "ZM")
#: 한쪽 α — 등록 상수(가족 구성이 바뀌어도 고치지 않는다)
ALPHA = 0.0125
STOP = "손절"
#: 값이 났거나 기한으로 닫힌(판정 불가) 실행의 날짜 — {"stop_exec_pair": "YYYY-MM-DD", "stop_cluster_defer": …}.
#: 적힌 판정은 다시 값을 내지 않는다. 적는 커밋은 `tests/test_stop_exec_2271` 의 `JUDGED == {}` 단언도 함께 고친다
JUDGED: dict[str, str] = {}

#: stop_exec_pair 문 — 갭 하락 사건·그런 체결일·그런 묶음. 기한까지 못 넘기면 판정 불가
PAIR_GATE = (100, 30, 6)
PAIR_DEADLINE = "2028-04-30"
#: stop_cluster_defer — 두 무리 닫힌 묶음 수(먼저 볼 문 · 기한 뒤 판정 하한) · 기한 · 묶음 성숙 달력일 · 군집 문턱
CLUSTER_BLOCKS = 12
CLUSTER_MIN_BLOCKS = 6
CLUSTER_DEADLINE = "2028-11-30"
MATURE_DAYS = 14
CLUSTER_MIN_STOCKS = 2
#: 꼬리 거부권 — 기본 종목 비중 상한 × ΣΔ5(%) 가 하루 손실 한도 아래(빌린 상수)
VETO_WEIGHT = 0.10
VETO_PCT = risk_gate.DAILY_LOSS_HALT_PCT


def _f(x: Any) -> float | None:
    try:
        return None if x is None or isinstance(x, bool) else float(x)
    except (TypeError, ValueError):
        return None


def _묶음(day: str) -> int:
    return date.fromisoformat(day[:10]).toordinal() // pp.EVIDENCE_BLOCK_DAYS


def _평균(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def closed_limit_down(close: float | None, change_pct: float | None) -> bool:
    """그날 종가가 국내 하한가였나 — 운영 `pp.limit_locked` 와 같은 판정(시장가 매도, 주식 호가)"""
    if not close or change_pct is None:
        return False
    주문 = pp.Order(id=0, account_id="", stock_id=0, side="sell", order_type="market", limit_price=None, quantity=1,
                  currency="KRW", fill_session="")  # fmt: skip
    bar = pp.Bar(date="", open=None, close=float(close), change_pct=float(change_pct))
    return pp.limit_locked(주문, bar, float(close))


def _공통_제외(e: dict[str, Any]) -> str | None:
    """두 등록이 같이 쓰는 제외 — 사유 이름(수만 센다) 또는 None"""
    if not e.get("fill_date") or _f(e.get("price")) is None:
        return "unfilled"
    결정일 = str(e["decided_on"])[:10]
    if str(e.get("ref_date") or "")[:10] != 결정일 or str(e["fill_date"])[:10] != e.get("next_session"):
        return "date_mismatch"
    if int(e.get("deferrals") or 0) > 0:
        return "deferred"
    if e.get("fill_basis") == "close":
        return "close_basis"
    if (_f(e.get("ref_close")) or 0) <= 0 or (_f(e.get("price")) or 0) <= 0:
        return "missing"
    무시가 = str(e.get("close_note") or "").startswith("시가 없음") or (
        e.get("bar_open") is None and _f(e.get("day_close")) == _f(e.get("price")))
    return "no_open" if 무시가 else None


def classify_pair(e: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """손절 매도 한 건 → (갈래, 값). 갈래: `event`·`wait`(종가 하한가인데 다음 시세가 아직 없다)·제외 사유.

    값 = {g0, d, limit_down}. g0 = 시가 ÷ 결정 종가 − 1, d = 종가 다리 ÷ 시가 다리 − 1 (여섯째 자리 반올림)
    """
    if e.get("reason") != STOP:
        겹침 = STOP in [x.strip() for x in str(e.get("reason") or "").split(",")]
        return ("overlap_reason" if 겹침 else "other_reason"), None
    if str(e.get("fill_date") or e["decided_on"])[:10] < SAMPLE_FROM:
        return "seen_period", None
    if e.get("order_type") != "market":
        return "not_market", None
    제외 = _공통_제외(e)
    if 제외:
        return 제외, None
    po, pc, ref = float(e["price"]), _f(e.get("day_close")), float(e["ref_close"])
    if pc is None or pc <= 0:
        return "missing", None
    종가 = _f(e.get("bar_close")) or pc
    if adjust.is_action(adjust.factor_of(ref, 종가, _f(e.get("bar_change_pct")))):
        return "corp_action", None
    if e.get("div_ex"):
        return "dividend_ex", None
    갇힘 = closed_limit_down(pc, _f(e.get("bar_change_pct")))
    if 갇힘:
        if not e.get("has_next"):
            return "wait", None
        pc = _f(e.get("next_open")) or _f(e.get("next_close"))
        if pc is None or pc <= 0:
            return "wait", None
    return "event", {"g0": round(po / ref - 1, 6), "d": round(pc / po - 1, 6), "limit_down": 갇힘}


Pair = tuple[dict[str, Any], dict[str, Any]]


def _중복_없앰(rows: list[Pair], 날키: str) -> list[Pair]:
    """제외를 건 뒤 남은 행 가운데 계좌 id 가 가장 앞선 것으로 (종목·날) 한 건"""
    고름: dict[tuple[int, str], Pair] = {}
    for e, v in sorted(rows, key=lambda x: str(x[0]["account_id"])):
        고름.setdefault((int(e["stock_id"]), str(e[날키])[:10]), (e, v))
    return list(고름.values())


def _하한(값: dict[int, float]) -> dict[str, Any]:
    b = pp.block_bound(값, ALPHA)
    상한 = None
    if b["lower"] is not None and b["se"] is not None:
        상한 = b["mean"] + pp.t_quantile(1 - ALPHA, b["n_blocks"] - 1) * b["se"]
    return {**b, "upper": 상한}


def judge_pair(events: list[dict[str, Any]], today: str, judged: dict[str, str] | None = None) -> dict[str, Any]:
    """`stop_exec_pair` — 수(늘) + 문을 처음 넘긴 실행에서만 값·판정. 국내만 판정, 미국은 수만"""
    judged = JUDGED if judged is None else judged
    # 기한 뒤 체결은 세지 않는다 — "기한까지 문 미달이면 판정 불가" 가 기한 뒤 사건으로 뒤집히지 않게(25.2274)
    국내 = [e for e in events if e.get("market") == "KR" and e.get("account_id") in ACCOUNTS
            and str(e.get("fill_date") or e["decided_on"])[:10] <= PAIR_DEADLINE]  # fmt: skip  # 미체결은 결정일로
    갈래: dict[str, int] = {}
    사건행: list[Pair] = []
    for e in 국내:
        k, v = classify_pair(e)
        갈래[k] = 갈래.get(k, 0) + 1
        if v is not None:
            사건행.append((e, v))
    사건 = _중복_없앰(사건행, "fill_date")
    갭 = [(e, v) for e, v in 사건 if v["g0"] < 0]
    날: dict[str, list[float]] = {}
    for e, v in 갭:
        날.setdefault(str(e["fill_date"])[:10], []).append(v["d"])
    묶음: dict[int, list[float]] = {}
    for day, ds in 날.items():
        묶음.setdefault(_묶음(day), []).append(_평균(ds))
    수 = {"events": len(사건), "gap_events": len(갭), "gap_days": len(날), "gap_blocks": len(묶음),
         "by": dict(sorted(갈래.items())),
         "us_rows": sum(e.get("market") == "US" and e.get("reason") == STOP for e in events)}  # fmt: skip
    문 = len(갭) >= PAIR_GATE[0] and len(날) >= PAIR_GATE[1] and len(묶음) >= PAIR_GATE[2]
    out: dict[str, Any] = {"name": "stop_exec_pair", "counts": 수, "gate": 문}
    if "stop_exec_pair" in judged:
        return {**out, "status": "judged_before", "judged_on": judged["stop_exec_pair"]}
    if not 문:
        return {**out, "status": "expired" if today > PAIR_DEADLINE else "wait"}
    b = _하한({k: _평균(v) for k, v in 묶음.items()})
    날값 = [_평균(날[d]) for d in sorted(날)]
    반 = len(날값) // 2
    앞, 뒤 = _평균(날값[:반]), _평균(날값[반:])
    통과 = b["lower"] is not None and b["lower"] > 0 and 앞 > 0 and 뒤 > 0
    ds = sorted(v["d"] for _e, v in 갭)
    음 = [x for x in ds if x < 0]
    꼬리수 = max(1, len(ds) // 10)
    날종목: dict[str, set[int]] = {}
    for e, _v in 사건:
        날종목.setdefault(str(e["fill_date"])[:10], set()).add(int(e["stock_id"]))
    out |= {
        "status": "judge", "verdict": "pass" if 통과 else "fold", "bound": b, "first_half": 앞, "second_half": 뒤,
        "block_days": {k: len(v) for k, v in sorted(묶음.items())}, "gap_dates": sorted(날),
        "tail": {
            "d_star_all_days": _평균([_평균([v["d"] if v["g0"] < 0 else 0.0 for e2, v in 사건
                                            if str(e2["fill_date"])[:10] == day]) for day in sorted(날종목)]),
            "p_neg": len(음) / len(ds), "mean_neg": _평균(음) if 음 else None, "worst_decile": _평균(ds[:꼬리수]),
            "min": ds[0], "half_split_min": ds[0] / 2, "limit_down": sum(v["limit_down"] for _e, v in 갭),
            "max_stocks_per_day": max(len(s) for s in 날종목.values()), "deferred": 갈래.get("deferred", 0),
        },
    }  # fmt: skip
    return out


def cluster_days(events: list[dict[str, Any]]) -> dict[str, int]:
    """국내 날(D)마다 손절이 선 서로 다른 종목 수 — 사유의 쉼표 분해에 '손절' 이 든 매도 결정, 제외를 걸기 전"""
    날: dict[str, set[int]] = {}
    for e in events:
        if (e.get("market") == "KR" and e.get("account_id") in ACCOUNTS and str(e["decided_on"])[:10] >= SAMPLE_FROM
                and STOP in [x.strip() for x in str(e.get("reason") or "").split(",")]):
            날.setdefault(str(e["decided_on"])[:10], set()).add(int(e["stock_id"]))
    return {d: len(s) for d, s in 날.items()}


def classify_cluster(e: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """손절 매도 결정 한 건 → (갈래, 값). 갈래: `event`·`wait`(5거래일 성과가 아직 없다)·제외 사유.

    값 = {delta5(소수), e5(%p — 지수 단순 초과, 없으면 None)}
    """
    if e.get("reason") != STOP:
        return "other_reason", None
    if str(e["decided_on"])[:10] < SAMPLE_FROM:
        return "seen_period", None
    r5 = _f(e.get("ret_5d"))
    if r5 is None and not e.get("outcome_note"):
        return "wait", None
    제외 = _공통_제외(e)
    if 제외:
        return 제외, None
    if e.get("lock_window"):
        return "lock_window", None
    if r5 is None:
        return "no_outcome", None
    b5 = _f(e.get("bench_5d"))
    delta = (1 + r5 / 100) / (float(e["price"]) / float(e["ref_close"])) - 1
    return "event", {"delta5": round(delta, 6), "e5": None if b5 is None else r5 - b5}


def judge_cluster(events: list[dict[str, Any]], today: str, judged: dict[str, str] | None = None) -> dict[str, Any]:
    """`stop_cluster_defer` — 수(늘) + 볼 시점의 한 번에서만 값·판정. 닫힌 묶음만 센다"""
    judged = JUDGED if judged is None else judged
    # 기한 뒤 결정일은 세지 않는다 — 기한 뒤 첫 실행의 "판정 불가" 가 그 뒤 사건으로 뒤집히지 않게(25.2274)
    events = [e for e in events if str(e["decided_on"])[:10] <= CLUSTER_DEADLINE]
    군집수 = cluster_days(events)
    국내 = [e for e in events if e.get("market") == "KR" and e.get("account_id") in ACCOUNTS
            and e.get("reason") == STOP and str(e["decided_on"])[:10] >= SAMPLE_FROM]  # fmt: skip
    갈래: dict[str, int] = {}
    분류 = []
    for e in 국내:
        k, v = classify_cluster(e)
        갈래[k] = 갈래.get(k, 0) + 1
        분류.append((e, k, v))
    # 기한 뒤에는 닫힘을 기한 다음 날 기준으로 굳힌다 — "기한 뒤 첫 실행" 을 언제 돌려도 같은 답(판정 불가가 묶음이
    # 뒤늦게 닫혀 판정으로 뒤집히지 않게, 25.2275)
    오늘 = min(date.fromisoformat(today[:10]), date.fromisoformat(CLUSTER_DEADLINE) + timedelta(days=1))
    덜참 = {_묶음(str(e["decided_on"])) for e, k, _v in 분류 if k == "wait"}

    def 닫힘(b: int) -> bool:
        끝 = date.fromordinal((b + 1) * pp.EVIDENCE_BLOCK_DAYS - 1)
        return b not in 덜참 and 오늘 > 끝 + timedelta(days=MATURE_DAYS)

    사건 = _중복_없앰([(e, v) for e, k, v in 분류 if k == "event" and v is not None], "decided_on")
    날값: dict[str, list[dict[str, Any]]] = {}
    for e, v in 사건:
        날값.setdefault(str(e["decided_on"])[:10], []).append(v)
    묶음: dict[int, dict[str, list[tuple[float, float | None]]]] = {}
    for day, vs in 날값.items():
        b = _묶음(day)
        if not 닫힘(b):
            continue
        무리 = "cluster" if 군집수.get(day, 0) >= CLUSTER_MIN_STOCKS else "single"
        e5들 = [v["e5"] for v in vs if v["e5"] is not None]
        묶음.setdefault(b, {"cluster": [], "single": []})[무리].append(
            (_평균([v["delta5"] for v in vs]), _평균(e5들) if e5들 else None))
    둘다 = {b: g for b, g in 묶음.items() if g["cluster"] and g["single"]}
    수 = {"events": len(사건), "days": len(날값), "cluster_days": sum(n >= CLUSTER_MIN_STOCKS for n in 군집수.values()),
         "single_days": sum(n < CLUSTER_MIN_STOCKS for n in 군집수.values()), "closed_blocks": len(묶음),
         "both_blocks": len(둘다), "one_sided_blocks": len(묶음) - len(둘다), "by": dict(sorted(갈래.items())),
         "us_rows": sum(e.get("market") == "US" and e.get("reason") == STOP for e in events)}  # fmt: skip
    out: dict[str, Any] = {"name": "stop_cluster_defer", "counts": 수, "gate": len(둘다) >= CLUSTER_BLOCKS}
    if "stop_cluster_defer" in judged:
        return {**out, "status": "judged_before", "judged_on": judged["stop_cluster_defer"]}
    기한뒤 = today[:10] > CLUSTER_DEADLINE
    if not out["gate"] and not 기한뒤:
        return {**out, "status": "wait"}
    if not out["gate"] and len(둘다) < CLUSTER_MIN_BLOCKS:
        return {**out, "status": "expired"}
    x = _하한({b: _평균([d for d, _ in g["cluster"]]) - _평균([d for d, _ in g["single"]]) for b, g in 둘다.items()})
    c = _하한({b: _평균([d for d, _ in g["cluster"]]) for b, g in 묶음.items() if g["cluster"]})
    e값 = {}
    for b, g in 둘다.items():
        ce, se = [x2 for _, x2 in g["cluster"] if x2 is not None], [x2 for _, x2 in g["single"] if x2 is not None]
        if ce and se:
            e값[b] = _평균(ce) - _평균(se)
    ex = _하한(e값)
    거부 = cluster_veto(분류, 군집수, 닫힘)
    양 = [k["lower"] is not None and k["lower"] > 0 for k in (x, c, ex)]
    if all(양) and not 거부["vetoed"]:
        판정 = "pass"
    elif c["upper"] is not None and c["upper"] < 0:
        판정 = "close"  # 군집일에도 즉시 매도
    elif 양[0] and 양[1] and ex["lower"] is not None and not 양[2] and not 거부["vetoed"]:
        판정 = "market_rebound"  # 시장 반등 몫 — 지수 국면 노출 질문으로. ③ 을 못 낸 날(지수 없음·묶음 부족)은 접음
    else:
        판정 = "fold"
    return {**out, "status": "judge", "verdict": 판정, "diff": x, "cluster": c, "excess": ex, "veto": 거부}


def cluster_veto(분류: list[tuple[dict[str, Any], str, dict[str, Any] | None]], 군집수: dict[str, int],
                 닫힘: Any) -> dict[str, Any]:  # fmt: skip
    """꼬리 거부권 (가) — 군집일의 (계좌, D)마다 0.10 × ΣΔ5(%) < 하루 손실 한도. 평균에서 뺀 사건도 넣는다
    (ret_5d 가 있으면 D 종가 기준 %, 없으면 걸린 것으로)"""
    합: dict[tuple[str, str], float] = {}
    모름: set[tuple[str, str]] = set()
    for e, k, v in 분류:
        day = str(e["decided_on"])[:10]
        if k in ("wait", "seen_period") or 군집수.get(day, 0) < CLUSTER_MIN_STOCKS or not 닫힘(_묶음(day)):
            continue
        열쇠 = (str(e["account_id"]), day)
        if v is not None:
            합[열쇠] = 합.get(열쇠, 0.0) + v["delta5"] * 100
        elif _f(e.get("ret_5d")) is not None:
            합[열쇠] = 합.get(열쇠, 0.0) + float(e["ret_5d"])
        else:
            모름.add(열쇠)
    최저 = min(합.values(), default=None)
    걸림 = sum(VETO_WEIGHT * s < VETO_PCT for s in 합.values())
    return {"vetoed": bool(걸림 or 모름), "breaches": 걸림, "unknown": len(모름),
            "worst_pct": None if 최저 is None else VETO_WEIGHT * 최저, "account_days": len(합)}  # fmt: skip


_머리칸 = ("name", "counts", "gate", "status", "verdict")


def lines(result: dict[str, Any]) -> list[str]:
    """사람이 읽을 줄 — 문 전·이미 판정한 뒤에는 수만(값을 싣지 않는다)"""
    c = result["counts"]
    머리 = f"[{result['name']}] 상태 {result['status']} · 문 {'참' if result['gate'] else '안 참'}"
    수 = " · ".join(f"{k} {v}" for k, v in c.items() if k != "by")
    out = [머리, f"  수: {수}", "  갈래: " + ", ".join(f"{k} {v}" for k, v in c["by"].items())]
    if result["status"] == "expired":
        return [*out, "  판정 불가(기한) — 이 날짜를 stop_exec.JUDGED 에 적어 커밋한다(닫음 — 다시 판정하지 않는다)"]
    if result["status"] != "judge":
        return out
    return [*out, "  판정(한 번 — 이 날짜를 stop_exec.JUDGED 에 적어 커밋): " + result["verdict"],
            "  값: " + repr({k: v for k, v in result.items() if k not in _머리칸})]  # fmt: skip
