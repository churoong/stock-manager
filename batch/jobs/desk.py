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
from datetime import UTC, date, datetime, timedelta
from typing import Any

from batch.core import calendar as cal
from batch.core import db
from batch.jobs import broker_mirror as bmj
from batch.services import desk
from batch.services import paper as pp

#: 관문 R 대비 옆 원가·묵은 평가 비중(25.1709) — H·R1·R2 의 지금 판 구간 줄만(보유 칸을 읽는다)
VALUATION_SQL = (
    "SELECT account_id, as_of, equity_krw, positions_json FROM paper_daily"
    " WHERE account_id IN (?, ?, ?) AND as_of >= ? ORDER BY account_id, as_of"
)
TWR_SQL = (
    "SELECT account_id, as_of, twr_index, recon_ok, equity_krw, pnl_krw, invested_krw, kospi_krw, sp500_krw, cash_krw"
    " FROM paper_daily ORDER BY account_id, as_of"
)
#: 대보기(25.1516) — KIS 계좌 전체 원화 총액 계열(국내 줄, 하루 한 행). **같은 기준(`basis`)의 줄만**(25.1528) —
#: 25.1527 이 총액에 해외 총자산을 더하면서 예전 줄과 섞으면 시작·끝 차이에 해외 자산 약 3.4억원이
#: "KIS 손익" 으로 들어간다
KIS_TOTALS_SQL = (
    "SELECT as_of, json_extract(summary_json, '$.account_krw.total_krw') AS total,"
    " json_extract(summary_json, '$.account_krw.cash_krw') AS cash,"
    " json_extract(summary_json, '$.account_krw.usd_krw') AS fx"
    " FROM paper_broker_daily"
    " WHERE broker = ? AND account_id = ? AND json_extract(summary_json, '$.account_krw.basis') = ?"
    " ORDER BY as_of"
)
#: 대보기 끝 후보 — 미국 일일 배치가 끝난 한국 날짜(25.1517). KIS 성과의 H 견주기와 같은 질의(25.1543)
US_DONE_SQL = bmj.US_DONE_SQL
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
#: `CROSS JOIN` 으로 계좌(몇 줄)부터 돈다 — 그냥 JOIN 이면 계획기가 평가 표 전체를 훑었다(25.1571, 운용 감사 [권고] 5)
#: 성과 분해(관문의 결정 단위 증거·챔피언)는 **적힌** 마지막 것(25.1690, 결정 원장 감사 [권고]) — 그날 성과 분해가
#: 실패해 마지막 행이 비면 선정 칸이 "모름" 이 되고 챔피언 후보가 모두 빠졌다(웹 /paper·연구실은 이미 적힌 것을 읽는다).
#: 기본키 (account_id, as_of) 를 거꾸로 훑어 대개 한 행에서 멈춘다
LATEST_SQL = (
    "SELECT d.account_id, d.equity_krw, d.holdings_krw, d.positions_json, a.rule_version,"
    " (SELECT y.attribution_json FROM paper_daily y WHERE y.account_id = a.id AND y.attribution_json IS NOT NULL"
    "  ORDER BY y.as_of DESC LIMIT 1) AS attribution_json"
    " FROM paper_accounts a CROSS JOIN paper_daily d"
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
#: 관문 — 최근 창의 일일 배치 기록(색인 job_name, started_at).
#: **사고 날 = 그 시장 거래일 가운데 성공 기록이 없거나 실패가
#: 있던 날**(`desk.batch_incident_days`). 실행 기록이 아니라 거래일로 센다 — Turso 한도로 막히면 첫 읽기에서 깨져
#: 실행 기록 행이 아예 남지 않는다(25.1572, 25.1571 반박 [필수]: 예전엔 `failed` 행만, 25.1571 은 건너뜀 행까지 셌지만
#: 행이 없는 날을 못 봤다)
#: 가상모의투자 단계 결과도 같은 행에서(읽기 행 +0) — 단계가 예외·잠금으로 끝나도 실행은 `partial` 이라 무사고로 셌다
#: (25.1636). 깨진 `step_log` 한 행이 관문 전체를 오류로 만들지 않게 `json_valid` 로 가른다(25.1633 의 같은 교훈)
BATCH_RUNS_SQL = (
    "SELECT job_name, trade_date, status,"
    " CASE WHEN json_valid(step_log) THEN json_extract(step_log, '$.paper.error') END AS paper_error,"
    " CASE WHEN json_valid(step_log) THEN json_extract(step_log, '$.paper.skipped') END AS paper_skipped,"
    " CASE WHEN json_valid(step_log) THEN json_extract(step_log, '$.paper.account_errors') END AS paper_account_errors"
    " FROM batch_runs WHERE job_name IN (?, ?) AND started_at >= ?"
)
NAMES_SQL = "SELECT id, name, rule_version FROM paper_accounts"


def _expected_trade_dates(market: str, since: str, now: datetime) -> list[str]:
    """창(`since` 이후) 안에서 그 시장 일일 배치가 다뤘어야 할 거래일들 — 장날 S 마다 `previous_session(S)`
    (배치는 장 전에
    직전 거래일을 다룬다, `cal.decide`). 오늘 장날은 뺀다(오늘 실행은 지금 도는 중이거나 아직이다)"""
    오늘 = now.astimezone(cal.market_tz(market)).date()
    out: list[str] = []
    d = date.fromisoformat(since)
    while d < 오늘:
        if cal.is_session(market, d):
            out.append(cal.previous_session(market, d).isoformat())
        d += timedelta(days=1)
    return out
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
#: 오늘 이하에서 판정 시점 기록(`gate.looks`, 관문을 비운 줄은 `gate_carry.looks`)이 있는 가장 늦은 운용 요약 —
#: 관문을 이어받는다(25.1963). 관문을 비운 실행(판오류·관문오류) 한 줄이 끼어도 기록이 끊기지 않게(25.1967,
#: 25.1963 교차검증 [필수]). 깨진 JSON 줄은 `json_valid` 로 건너뛴다(CASE 는 앞 조건이 거짓이면 뒤를 셈하지 않는다)
#: 기록은 배열일 때만 — `_앞관문` 과 같은 규칙(25.1970, 배열이 아닌 줄을 골라 처음부터 판정하지 않게)
DESK_UPTO_SQL = (
    "SELECT summary_json FROM paper_desk WHERE as_of <= ? AND CASE WHEN json_valid(summary_json) THEN"
    " json_type(summary_json, '$.gate.looks') = 'array' OR json_type(summary_json, '$.gate_carry.looks') = 'array'"
    " END ORDER BY as_of DESC LIMIT 1"
)
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
    이름 = {str(r["id"]): pp.account_name(str(r["id"])) for r in 계좌들}  # 화면 이름(25.1893 — DB 이름은 "가상 A (…)")
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
        상태 = f"꺼짐(베낄 계좌 {pp.account_name(h)} 없음)"
    elif after_close:
        상태 = "장 끝난 뒤 실행이라 오늘은 내지 않습니다"
    elif 막힘 := bmj.send_block(market, now):
        # KIS 단계와 같은 판정(25.1631) — 예약 마감 뒤·접수 시작 전이면 "냅니다" 가 거짓이었다. 리포트를 보낸 몇 분 뒤
        # 단계가 돌므로 경계 몇 분은 어긋날 수 있다 — 그날 실제 결과는 다음 운용 보고의 "지난 실행" 이 말한다
        상태 = f"오늘은 내지 않습니다 — {막힘.split(' — ')[0].removeprefix('오늘은 ')}"
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
    # 관문 판정 시점 기록을 이어받는다(25.1963) — 오늘 이하 가장 늦은 운용 요약의 관문. 읽기가 깨지면(JSON 함수·연결)
    # 운용 요약 전체를 잃지 않고 관문만 비운다 — 앞 기록 없이 판정하면 지난 시점을 다시 판정한다(25.1971)
    앞오류: str | None = None
    try:
        앞줄 = client.execute(DESK_UPTO_SQL, [as_of]).rows
        앞관문 = _앞관문((_json(앞줄[0][0]) if 앞줄 else None) or {})
    except Exception as exc:  # noqa: BLE001 — 까닭은 gate_error 로 화면에
        앞관문, 앞오류 = None, f"{type(exc).__name__}: {str(exc)[:120]}"
    관문판, 관문오류 = _관문판(client, now, 계열, h, 검산틀린날, kis요약, since=str(지금판.get("from") or ""),
                         version=지금판.get("version"), index=그림자.get(h), cash=현금.get(h),
                         control_cash={r: 현금.get(r) or [] for r in desk.CONTROL_IDS},
                         valuation=_멈춘평가(client, h, str(지금판.get("from") or "")),
                         판시작={a: str((줄 or [{}])[-1].get("from") or "") for a, 줄 in 판이력.items()},
                         판버전={a: int((줄 or [{}])[-1].get("version") or 1) for a, 줄 in 판이력.items()},
                         그림자=그림자, 앞관문=앞관문)  # fmt: skip
    if 판오류:
        # 판 이력을 못 읽으면 "지금 판" 의 시작을 모른다 — since='' 로 관문을 내면 모든 판의 평가를 지금 판으로
        # 섞어 셌다(25.1573, 운용 감사 [참고]). 관문을 비우고 까닭을 화면(gate_error)에 올린다.
        # 챔피언 후보도 비운다 — 판 시작을 모르면 옛 판 성적까지 섞어 블록 규칙을 넘었다(25.1610, 운용 요약 감사
        # [필수] 3 — 예전 주석 "판과 무관" 은 25.1575 가 챔피언을 지금 판 구간으로 좁힌 뒤로 틀렸다).
        # 위험판은 판과 무관해 둔다
        관문오류 = f"판 이력을 읽지 못해 관문을 내지 않음(판을 섞지 않게, 챔피언 후보도 비움) — {판오류}"
        관문판 = {**관문판, "gate": None, "champion": [], "prereg_status": {}, "gate_error": 관문오류,
                "gate_carry": 앞관문}  # fmt: skip
    elif 앞오류:
        # 앞 기록을 모르면 판정 시점을 이어받지 못한다 — 관문만 비우고(다음 실행이 기록 있는 줄을 다시 찾는다) 위험판·
        # 챔피언·사전 등록 값은 둔다(판정 시점과 무관)
        관문오류 = f"앞 관문(판정 시점 기록)을 읽지 못해 관문을 내지 않음(지난 시점을 다시 판정하지 않게) — {앞오류}"
        관문판 = {**관문판, "gate": None, "gate_error": 관문오류, "gate_carry": None}
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
        # 당일 단타(T)는 KIS 체결 보정에서 뺀다 — 하루 회전 약 200% 에 H 가 잰 칸(장 전 시장가·장 열린 뒤 종가)을 곱하면
        # 종가 매도 짝의 체결과 뜻이 다르다(paper.md 16.3.2, 25.1885)
        시작 = {str(r["id"]): str(r["open"] if r.get("young") or r.get("separate") else 순위.get("window_start"))
              for r in 순위.get("rows") or [] if r.get("open") and str(r["id"]) not in pp.SAME_DAY_IDS}  # fmt: skip
        if not 시작:
            return {"fill_adjust": None, "fill_adjust_error": None}
        총액줄 = client.execute(KIS_TOTALS_SQL, [bmj.BROKER, h, bmj.TOTAL_BASIS]).dicts()
        총액 = [(str(r["as_of"]), r["total"]) for r in 총액줄]
        # 쉬는 해외 현금 환율 몫을 빼려고 (25.1543) — {날: (해외 현금 원화, KIS 고시환율)}
        환 = {str(r["as_of"]): (r.get("cash"), r.get("fx")) for r in 총액줄}
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
        대봄 = _대보기(client, h, 창, dict(총액), 평가, 손익, 체결, 첫칸, 환) if 창 else None
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
    환: dict[str, tuple[Any, Any]] | None = None,
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
    # KIS 끝 총액은 쉬는 해외 현금의 환율 몫을 시작 환율에 묶어 견준다(25.1543) — 모르면 원래 총액, `fx_mixed` 로 알린다
    c1, x1 = (환 or {}).get(e, (None, None))
    _c0, x0 = (환 or {}).get(s, (None, None))
    묶음 = bm.fx_neutral_total(float(총액[e]), c1, x0, x1)
    판 = desk.h_check(start=s, end=e, kis0=float(총액[s]), kis1=묶음 if 묶음 is not None else float(총액[e]),
                     h_pnl0=float(p0 or 0.0), h_pnl1=p1, h_equity0=float(e0 or 0.0), h_adj=h보정,
                     pre_link=연결전)  # fmt: skip
    if 판 is not None:
        판["fx_mixed"] = 묶음 is None
        판["fx_cash_krw"] = float(총액[e]) - 묶음 if 묶음 is not None else None
    return 판


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


def _멈춘평가(client: Any, h: str, since: str) -> dict[str, list[tuple[str, float]]] | None:
    """H·R1·R2 의 원가·묵은 평가 비중 계열(25.1709). 곁다리 — 못 읽으면 None(칸이 비고 판정은 예전대로).
    한도 오류는 다시 올린다"""
    try:
        return desk.valuation_gaps(client.execute(VALUATION_SQL, [h, *desk.CONTROL_IDS, since]).dicts())
    except Exception as exc:  # noqa: BLE001
        if db.quota_reason(exc):
            raise
        return None


def _앞관문(줄: dict[str, Any]) -> dict[str, Any] | None:
    """이어받을 관문 — 그 줄의 관문에 판정 시점 기록이 있으면 그것, 관문을 비운 줄이면 그 실행이 받았던 관문
    (`gate_carry`, 25.1967). 둘 다 없으면 None(처음부터)"""
    for 키 in ("gate", "gate_carry"):
        g = 줄.get(키)
        if isinstance(g, dict) and isinstance(g.get("looks"), list):
            return g
    return None


def _관문판(
    client: Any, now: datetime, 계열: dict[str, list[tuple[str, float]]], h: str, 검산틀린날: set[str],
    kis요약: list[dict[str, Any]], *, since: str, version: int | None,
    index: list[tuple[str, float | None, float | None, float]] | None = None,
    cash: list[tuple[str, float, float]] | None = None, 판시작: dict[str, str] | None = None,
    control_cash: dict[str, list[tuple[str, float, float]]] | None = None,
    valuation: dict[str, list[tuple[str, float]]] | None = None,
    판버전: dict[str, int] | None = None,
    그림자: dict[str, list[tuple[str, float | None, float | None, float]]] | None = None,
    앞관문: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """실전 전환 관문·위험판·챔피언 교체 후보 (docs/desk.md 6장, 25.1508) — 실패해도 운용 요약은 적는다.

    실패하면 세 칸을 비우고 `gate_error` 를 적는다 — 그날 앞 실행의 값이 새 갱신 시각 아래 남거나 카드가 말없이
    사라지지 않게(25.1511 검토 5). 성공하면 `gate_error` 를 None 으로 덮는다
    """
    from datetime import timedelta

    from batch.core import settings_range as sr
    from batch.jobs.daily import job_name
    from batch.services.signals import DEFAULT_TARGETS, rules_for

    try:
        창 = (now - timedelta(days=desk.GATE_INCIDENT_DAYS)).astimezone(cal.market_tz("KR")).date()
        창 = 창.isoformat()
        기대 = {job_name(m): _expected_trade_dates(m, 창, now) for m in ("KR", "US")}
        # 실행은 거래일 다음 장날 아침이라 started_at 창을 며칠 앞당겨 읽는다(긴 연휴 ≤ 11일)
        앞당김 = (date.fromisoformat(창) - timedelta(days=12)).isoformat()
        실행들 = client.execute(BATCH_RUNS_SQL, [job_name("KR"), job_name("US"), 앞당김]).dicts()
        실패 = desk.batch_incident_days(실행들, 기대)
        마지막 = client.execute(LATEST_SQL).dicts()
        # 결정 단위 선정 증거 — 가상 단계가 성과 분해와 함께 마지막 평가 행에 적은 것(25.1518, 읽기 0)
        # 대신 읽은 성과 분해가 **지금 판** 것일 때만(25.1696, 25.1690 교차검증 [권고]) — 판을 올린 날부터 성과 분해가
        # 실패하면 옛 판의 증거로 "지금 판" 선정 칸이 통과할 수 있었다. 판을 모르는 옛 행(25.1602 전)은 판 1
        증거 = {str(r["account_id"]): _this_version_evidence(r) for r in 마지막}
        관문 = desk.gate(계열, h, since=since, version=version, recon_bad_days=sum(1 for d in 검산틀린날 if d >= 창),
                       batch_failed=실패, kis=desk.combine_kept(kis요약), evidence=증거.get(h),
                       index=index, cash=cash, control_cash=control_cash, valuation=valuation,
                       prev=앞관문)  # fmt: skip
        보유들 = {str(r["account_id"]): (_json_list(r["positions_json"])) for r in 마지막}
        ids = sorted({int(q["stock_id"]) for v in 보유들.values() for q in v if q.get("stock_id") is not None})
        이름 = ({int(r["id"]): str(r["name"]) for r in client.execute(STOCK_NAMES_SQL, [json.dumps(ids)]).dicts()}
              if ids else {})  # fmt: skip
        목표, _경고 = sr.기간별_목표(db.get_setting(client, "horizon_targets", None))
        # 칸별로 덮는다 — 가상모의투자 매도와 같은 식(`signals.rules_for`, 25.1748)
        손절 = {k: float(rules_for(k, 목표)["stop_pct"]) for k in DEFAULT_TARGETS}
        latest = {}
        for r in 마지막:
            a = str(r["account_id"])
            종목 = [{**q, "name": 이름.get(int(q.get("stock_id") or 0))} for q in 보유들[a]]
            latest[a] = {"equity": r["equity_krw"], "holdings": r["holdings_krw"], "positions": 종목}
        늦음창 = (now - timedelta(days=desk.READY_WINDOW_DAYS)).astimezone(cal.market_tz("KR")).date().isoformat()
        늦음 = client.execute(BATCH_DELAY_SQL, [desk.READY_LATE_SEC, job_name("KR"), job_name("US"), 늦음창]).dicts()
        # 판마다 사전 등록 지표의 오늘 값(25.1710) — 날마다 이 줄에 남아 판정 시점의 값을 나중에 그대로 읽는다.
        # 따로 감싼다 — 곁다리 기록의 실패가 H 관문·위험판까지 지우지 않게(25.1711, 25.1710 교차검증 [권고])
        try:
            판들 = {a: (int(v), 판시작.get(a, "")) for a, v in (판버전 or {}).items()}
            사전: dict[str, Any] = desk.prereg_status(계열, 판들, 증거, 그림자 or {})
        except Exception as exc:  # noqa: BLE001
            사전 = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        판 = {"gate": 관문, "risk": desk.risk_board(latest, 계열, 손절), "prereg_status": 사전,
             "champion": desk.champion(계열, h, 증거, 판시작),
             "readiness": desk.readiness(runs=늦음[0] if 늦음 else None, kis=kis요약), "gate_error": None,
             "gate_carry": None}  # fmt: skip
        return 판, None
    except Exception as exc:  # noqa: BLE001 — 곁다리 안의 곁다리
        글 = f"{type(exc).__name__}: {str(exc)[:120]}"
        return {"gate": None, "risk": [], "champion": [], "readiness": [], "prereg_status": {}, "gate_error": 글,
                "gate_carry": 앞관문}, 글  # fmt: skip


def _this_version_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """`LATEST_SQL` 한 줄 → 결정 단위 증거. 성과 분해의 판이 계좌의 지금 판과 다르면 빈 것(모름) (25.1696)"""
    a = _json(row.get("attribution_json")) or {}
    return (a.get("evidence") or {}) if a.get("rule_version", 1) == int(row.get("rule_version") or 1) else {}


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
