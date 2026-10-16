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

TWR_SQL = "SELECT account_id, as_of, twr_index, recon_ok FROM paper_daily ORDER BY account_id, as_of"
#: 위험판 (25.1508) — 계좌마다 마지막 평가 한 줄(기본키 (account_id, as_of) 로 계좌마다 하나)
LATEST_SQL = (
    "SELECT d.account_id, d.equity_krw, d.holdings_krw, d.positions_json FROM paper_accounts a JOIN paper_daily d"
    " ON d.account_id = a.id AND d.as_of = (SELECT MAX(x.as_of) FROM paper_daily x WHERE x.account_id = a.id)"
)
STOCK_NAMES_SQL = (
    "SELECT id, COALESCE(name_ko, name_en, ticker) AS name FROM stocks WHERE id IN (SELECT value FROM json_each(?))"
)
#: 관문 — 최근 창의 일일 배치 실패 수(색인 job_name, started_at)
BATCH_FAILED_SQL = (
    "SELECT COUNT(*) FROM batch_runs WHERE job_name IN (?, ?) AND started_at >= ? AND status = 'failed'"
)
NAMES_SQL = "SELECT id, name, rule_version FROM paper_accounts"
#: 판 이력 (25.1505) — 계좌마다 판을 처음 본 결정일. 몇십 행
RULE_VERSIONS_SQL = "SELECT account_id, rule_version, since FROM paper_rule_versions"
RULE_VERSION_INSERT_SQL = (
    "INSERT OR IGNORE INTO paper_rule_versions (account_id, rule_version, since, noted_at) VALUES (?, ?, ?, ?)"
)
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
#: 판마다 주문 수 — 주문 원장을 (계좌, 판)으로 묶는다. 주문 원장 전체를 훑는다(1년 수만 행 — 실행마다).
#: 커지면 판이 바뀐 날만 읽게 줄인다
RULES_SQL = (
    "SELECT account_id, rule_version, MIN(decided_on) AS first, MAX(decided_on) AS last, COUNT(*) AS n"
    " FROM paper_orders GROUP BY account_id, rule_version"
)
DESK_READ_SQL = "SELECT summary_json FROM paper_desk WHERE as_of = ?"
DESK_PREV_SQL = "SELECT summary_json FROM paper_desk WHERE as_of < ? ORDER BY as_of DESC LIMIT 1"
DESK_LAST_SQL = "SELECT summary_json FROM paper_desk ORDER BY as_of DESC LIMIT 1"
DESK_UPSERT_SQL = (
    "INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES (?, ?, ?)"
    " ON CONFLICT (as_of) DO UPDATE SET summary_json = excluded.summary_json, updated_at = excluded.updated_at"
)
#: 주문 사유는 한 줄로 자른다 — 화면 칸
REASON_CHARS = 80


def _기초(client: Any) -> tuple[dict[str, list[tuple[str, float]]], set[str], list[dict[str, Any]], dict[str, Any]]:
    """평가 계열·검산 어긋난 날·계좌·순위 — 운용 요약과 아침 운용 보고가 같이 쓴다"""
    계열: dict[str, list[tuple[str, float]]] = {}
    검산틀린날: set[str] = set()
    for r in client.execute(TWR_SQL).dicts():
        if r["twr_index"] is not None:
            계열.setdefault(str(r["account_id"]), []).append((str(r["as_of"]), float(r["twr_index"])))
        if r.get("recon_ok") == 0:
            검산틀린날.add(str(r["as_of"]))
    계좌들 = client.execute(NAMES_SQL).dicts()
    이름 = {str(r["id"]): str(r["name"] or r["id"]) for r in 계좌들}
    return 계열, 검산틀린날, 계좌들, desk.leaderboard(계열, 이름)


