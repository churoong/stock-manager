"""운용 상황실 요약을 적는다 (docs/desk.md 2장, docs/infra.md 25.1501).

일일 배치가 KIS모의투자 단계 뒤 부른다(곁다리 — 실패해도 배치는 그대로). 그날(한국 날짜) `paper_desk` 한 줄에
이 시장 칸을 합치고, 계좌 순위·KIS 계좌는 이번 것으로 덮는다. 웹 첫 화면(운용)은 이 줄을 읽기만 한다.
그날 줄을 처음 열 때는 **직전 줄의 시장 칸을 이어받는다** — 한 시장이 쉬는 날(미국 휴장·설 연휴)에도 그 시장의 마지막
실행 칸이 남게(25.1503, 검토 3). 이어받은 칸은 제 실행 시각(`run_at`)을 그대로 갖고 있다.

읽기(실행마다, 하루 두 번): 가상 계좌 평가 계열 전부(계좌 수 × 날 수 — 열 계좌 1년이면 약 3천 행, 몇 해 뒤 커지면 계좌별
첫·기준·마지막 줄만 읽게 줄인다)·최근 `FILL_LOOKBACK_DAYS` 일 체결(색인)·그 시장 열린 주문(색인)·KIS 주문·KIS 마지막 줄·
주문 원장 묶음(판 이력). 장부 검산은 가상 단계 실행 기록에서 읽는다(읽기 0)
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from batch.core import calendar as cal
from batch.core import db
from batch.jobs import broker_mirror as bmj
from batch.services import desk

TWR_SQL = "SELECT account_id, as_of, twr_index FROM paper_daily ORDER BY account_id, as_of"
NAMES_SQL = "SELECT id, name FROM paper_accounts"
#: 이 실행이 판정한 체결 = 주문이 이 판정일(`closed_on`)에 체결로 닫힌 것 —
#: 배치가 하루 빠져 밀린 체결(체결일이 더 이른 것)도 든다(25.1503, 검토 5).
#: 체결 색인(account_id, fill_date)을 타게 체결일 아래 끝을 둔다
FILL_LOOKBACK_DAYS = 14
FILLS_SQL = (
    "SELECT f.account_id, f.side, f.quantity, f.price, f.currency, f.fill_date, s.ticker,"
    " COALESCE(s.name_ko, s.name_en, s.ticker) AS name FROM paper_fills f"
    " JOIN paper_orders o ON o.id = f.order_id JOIN stocks s ON s.id = f.stock_id"
    " WHERE f.account_id IN (SELECT id FROM paper_accounts) AND f.fill_date >= ? AND o.market = ? AND o.closed_on = ?"
    " ORDER BY f.account_id, f.id"
)
#: 이 실행이 낸 주문 = 이 판정일에 결정한 열린 주문. 체결 장(`fill_session`)은 주문마다 그대로 — 장 끝난 뒤 손 실행이면
#: 다음다음 장이다(25.1447 — 25.1503, 검토 4)
ORDERS_SQL = (
    "SELECT o.account_id, o.side, o.order_type, o.limit_price, o.quantity, o.tranche, o.reason, o.currency,"
    " o.fill_session, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name FROM paper_orders o"
    " JOIN stocks s ON s.id = o.stock_id WHERE o.status = 'open' AND o.market = ? AND o.decided_on = ?"
    " ORDER BY o.account_id, o.id"
)
KIS_SQL = "SELECT as_of, summary_json FROM paper_broker_daily WHERE broker = ? ORDER BY as_of DESC LIMIT 1"
KIS_ORDERS_SQL = (
    "SELECT b.side, b.code, b.quantity, b.order_type, b.limit_price, b.status, b.message, b.reason,"
    " COALESCE(s.name_ko, s.name_en, s.ticker) AS name FROM paper_broker_orders b"
    " LEFT JOIN stocks s ON s.id = b.stock_id WHERE b.broker = ? AND b.trade_date = ? ORDER BY b.id"
)
#: 판 이력 — 주문 원장을 (계좌, 판)으로 묶는다. 주문 원장 전체를 훑는다(1년 수만 행 — 실행마다).
#: 커지면 판이 바뀐 날만 읽게 줄인다
RULES_SQL = (
    "SELECT account_id, rule_version, MIN(decided_on) AS first, MAX(decided_on) AS last, COUNT(*) AS n"
    " FROM paper_orders GROUP BY account_id, rule_version"
)
DESK_READ_SQL = "SELECT summary_json FROM paper_desk WHERE as_of = ?"
DESK_PREV_SQL = "SELECT summary_json FROM paper_desk WHERE as_of < ? ORDER BY as_of DESC LIMIT 1"
DESK_UPSERT_SQL = (
    "INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES (?, ?, ?)"
    " ON CONFLICT (as_of) DO UPDATE SET summary_json = excluded.summary_json, updated_at = excluded.updated_at"
)
#: 주문 사유는 한 줄로 자른다 — 화면 칸
REASON_CHARS = 80


def write(
    client: Any, market: str, now: datetime, *, trade_date: str, session_date: str, step_log: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    """그날 운용 요약을 합쳐 적고, 실행 기록에 넣을 건수를 돌려준다."""
    from datetime import date, timedelta

    as_of = now.astimezone(cal.market_tz("KR")).date().isoformat()
    지금 = now.astimezone(UTC).isoformat()
    계열: dict[str, list[tuple[str, float]]] = {}
    for r in client.execute(TWR_SQL).dicts():
        if r["twr_index"] is not None:
            계열.setdefault(str(r["account_id"]), []).append((str(r["as_of"]), float(r["twr_index"])))
    이름 = {str(r["id"]): str(r["name"] or r["id"]) for r in client.execute(NAMES_SQL).dicts()}
    순위 = desk.leaderboard(계열, 이름)

    h = bmj.MIRROR_ACCOUNT
    아래 = (date.fromisoformat(trade_date) - timedelta(days=FILL_LOOKBACK_DAYS)).isoformat()
    체결 = client.execute(FILLS_SQL, [아래, market, trade_date]).dicts()
    주문 = client.execute(ORDERS_SQL, [market, trade_date]).dicts()
    p = step_log.get("paper") or {}
    검산틀림 = [str(a) for a, v in (p.get("accounts") or {}).items()
                if isinstance(v, dict) and v.get("recon_ok") is False]  # fmt: skip
    h주문 = [r for r in 주문 if r["account_id"] == h]
    증권사 = bmj.BROKER_US if market == "US" else bmj.BROKER
    kis주문 = client.execute(KIS_ORDERS_SQL, [증권사, session_date]).dicts()
    시장칸 = {
        "run_at": 지금, "trade_date": trade_date, "session_date": session_date,
        "fills_h": [{"side": r["side"], "name": r["name"], "ticker": r["ticker"], "quantity": r["quantity"],
                     "price": r["price"], "currency": r["currency"], "fill_date": r["fill_date"]}
                    for r in 체결 if r["account_id"] == h],
        "fills_by_account": dict(Counter(str(r["account_id"]) for r in 체결)),
        "orders_h": [{"side": r["side"], "name": r["name"], "ticker": r["ticker"], "quantity": r["quantity"],
                      "order_type": r["order_type"], "limit_price": r["limit_price"], "tranche": r["tranche"],
                      "currency": r["currency"], "fill_session": r["fill_session"],
                      "reason": str(r["reason"] or "")[:REASON_CHARS]} for r in h주문],
        "orders_by_account": dict(Counter(str(r["account_id"]) for r in 주문)),
        "kis_orders": [{"side": r["side"], "name": r["name"] or r["code"], "quantity": r["quantity"],
                        "order_type": r["order_type"], "limit_price": r["limit_price"], "status": r["status"],
                        "reason": r["reason"], "message": str(r["message"] or "")[:REASON_CHARS]} for r in kis주문],
        "kis_step": {k: v for k, v in (step_log.get("broker") or {}).items()
                     if k in ("off", "send", "sent", "planned", "not_sent", "reconciled", "error")},
        "checks": desk.checks(
            halted=_멈춤(client), paper=p, broker=step_log.get("broker"), recon_bad=검산틀림, warnings=len(warnings),
            h_orders=len(h주문),
        ),
    }  # fmt: skip
    kis = client.execute(KIS_SQL, [bmj.BROKER]).dicts()
    요약 = (_json(kis[0]["summary_json"]) or {}) if kis else {}
    판 = [{"account": r["account_id"], "version": r["rule_version"], "first": r["first"], "last": r["last"],
          "orders": r["n"]} for r in client.execute(RULES_SQL).dicts()]  # fmt: skip
    공통 = {"leaderboard": 순위, "rule_history": desk.rule_history(판, 계열), "mirror_account": h, "updated_at": 지금,
          "last_market": market,
          "kis_account": {"as_of": kis[0]["as_of"] if kis else None, "account_krw": 요약.get("account_krw"),
                          "perf": 요약.get("perf")}}  # fmt: skip
    앞 = client.execute(DESK_READ_SQL, [as_of]).rows
    그날 = _json(앞[0][0]) if 앞 else None
    if 그날 is None:
        # 그날 줄을 처음 연다 — 직전 줄의 시장 칸만 이어받는다(순위·KIS 는 이번 것)
        전 = client.execute(DESK_PREV_SQL, [as_of]).rows
        그날 = {"markets": ((_json(전[0][0]) if 전 else None) or {}).get("markets") or {}}
    합 = desk.merge(그날, market, 시장칸, 공통)
    client.execute(DESK_UPSERT_SQL, [as_of, json.dumps(합, ensure_ascii=False), 지금])
    return {"as_of": as_of, "accounts": len(순위["rows"]), "checks": len(시장칸["checks"])}


def _json(text: Any) -> dict[str, Any] | None:
    """깨진 JSON 은 없는 셈 — 그날 줄 하나가 깨져 운용 요약이 날마다 실패하지 않게(25.1503, 검토 7)"""
    try:
        v = json.loads(text) if text else None
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _멈춤(client: Any) -> bool:
    값 = db.get_setting(client, "paper_halt", None)
    return bool(값.get("on")) if isinstance(값, dict) else bool(값)
