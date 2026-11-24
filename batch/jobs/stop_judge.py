"""손절 매도 집행 판정 읽기 — 사전 등록 둘(docs/factors.md 12.53, 원장 #45·#46)의 재료를 읽어 수를 낸다 (25.2271).

읽기 전용. 매월 첫 발굴 회차 때 `stop-judge.yml`(수동) → `scripts/stop_judge.py` 로 한 번 돌린다. 문이 차기 전에는 수만,
문을 처음 넘긴 실행에서 한 번 값·판정을 낸다(`services/stop_exec` — 그 날짜를 `stop_exec.JUDGED` 에 적어 커밋).
일일 배치가 부르지 않는다(읽기·쓰기 +0). 출력은 `scripts/ops_tee.py` 를 거쳐 비공개 운영 이슈로만.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from batch.core import calendar as cal
from batch.services import signals as sg
from batch.services import stop_exec as sx

#: 표본 시작 며칠 앞부터 읽는다 — 체결일이 표본 시작인 사건의 결정일은 그 앞 거래일이다
READ_BACK_DAYS = 10
#: 계좌 집합의 매도 결정(사유에 '손절' 이 든 것)과 그 주문·체결 — 판 거름·365일 창 없음(`ATTR_FILLS_SQL` 과 다른 질의).
#: 추적손절·스윙손절도 읽히지만 판정 함수가 사유로 가른다
EVENTS_SQL = (
    "SELECT d.account_id, d.market, d.stock_id, d.decided_on, d.reason, d.ref_close, d.ref_date, d.ret_5d, d.ret_60d,"
    " d.bench_5d, d.outcome_note, o.order_type, o.status, o.deferrals, o.fill_basis, o.close_note,"
    " f.fill_date, f.price, f.day_close, s.ticker,"
    # 보유 기간 — 매도 체결의 horizon 은 늘 비어 있다(매도 주문이 싣지 않는다).
    # 결정일 앞 마지막 매수 체결에서 읽는다(25.2274)
    " (SELECT b.horizon FROM paper_fills b WHERE b.account_id = d.account_id AND b.stock_id = d.stock_id"
    "   AND b.side = 'buy' AND b.fill_date <= d.decided_on ORDER BY b.fill_date DESC, b.id DESC LIMIT 1) AS horizon"
    " FROM paper_decisions d JOIN stocks s ON s.id = d.stock_id"
    " LEFT JOIN paper_orders o ON o.account_id = d.account_id AND o.market = d.market AND o.decided_on = d.decided_on"
    "  AND o.stock_id = d.stock_id AND o.side = 'sell'"
    " LEFT JOIN paper_fills f ON f.order_id = o.id"
    " WHERE d.kind = 'sell' AND d.decided_on >= ? AND d.reason LIKE '%손절%'"
    " AND d.account_id IN (SELECT value FROM json_each(?))"
)
#: 사건 종목의 시세 — 체결일 행(시가·종가·등락률)과 그다음 행(종가 하한가 이월)
PRICES_SQL = (
    "SELECT stock_id, date, open, close, change_pct FROM prices"
    " WHERE date >= ? AND stock_id IN (SELECT value FROM json_each(?)) ORDER BY stock_id, date"
)
#: 권리락·배당락 일정 — 기준일을 `cal.ex_date_kr` 로 락일로 바꾼다. 일정 행이 없는 종목은 '락일 아님'(알려진 한계)
CORP_SQL = "SELECT code, kind, record_date FROM kr_corp_events WHERE record_date >= ?"
#: 꼬리 칸의 KOSPI 전일 종가 → 당일 종가 (지수 시가 열이 없다)
KOSPI_SQL = "SELECT date, close FROM index_prices WHERE index_code = 'KOSPI' AND date >= ? ORDER BY date"
LOCK_KINDS = ("bonus", "rights", "split", "dividend")


def load(client: Any) -> list[dict[str, Any]]:
    """사건 행에 달력·시세·일정으로 정하는 칸을 붙여 돌려준다 (`stop_exec.classify_*` 가 먹는 꼴)"""
    부터 = (date.fromisoformat(sx.SAMPLE_FROM) - timedelta(days=READ_BACK_DAYS)).isoformat()
    events = client.execute(EVENTS_SQL, [부터, json.dumps(list(sx.ACCOUNTS))]).dicts()
    국내 = [e for e in events if e["market"] == "KR"]
    ids = sorted({int(e["stock_id"]) for e in 국내})
    시세: dict[int, list[dict[str, Any]]] = {}
    if ids:
        for r in client.execute(PRICES_SQL, [부터, json.dumps(ids)]).dicts():
            시세.setdefault(int(r["stock_id"]), []).append(r)
    락: dict[str, dict[str, set[str]]] = {}  # 코드 → 종류 → 락일들
    for r in client.execute(CORP_SQL, [부터]).dicts() if 국내 else []:
        if r["kind"] in LOCK_KINDS:
            try:
                락일 = cal.ex_date_kr(date.fromisoformat(str(r["record_date"])[:10])).isoformat()
            except ValueError:
                continue
            락.setdefault(str(r["code"]), {}).setdefault(str(r["kind"]), set()).add(락일)
    for e in 국내:
        d = date.fromisoformat(str(e["decided_on"])[:10])
        e["next_session"] = cal.next_session("KR", d).isoformat()
        줄 = 시세.get(int(e["stock_id"]), [])
        체결일 = str(e["fill_date"] or e["next_session"])[:10]
        i = next((k for k, r in enumerate(줄) if str(r["date"])[:10] == 체결일), None)
        bar = 줄[i] if i is not None else {}
        뒤 = 줄[i + 1] if i is not None and i + 1 < len(줄) else None
        다음 = 뒤 or {}
        e |= {"bar_open": bar.get("open"), "bar_close": bar.get("close"), "bar_change_pct": bar.get("change_pct"),
              "has_next": 뒤 is not None, "next_open": 다음.get("open"), "next_close": 다음.get("close")}  # fmt: skip
        일정 = 락.get(str(e["ticker"]), {})
        e["div_ex"] = 체결일 in 일정.get("dividend", set())
        # D+1 ~ D+5 거래일 창에 권리락·배당락이 끼었나 (stop_cluster_defer)
        창, 날 = set(), d
        for _ in range(5):
            날 = cal.next_session("KR", 날)
            창.add(날.isoformat())
        e["lock_window"] = any(창 & s for s in 일정.values())
    return events


def facts(events: list[dict[str, Any]]) -> dict[str, Any]:
    """`stop_cluster_defer` 꼬리 (나) — 사실로만: 군집·홑날 따로 ret_60d 가 그 보유 기간 손절폭 이하인 비율·최저값,
    날마다 사건이 선 계좌 수의 최댓값. 판정에 쓰지 않는다. 손절폭은 기본값(`signals.DEFAULT_TARGETS`)"""
    군집 = sx.cluster_days(events)
    out: dict[str, Any] = {}
    표본 = [e for e in events if e.get("market") == "KR" and e.get("reason") == sx.STOP
            and str(e["decided_on"])[:10] >= sx.SAMPLE_FROM]  # fmt: skip
    기본 = sg.DEFAULT_TARGETS

    def 손절폭(e: dict[str, Any]) -> float:
        return float(기본.get(str(e.get("horizon") or "mid"), 기본["mid"])["stop_pct"])

    # 보유 기간을 못 읽은 사건은 중기(mid)로 셈한다 — 그 수를 함께 낸다
    out["horizon_unknown"] = sum(e.get("horizon") not in 기본 for e in 표본)

    for 이름, 군집인가 in (("cluster", True), ("single", False)):
        행 = [e for e in 표본 if e.get("ret_60d") is not None
              and (군집.get(str(e["decided_on"])[:10], 0) >= sx.CLUSTER_MIN_STOCKS) is 군집인가]  # fmt: skip
        둘째 = [float(e["ret_60d"]) <= 손절폭(e) for e in 행]
        out[이름] = {"n": len(행), "second_stop_share": sum(둘째) / len(둘째) if 둘째 else None,
                    "min_ret_60d": min((float(e["ret_60d"]) for e in 행), default=None)}  # fmt: skip
    계좌: dict[str, set[str]] = {}
    for e in 표본:
        계좌.setdefault(str(e["decided_on"])[:10], set()).add(str(e["account_id"]))
    out["max_accounts_per_day"] = max((len(s) for s in 계좌.values()), default=0)
    return out


def kospi_on(client: Any, days: list[str]) -> float | None:
    """그 날들의 KOSPI 전일 종가 → 당일 종가 평균(소수) — 꼬리 칸"""
    부터 = (date.fromisoformat(sx.SAMPLE_FROM) - timedelta(days=READ_BACK_DAYS)).isoformat()
    줄 = client.execute(KOSPI_SQL, [부터]).rows
    앞: dict[str, float] = {str(b[0])[:10]: float(a[1]) for a, b in zip(줄, 줄[1:], strict=False) if a[1]}
    종 = {str(r[0])[:10]: float(r[1]) for r in 줄 if r[1]}
    xs = [종[d] / 앞[d] - 1 for d in days if d in 앞 and d in 종]
    return sum(xs) / len(xs) if xs else None


def run(client: Any, today: str) -> list[str]:
    """두 판정의 줄 — 문 전에는 수만. 값이 나면 그 날짜를 `stop_exec.JUDGED` 에 적어 커밋한다(주 세션)"""
    events = load(client)
    짝 = sx.judge_pair(events, today)
    군 = sx.judge_cluster(events, today)
    줄 = [f"[손절 매도 집행 판정 — docs/factors.md 12.53 · 표본 {sx.SAMPLE_FROM}~ · 오늘 {today}]", *sx.lines(짝)]
    if 짝["status"] == "judge":
        줄.append(f"  KOSPI 전일 종가→당일 종가(갭 하락 체결일 평균): {kospi_on(client, 짝['gap_dates'])}")
    줄 += sx.lines(군)
    if 군["status"] == "judge":
        줄.append(f"  꼬리 (나) 사실: {facts(events)}")
    return 줄
