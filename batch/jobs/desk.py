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

TWR_SQL = (
    "SELECT account_id, as_of, twr_index, recon_ok, equity_krw, pnl_krw, invested_krw, kospi_krw, sp500_krw, cash_krw"
    " FROM paper_daily ORDER BY account_id, as_of"
)
#: 대보기(25.1516) — KIS 계좌 전체 원화 총액 계열(국내 줄, 하루 한 행). **같은 기준(`basis`)의 줄만**(25.1528) —
#: 25.1527 이 총액에 해외 총자산을 더하면서 예전 줄과 섞으면 시작·끝 차이에 해외 자산 약 3.4억원이
#: "KIS 손익" 으로 들어간다
KIS_TOTALS_SQL = (
    "SELECT as_of, json_extract(summary_json, '$.account_krw.total_krw') AS total FROM paper_broker_daily"
    " WHERE broker = ? AND account_id = ? AND json_extract(summary_json, '$.account_krw.basis') = ?"
    " ORDER BY as_of"
)
#: 대보기 끝 후보 — 미국 일일 배치가 끝난 한국 날짜(그날 가상 평가 행을 미국 종가로 덮었다, 25.1517)
US_DONE_SQL = (
    "SELECT DISTINCT date(started_at, '+9 hours') AS d FROM batch_runs WHERE job_name = ?"
    " AND status IN ('success', 'partial') AND started_at >= ?"
)
#: 대보기 시작을 찾느라 훑는 날 수 상한 — 날마다 잔고 두 줄·평가 한 줄을 읽는다
H_START_SCAN = 60
#: 대보기 시작일에 KIS 가 든 종목 — 그날 이하 마지막 잔고 줄(국내·미국)
KIS_HOLD_AT_SQL = (
    "SELECT holdings_json FROM paper_broker_daily WHERE broker = ? AND as_of <= ? ORDER BY as_of DESC LIMIT 1"
)
H_POS_AT_SQL = (
    "SELECT positions_json FROM paper_daily WHERE account_id = ? AND as_of <= ? ORDER BY as_of DESC LIMIT 1"
)
#: 체결 보정(25.1514) — 순위 구간 체결에 주문 유형·판정 잣대·지정가·그날 시가를 붙인다
#: (체결 색인 (account_id, fill_date), 시세 유일 색인 (stock_id, date)). 시작부터 누적이라 1년이면 수천 행 —
#: 커지면 계좌별 합계만 SQL 로 낸다
ADJ_FILLS_SQL = (
    "SELECT f.account_id, o.market, f.side, o.order_type, o.fill_basis, o.limit_price, f.fill_date, f.price,"
    " f.quantity, f.fx_rate, p.open, p.close FROM paper_fills f JOIN paper_orders o ON o.id = f.order_id"
    " LEFT JOIN prices p ON p.stock_id = f.stock_id AND p.date = f.fill_date"
    " WHERE f.account_id IN (SELECT id FROM paper_accounts) AND f.fill_date >= ?"
)
#: 위험판 (25.1508) — 계좌마다 마지막 평가 한 줄(기본키 (account_id, as_of) 로 계좌마다 하나)
LATEST_SQL = (
    "SELECT d.account_id, d.equity_krw, d.holdings_krw, d.positions_json, d.attribution_json"
    " FROM paper_accounts a JOIN paper_daily d"
    " ON d.account_id = a.id AND d.as_of = (SELECT MAX(x.as_of) FROM paper_daily x WHERE x.account_id = a.id)"
)
STOCK_NAMES_SQL = (
    "SELECT id, COALESCE(name_ko, name_en, ticker) AS name FROM stocks WHERE id IN (SELECT value FROM json_each(?))"
)
#: 실전 준비 목록 — 최근 창의 일일 배치 늦음(색인 job_name, started_at). 분모는 **지연을 잰 실행만**
#: (`COUNT(delay_seconds)`) — 손 실행·예약 없는 실행·정리된 실행은 지연이 비어 "N회 중" 을 부풀렸다(25.1516, 검토 6)
BATCH_DELAY_SQL = (
    "SELECT COUNT(delay_seconds) AS n, SUM(CASE WHEN delay_seconds > ? THEN 1 ELSE 0 END) AS late,"
    " MAX(delay_seconds) AS max_delay"
    " FROM batch_runs WHERE job_name IN (?, ?) AND started_at >= ? AND status != 'running'"
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
    계열, 검산틀린날, 계좌들, 순위, _평가, _손익, _그림자, _현금 = _기초_평가(client)
    return 계열, 검산틀린날, 계좌들, 순위


def _기초_평가(
    client: Any,
) -> tuple[dict[str, list[tuple[str, float]]], set[str], list[dict[str, Any]], dict[str, Any],
           dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]],
           dict[str, list[tuple[str, float | None, float | None, float]]],
           dict[str, list[tuple[str, float, float]]]]:  # fmt: skip
    """`_기초` + 계좌마다 원화 평가 총액 계열(체결 보정의 회전율 분모, 25.1514)·평가손익 계열(대보기, 25.1516)
    — 같은 한 번의 읽기"""
    계열: dict[str, list[tuple[str, float]]] = {}
    평가: dict[str, list[tuple[str, float]]] = {}
    손익: dict[str, list[tuple[str, float]]] = {}
    # 지수 그림자 계좌(같은 날 같은 돈을 KOSPI·S&P500 에 넣었다면, docs/paper.md 4장) — 관문 "지수 대비"(25.1519)
    그림자: dict[str, list[tuple[str, float | None, float | None, float]]] = {}
    검산틀린날: set[str] = set()
    # 계좌마다 (as_of, 현금, 평가) — 관문 지수 대비의 현금 환산 참고 칸(25.1540). 같은 행이라 읽는 행 수는 그대로
    현금: dict[str, list[tuple[str, float, float]]] = {}
    for r in client.execute(TWR_SQL).dicts():
        if r["twr_index"] is not None:
            계열.setdefault(str(r["account_id"]), []).append((str(r["as_of"]), float(r["twr_index"])))
        if r.get("equity_krw") is not None:
            평가.setdefault(str(r["account_id"]), []).append((str(r["as_of"]), float(r["equity_krw"])))
        if r.get("pnl_krw") is not None:
            손익.setdefault(str(r["account_id"]), []).append((str(r["as_of"]), float(r["pnl_krw"])))
        if r.get("invested_krw") is not None:
            그림자.setdefault(str(r["account_id"]), []).append(
                (str(r["as_of"]), r.get("kospi_krw"), r.get("sp500_krw"), float(r["invested_krw"])))
        if r.get("cash_krw") is not None and r.get("equity_krw") is not None:
            현금.setdefault(str(r["account_id"]), []).append(
                (str(r["as_of"]), float(r["cash_krw"]), float(r["equity_krw"])))
        if r.get("recon_ok") == 0:
            검산틀린날.add(str(r["as_of"]))
    계좌들 = client.execute(NAMES_SQL).dicts()
    이름 = {str(r["id"]): str(r["name"] or r["id"]) for r in 계좌들}
    return 계열, 검산틀린날, 계좌들, desk.leaderboard(계열, 이름), 평가, 손익, 그림자, 현금


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
    계열, 검산틀린날, 계좌들, 순위, 평가, 손익, 그림자, 현금 = _기초_평가(client)
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
                         version=지금판.get("version"), index=그림자.get(h), cash=현금.get(h))  # fmt: skip
    공통 = {"leaderboard": 순위, "rule_history": 판이력, "mirror_account": h, "updated_at": 지금, "last_market": market,
          **관문판,
          "kis_account": {"as_of": kis[0]["as_of"] if kis else None, "account_krw": 요약.get("account_krw"),
                          "perf": 요약.get("perf")},
          **_보정판(client, 순위, 평가, 손익, h, 요약, 요약_us, as_of, 최근)}  # fmt: skip
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