def _시장칸(
    client: Any, market: str, 지금: str, *, trade_date: str, session_date: str, step_log: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    """이 시장 칸 — 이 실행이 판정한 체결·낸 주문·KIS 주문·확인할 일"""
    from datetime import date, timedelta

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
    return {
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
        # 지주사(H) 그날 기록 — 회사별 가중치·표본·추천/자격/고름 수.
        # 연구실이 날마다 쌓인 이 칸으로 가중치 추이를 그린다(25.1506)
        "holding": next((v["holding"] for a, v in (p.get("accounts") or {}).items()
                         if a == h and isinstance(v, dict) and isinstance(v.get("holding"), dict)), None),
        "kis_step": {k: v for k, v in (step_log.get("broker") or {}).items()
                     if k in ("off", "send", "sent", "planned", "not_sent", "reconciled", "error")},
        "checks": desk.checks(
            halted=_멈춤(client), paper=p, broker=step_log.get("broker"), recon_bad=검산틀림, warnings=len(warnings),
            h_orders=len(h주문),
        ),
    }  # fmt: skip


def report_head(
    client: Any, market: str, now: datetime, *, trade_date: str, session_date: str, step_log: dict[str, Any],
    warnings: list[str], after_close: bool = False,
) -> list[str]:
    """아침(미국은 밤) 리포트 머리의 운용 보고 (docs/desk.md 7장, 25.1510) — 가상모의투자 단계 바로 뒤, 보내기 전.

    KIS모의투자는 리포트를 보낸 뒤 접수 시간에 내므로 이번 KIS 칸은 아직 비었다 — 지난 실행이 보낸 결과를 대신 싣고,
    이번에 내는지는 멈춤·키·장 끝난 뒤 손 실행을 보고 말한다(25.1512 검토 1 — 늘 "냅니다" 라 했다).
    읽기: 평가 계열(운용 요약과 같은 것을 한 번 더 — 열 계좌 1년 약 3천 행)·체결·주문·운용 요약 마지막 줄
    """
    from batch.sources import kis_vts as kv

    _계열, _틀린날, 계좌들, 순위 = _기초(client)
    칸 = _시장칸(client, market, now.astimezone(UTC).isoformat(), trade_date=trade_date, session_date=session_date,
               step_log=step_log, warnings=warnings)  # fmt: skip
    h = bmj.MIRROR_ACCOUNT
    if _멈춤(client):
        상태 = "멈춤 스위치가 켜져 있어 내지 않습니다"
    elif not kv.configured():
        # 셋 다 비면 연결하지 않은 것(정상), 일부만이면 설정 실수 (25.1492)
        상태 = "꺼짐(" + ("키 없음" if len(kv.missing()) >= 3 else "키 일부만 있음 — 설정 확인") + ")"
    elif h not in {str(r["id"]) for r in 계좌들}:
        상태 = f"꺼짐(베낄 계좌 {h} 없음)"
    elif after_close:
        상태 = "장 끝난 뒤 실행이라 오늘은 내지 않습니다"
    else:
        상태 = "이 주문을 접수 시간에 그대로 냅니다"
    지난 = client.execute(DESK_LAST_SQL).rows
    지난칸 = (((_json(지난[0][0]) if 지난 else None) or {}).get("markets") or {}).get(market)
    지난kis = list(지난칸.get("kis_orders") or []) if isinstance(지난칸, dict) else None
    return desk.report_lines(칸, 순위, h, prev_kis=지난kis, kis_state=상태)


def write(
    client: Any, market: str, now: datetime, *, trade_date: str, session_date: str, step_log: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    """그날 운용 요약을 합쳐 적고, 실행 기록에 넣을 건수를 돌려준다."""
    as_of = now.astimezone(cal.market_tz("KR")).date().isoformat()
    지금 = now.astimezone(UTC).isoformat()
    계열, 검산틀린날, 계좌들, 순위 = _기초(client)
    h = bmj.MIRROR_ACCOUNT
    시장칸 = _시장칸(client, market, 지금, trade_date=trade_date, session_date=session_date, step_log=step_log,
                 warnings=warnings)  # fmt: skip
    kis = client.execute(KIS_SQL, [bmj.BROKER]).dicts()
    요약 = (_json(kis[0]["summary_json"]) or {}) if kis else {}
    kis_us = client.execute(KIS_SQL, [bmj.BROKER_US]).dicts()
    요약_us = (_json(kis_us[0]["summary_json"]) or {}) if kis_us else {}
    판이력, 판오류 = _판이력(client, 계좌들, 계열, trade_date, 지금)
    # KIS 마지막 줄이 `KIS_RECENT_DAYS` 보다 오래되면 실현율에 넣지 않는다(25.1511 검토 7)
    from datetime import timedelta

    최근 = (now - timedelta(days=desk.KIS_RECENT_DAYS)).astimezone(cal.market_tz("KR")).date().isoformat()
    kis요약 = [x for rows, x in ((kis, 요약), (kis_us, 요약_us)) if rows and str(rows[0]["as_of"]) >= 최근]
    지금판 = (판이력.get(h) or [{}])[-1]
    관문판, 관문오류 = _관문판(client, now, 계열, h, 검산틀린날, kis요약, since=str(지금판.get("from") or ""),
                         version=지금판.get("version"))  # fmt: skip
    공통 = {"leaderboard": 순위, "rule_history": 판이력, "mirror_account": h, "updated_at": 지금, "last_market": market,
          **관문판,
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
    out = {"as_of": as_of, "accounts": len(순위["rows"]), "checks": len(시장칸["checks"])}
    return out | ({"rule_history_error": 판오류} if 판오류 else {}) | ({"gate_error": 관문오류} if 관문오류 else {})


def _관문판(
    client: Any, now: datetime, 계열: dict[str, list[tuple[str, float]]], h: str, 검산틀린날: set[str],
    kis요약: list[dict[str, Any]], *, since: str, version: int | None,
) -> tuple[dict[str, Any], str | None]:
    """실전 전환 관문·위험판·챔피언 교체 후보 (docs/desk.md 6장, 25.1508) — 실패해도 운용 요약은 적는다.

    실패하면 세 칸을 비우고 `gate_error` 를 적는다 — 그날 앞 실행의 값이 새 갱신 시각 아래 남거나 카드가 말없이
    사라지지 않게(25.1511 검토 5). 성공하면 `gate_error` 를 None 으로 덮는다
    """
    from datetime import timedelta

    from batch.core import settings_range as sr
    from batch.jobs.daily import job_name
    from batch.services.signals import DEFAULT_TARGETS

    try:
        창 = (now - timedelta(days=desk.GATE_INCIDENT_DAYS)).astimezone(cal.market_tz("KR")).date()
        창 = 창.isoformat()
        실패 = int(client.execute(BATCH_FAILED_SQL, [job_name("KR"), job_name("US"), 창]).rows[0][0] or 0)
        관문 = desk.gate(계열, h, since=since, version=version, recon_bad_days=sum(1 for d in 검산틀린날 if d >= 창),
                       batch_failed=실패, kis=desk.combine_kept(kis요약))  # fmt: skip
        마지막 = client.execute(LATEST_SQL).dicts()
        보유들 = {str(r["account_id"]): (_json_list(r["positions_json"])) for r in 마지막}
        ids = sorted({int(q["stock_id"]) for v in 보유들.values() for q in v if q.get("stock_id") is not None})
        이름 = ({int(r["id"]): str(r["name"]) for r in client.execute(STOCK_NAMES_SQL, [json.dumps(ids)]).dicts()}
              if ids else {})  # fmt: skip
        목표, _경고 = sr.기간별_목표(db.get_setting(client, "horizon_targets", None))
        손절 = {k: float(((목표 or {}).get(k) or v)["stop_pct"]) for k, v in DEFAULT_TARGETS.items()}
        latest = {}
        for r in 마지막:
            a = str(r["account_id"])
            종목 = [{**q, "name": 이름.get(int(q.get("stock_id") or 0))} for q in 보유들[a]]
            latest[a] = {"equity": r["equity_krw"], "holdings": r["holdings_krw"], "positions": 종목}
        판 = {"gate": 관문, "risk": desk.risk_board(latest, 계열, 손절), "champion": desk.champion(계열, h),
             "gate_error": None}  # fmt: skip
        return 판, None
    except Exception as exc:  # noqa: BLE001 — 곁다리 안의 곁다리
        글 = f"{type(exc).__name__}: {str(exc)[:120]}"
        return {"gate": None, "risk": [], "champion": [], "gate_error": 글}, 글


def _json_list(text: Any) -> list[dict[str, Any]]:
    try:
        v = json.loads(text) if text else []
    except ValueError:
        return []
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _판이력(
    client: Any, 계좌들: list[dict[str, Any]], 계열: dict[str, list[tuple[str, float]]], trade_date: str, 지금: str,
) -> tuple[dict[str, Any], str | None]:
    """판 이력 표를 채우고 판 이력을 낸다 — 실패해도 운용 요약은 적는다(판 이력만 비움, 25.1505 검토 8)."""
    try:
        알던 = [(str(r["account_id"]), int(r["rule_version"]), str(r["since"]))
              for r in client.execute(RULE_VERSIONS_SQL).dicts()]  # fmt: skip
        지금판 = {str(r["id"]): int(r["rule_version"]) for r in 계좌들 if r.get("rule_version") is not None}
        새 = desk.new_versions(지금판, 알던, trade_date)
        for a, v, since in 새:
            client.execute(RULE_VERSION_INSERT_SQL, [a, v, since, 지금])
        주문수 = {(str(r["account_id"]), int(r["rule_version"])): int(r["n"])
                for r in client.execute(RULES_SQL).dicts()}  # fmt: skip
        return desk.rule_history(알던 + 새, 계열, 주문수), None
    except Exception as exc:  # noqa: BLE001 — 곁다리 안의 곁다리
        return {}, f"{type(exc).__name__}: {str(exc)[:120]}"


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