def _칸합침(client: Any, 요약: dict[str, Any], 요약_us: dict[str, Any], 최근: str = "") -> dict[str, dict[str, Any]]:
    """시장마다 체결 칸 — 첫째(챔피언) 계좌 것에 둘째(도전자, 25.1515) 계좌 것을 칸마다 더한다.

    두 계좌의 같은 칸은 합치지 않고 **거래일이 더 많은 쪽**을 쓴다 — 금액 가중 평균을 합치려면 칸마다 총금액이 있어야
    하는데 요약에 없다. 챔피언이 안 쓰는 칸(시장가 매수 등)은 도전자 것만 있어 그대로 들어온다
    """
    out = {"KR": dict(요약.get("cells") or {}), "US": dict(요약_us.get("cells") or {})}
    for m, 증권사 in (("KR", bmj.CHALLENGER.broker), ("US", bmj.CHALLENGER.broker_us)):
        줄 = client.execute(KIS_SQL, [증권사]).dicts()
        # 오래된 줄은 쓰지 않는다 — 키를 빼거나 도전자를 꺼도 마지막 칸이 영영 보정에 남았다(25.1517 검토 3)
        if not 줄 or str(줄[0]["as_of"]) < 최근:
            continue
        둘째 = (_json(줄[0]["summary_json"]) or {}).get("cells") or {}
        for k, c in 둘째.items():
            if not isinstance(c, dict):
                continue
            지금 = out[m].get(k) or {}
            # 잰 칸이 먼저, 그다음 거래일 수 — 거래일만 많은 안 잰 칸이 잰 칸을 밀어내지 않게
            새것 = (bool(c.get("measured")), int(c.get("days") or 0))
            if 새것 > (bool(지금.get("measured")), int(지금.get("days") or 0)):
                out[m][k] = c
    return out


def _보정판(
    client: Any, 순위: dict[str, Any], 평가: dict[str, list[tuple[str, float]]],
    손익: dict[str, list[tuple[str, float]]], h: str, 요약: dict[str, Any], 요약_us: dict[str, Any], 오늘: str,
    최근: str = "",
) -> dict[str, Any]:
    """KIS모의 체결 기준 보정(근사·참고, docs/desk.md 8장, 25.1514) — 실패해도 운용 요약은 적는다.

    순위 구간(자리 잡은 계좌는 공통 구간 시작, 갓 연 계좌는 제 개설일)마다 회전율 × KIS 칸. H 는 대보기 기간
    (`desk.h_window` — 끝은 어제까지, 25.1516)으로 한 번 더 재어 실측 손익 차이와 대본다
    """
    from batch.services import broker_mirror as bm

    try:
        칸 = _칸합침(client, 요약, 요약_us, 최근)
        시작 = {str(r["id"]): str(r["open"] if r.get("young") else 순위.get("window_start"))
              for r in 순위.get("rows") or [] if r.get("open")}  # fmt: skip
        if not 시작:
            return {"fill_adjust": None, "fill_adjust_error": None}
        총액 = [(str(r["as_of"]), r["total"])
              for r in client.execute(KIS_TOTALS_SQL, [bmj.BROKER, h, bmj.TOTAL_BASIS]).dicts()]  # fmt: skip
        창 = None
        if h in 평가 and 총액:
            from batch.jobs.daily import job_name

            미국 = {str(r[0]) for r in client.execute(US_DONE_SQL, [job_name("US"), min(d for d, _v in 총액)]).rows}
            창 = desk.h_window(총액, 오늘, 미국)
        아래 = min([*시작.values(), *([창[0][0]] if 창 else [])])
        체결: dict[str, list[dict[str, Any]]] = {}
        for r in client.execute(ADJ_FILLS_SQL, [아래]).dicts():
            체결.setdefault(str(r["account_id"]), []).append(r)
        보정 = desk.fill_adjust(fills=체결, equity=평가, starts=시작, cells=칸, min_days=bm.CELL_MIN_DAYS)
        # 대보기는 **첫째(H 자기) 칸만** — 도전자 칸을 섞으면 H 체결로 칸을 검증한다는 뜻이 흐려진다(25.1517 검토 3)
        첫칸 = {"KR": 요약.get("cells") or {}, "US": 요약_us.get("cells") or {}}
        대봄 = _대보기(client, h, 창, dict(총액), 평가, 손익, 체결, 첫칸) if 창 else None
        return {"fill_adjust": {"rows": desk.adjust_rows(순위, 보정), "h_check": 대봄, "min_days": bm.CELL_MIN_DAYS},
                "fill_adjust_error": None}  # fmt: skip
    except Exception as exc:  # noqa: BLE001 — 곁다리 안의 곁다리
        return {"fill_adjust": None, "fill_adjust_error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def _마지막(v: list[tuple[str, float]], d: str) -> float | None:
    앞 = [x for a, x in sorted(v) if a <= d]
    return 앞[-1] if 앞 else None


def _대보기(
    client: Any, h: str, 창: tuple[list[str], str], 총액: dict[str, Any], 평가: dict[str, list[tuple[str, float]]],
    손익: dict[str, list[tuple[str, float]]], 체결: dict[str, list[dict[str, Any]]], 칸: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """H 대보기 (25.1516 검토 1·25.1517 검토 2) — 시작 = 후보 가운데 H 평가가 있고 **연결 전 보유가 없는** 첫날
    (`H_START_SCAN` 날까지 훑고, 끝내 없으면 첫 후보를 쓰되 `pre_link` 를 적어 판정에서 빠지게). 시작이 영영 KIS 첫날로
    고정이면 그날 연결 전 보유가 있었을 때 대보기가 영영 쓸모없었다. 시작·끝 행의 KIS 총액·H 평가손익, 그 사이
    (시작 ≤ 체결일 < 끝) 체결로 다시 잰 보정"""
    from batch.services import broker_mirror as bm

    후보, e = 창
    p1 = _마지막(손익.get(h) or [], e)
    if p1 is None:
        return None
    고른: tuple[str, int] | None = None
    for s in 후보[:H_START_SCAN]:
        if _마지막(손익.get(h) or [], s) is None or not _마지막(평가.get(h) or [], s):
            continue
        n = _연결전(client, h, s)
        if 고른 is None:
            고른 = (s, n)
        if n == 0:
            고른 = (s, 0)
            break
    if 고른 is None:
        return None
    s, 연결전 = 고른
    p0, e0 = _마지막(손익.get(h) or [], s), _마지막(평가.get(h) or [], s)
    h보정 = desk.fill_adjust(fills=체결, equity=평가, starts={h: s}, ends={h: e}, cells=칸,
                           min_days=bm.CELL_MIN_DAYS)[h]  # fmt: skip
    return desk.h_check(start=s, end=e, kis0=float(총액[s]), kis1=float(총액[e]), h_pnl0=float(p0 or 0.0), h_pnl1=p1,
                        h_equity0=float(e0 or 0.0), h_adj=h보정, pre_link=연결전)  # fmt: skip


def _연결전(client: Any, h: str, day: str) -> int:
    """그날 H 가 들고 KIS(국내·미국) 잔고에 없던 종목 수 — 그 종목의 시세 변동은 H 에만 있다"""
    줄 = client.execute(H_POS_AT_SQL, [h, day]).rows
    h종목 = {int(q["stock_id"]) for q in _json_list(줄[0][0] if 줄 else None) if q.get("stock_id") is not None}
    kis종목: set[int] = set()
    for 증권사 in (bmj.BROKER, bmj.BROKER_US):
        k = client.execute(KIS_HOLD_AT_SQL, [증권사, day]).rows
        for q in json.loads(k[0][0] or "[]") if k else []:
            sid = q.get("id") if isinstance(q, dict) else (q[0] if isinstance(q, list) and q else None)
            if isinstance(sid, int):
                kis종목.add(sid)
    return len(h종목 - kis종목)


def _관문판(
    client: Any, now: datetime, 계열: dict[str, list[tuple[str, float]]], h: str, 검산틀린날: set[str],
    kis요약: list[dict[str, Any]], *, since: str, version: int | None,
    index: list[tuple[str, float | None, float | None, float]] | None = None,
    cash: list[tuple[str, float, float]] | None = None,
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
        마지막 = client.execute(LATEST_SQL).dicts()
        # 결정 단위 선정 증거 — 가상 단계가 성과 분해와 함께 마지막 평가 행에 적은 것(25.1518, 읽기 0)
        증거 = {str(r["account_id"]): ((_json(r.get("attribution_json")) or {}).get("evidence") or {}) for r in 마지막}
        관문 = desk.gate(계열, h, since=since, version=version, recon_bad_days=sum(1 for d in 검산틀린날 if d >= 창),
                       batch_failed=실패, kis=desk.combine_kept(kis요약), evidence=증거.get(h),
                       index=index, cash=cash)  # fmt: skip
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
        늦음창 = (now - timedelta(days=desk.READY_WINDOW_DAYS)).astimezone(cal.market_tz("KR")).date().isoformat()
        늦음 = client.execute(BATCH_DELAY_SQL, [desk.READY_LATE_SEC, job_name("KR"), job_name("US"), 늦음창]).dicts()
        판 = {"gate": 관문, "risk": desk.risk_board(latest, 계열, 손절), "champion": desk.champion(계열, h, 증거),
             "readiness": desk.readiness(runs=늦음[0] if 늦음 else None, kis=kis요약), "gate_error": None}  # fmt: skip
        return 판, None
    except Exception as exc:  # noqa: BLE001 — 곁다리 안의 곁다리
        글 = f"{type(exc).__name__}: {str(exc)[:120]}"
        return {"gate": None, "risk": [], "champion": [], "readiness": [], "gate_error": 글}, 글


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
