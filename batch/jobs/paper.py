"""모의 투자 계좌의 하루 (docs/paper.md, docs/infra.md 25.1445).

일일 배치(`jobs/daily.run`)가 리포트를 만든 뒤 부른다 — **곁다리**다. 실패해도 리포트는 그대로 나간다.
순서: ① 지난 주문 체결 판정(그 장날짜의 시세로만) → ② 평가·검산·시간가중·그림자 기준을 `paper_daily` 에 →
③ 이 시장 보유의 매도 판정 → ④ 2부와 같은 함수로 매수 주문. 계좌·시장·거래일 주문이 이미 있으면 ③④ 를 건너뛴다
(재실행 멱등, docs/paper.md 2.4).

`trades`·`positions`·`sell_flags` 를 읽거나 쓰지 않는다(`tests/test_paper_engine_1444.py` 가 본다).
금액은 공개 로그에 찍지 않는다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace as dc_replace
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import Any

from batch.core import calendar as cal
from batch.core import db
from batch.core import settings_range as sr
from batch.services import backtest as bt
from batch.services import company as co
from batch.services import holding as hold
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import risk_gate as rg
from batch.services import sell_flags as sf
from batch.services import stop_vol as sv

log = logging.getLogger(__name__)

ACCOUNTS_SQL = (
    "SELECT id, sell_rule, rule_version, entry_rule, top_n, selection, charter_json, created_at FROM paper_accounts"
    " ORDER BY id"
)
FLOWS_SQL = "SELECT id, flow_date, amount_krw, kind, cancels_id, created_at FROM paper_cash_flows ORDER BY id"
FILLS_SQL = (
    "SELECT order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate, fee, tax, slippage,"
    " cash_krw, horizon, score_at_trade, snapshot_as_of, created_at FROM paper_fills ORDER BY fill_date, id"
)
OPEN_ORDERS_SQL = (
    "SELECT id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency, fill_session,"
    " fill_basis, deferrals, horizon, score_at_order, score_as_of, ref_price, rule_version FROM paper_orders"
    " WHERE status = 'open'"
    " ORDER BY id"
)
BAR_SQL = "SELECT date, open, close, volume, change_pct FROM prices WHERE stock_id = ? AND date = ?"
#: 그 시장 전체에 그날 시세가 들어왔나 — 못 받은 날(배치 실패)과 정지를 가른다 (docs/paper.md 2.3)
MARKET_DAY_SQL = (
    "SELECT 1 FROM prices p CROSS JOIN stocks s ON s.id = p.stock_id WHERE p.date = ? AND s.country = ? LIMIT 1"
)
LAST_CLOSE_SQL = (
    "SELECT close, date FROM prices WHERE stock_id = ? AND date <= ? AND close > 0 ORDER BY date DESC LIMIT 1"
)
FX_SQL = "SELECT rate, date FROM fx_rates WHERE pair = 'USDKRW' AND date <= ? ORDER BY date DESC LIMIT 1"
INDEX_FROM_SQL = "SELECT close, date FROM index_prices WHERE index_code = ? AND date >= ? ORDER BY date LIMIT 1"
INDEX_NOW_SQL = "SELECT close, date FROM index_prices WHERE index_code = ? ORDER BY date DESC LIMIT 1"
PREV_DAILY_SQL = (
    "SELECT as_of, cash_krw, equity_krw, twr_index, updated_at FROM paper_daily"
    " WHERE account_id = ? AND as_of < ? ORDER BY as_of DESC LIMIT 1"
)
#: 그 계좌·시장·거래일에 **결정**(주문이든 안 낸 결정이든)이 이미 있나 — 먼저 쓴 쪽이 이긴다 (docs/paper.md 2.4).
#: 주문만 보면 다 막힌 날(멈춤·현금 부족) 다시 돌 때 skip 위에 buy 가 더 적혀 같은 날 두 집단에 다 셌다 (25.1453)
ORDERS_EXIST_SQL = (
    "SELECT (SELECT COUNT(*) FROM paper_orders WHERE account_id = ? AND market = ? AND decided_on = ?)"
    " + (SELECT COUNT(*) FROM paper_decisions"
    "    WHERE account_id = ? AND market = ? AND decided_on = ? AND kind != 'missed')"
    # 회의록도 센다 — 결정 없이 회의록만 먼저 써진 날 다시 돌면 결정이 또 써졌다 (docs/company.md 3장 8, 25.1466)
    " + (SELECT COUNT(*) FROM paper_meetings WHERE account_id = ? AND market = ? AND decided_on = ?)"
)
STOCK_SQL = (
    "SELECT id, ticker, COALESCE(name_ko, name_en, ticker) AS name, sector, country, currency, asset_type"
    " FROM stocks WHERE id = ?"
)
SCORE_NOW_SQL = (
    "SELECT total_score, as_of_date FROM scores WHERE stock_id = ? ORDER BY as_of_date DESC, calc_version DESC LIMIT 1"
)
INSERT_FILL_SQL = (
    "INSERT OR IGNORE INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
    " fx_rate, fx_date, fee, tax, slippage, cash_krw, horizon, score_at_trade, snapshot_as_of, created_at, day_close,"
    " realized_krw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
FILLED_SQL = (
    "UPDATE paper_orders SET status = 'filled', closed_on = ?, close_note = ? WHERE id = ? AND status = 'open'"
)
#: 전량 매도 때 거둔 매수 회차의 만료 까닭(25.1591) — KIS 비교에서 판정 밖 만료로 뺀다
#: (`broker_mirror.NON_JUDGMENT_EXPIRY`)
WITHDRAWN_NOTE = "같은 종목 전량 매도 — 남은 매수 회차 거둠 (docs/paper.md 2.2)"
#: 매수 단계가 일찍 끝난 날 고른 종목의 skip 까닭 머리(25.1622) — 선정 결과가 아니라 견줄 후보에서 뺀다
#: (`pp.NONCOMPARABLE_SKIP_PREFIXES`)
BUY_STEP_SKIP_PREFIX = pp.BUY_STEP_SKIP_PREFIX
#: 손절이 선 보유(따르지 않는 계좌·매도가 막힌 날 포함)의 남은 매수 회차를 거둘 때(25.1612). 판정 밖 만료로 센다
STOP_WITHDRAWN_NOTE = "손절이 선 보유 — 남은 매수 회차 거둠 (docs/paper.md 2.2)"
EXPIRE_SQL = (
    "UPDATE paper_orders SET status = 'expired', closed_on = ?, close_note = ? WHERE id = ? AND status = 'open'"
)
#: 미룬 주문은 다음 장 **시가** 잣대 — 판정 때 `pp.Order.basis` 가 미룬 횟수로 가린다(25.1621). 여기서 `fill_basis` 를
#: 바꾸면 KIS 비교의 첫날 줄까지 시가 표본에 들어갔다(25.1620 반박 [필수] 1)
DEFER_SQL = (
    "UPDATE paper_orders SET fill_session = ?, deferrals = deferrals + 1, close_note = ?"
    " WHERE id = ? AND status = 'open'"
)
INSERT_ORDER_SQL = (
    "INSERT OR IGNORE INTO paper_orders (account_id, market, stock_id, side, order_type, limit_price, quantity,"
    " currency, decided_on, fill_session, fill_basis, tranche, reason, horizon, score_at_order, score_as_of,"
    " rationale_json, rule_version, created_at, ref_price)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
UPSERT_DAILY_SQL = (
    "INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
    " twr_index, kospi_krw, sp500_krw, positions_json, fills_today, recon_ok, recon_note, unknown_json, updated_at,"
    " realized_krw, unrealized_krw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (account_id, as_of) DO UPDATE SET invested_krw = excluded.invested_krw,"
    " cash_krw = excluded.cash_krw, holdings_krw = excluded.holdings_krw, equity_krw = excluded.equity_krw,"
    " pnl_krw = excluded.pnl_krw, twr_index = excluded.twr_index, kospi_krw = excluded.kospi_krw,"
    " sp500_krw = excluded.sp500_krw, positions_json = excluded.positions_json,"
    " fills_today = paper_daily.fills_today + excluded.fills_today, recon_ok = excluded.recon_ok,"
    " recon_note = excluded.recon_note, unknown_json = excluded.unknown_json, updated_at = excluded.updated_at,"
    " realized_krw = excluded.realized_krw, unrealized_krw = excluded.unrealized_krw"
)

PAPER_TWR_SQL = "SELECT account_id, as_of, twr_index FROM paper_daily ORDER BY account_id, as_of"
#: 실제 포트폴리오의 시간가중 지수 — 비교에만 읽는다(쓰지 않는다). `jobs/portfolio` 가 매매 기록에서 다시 만든 파생 표
REAL_TWR_SQL = "SELECT date, twr_index FROM portfolio_values WHERE date >= ? ORDER BY date"
#: 실제 계열을 모의 첫 기록보다 이만큼 앞부터 읽는다 — 시작일 이하의 마지막 행을 잡으려고(주말·휴일 사이)
REAL_LOOKBACK_DAYS = 14

#: 결정 원장 (docs/paper.md 9장, 25.1449) — 안 한 결정까지. 재실행은 유일 제약이 막는다
DECISION_INSERT_SQL = (
    "INSERT OR IGNORE INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, detail_json,"
    " ref_close, ref_date, created_at, rule_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
#: 투자위원회 회의록 · 조사 자료 (docs/company.md 3·5장, 25.1466) — 결정 원장과 한 묶음으로 쓴다
MEETING_INSERT_SQL = (
    "INSERT OR IGNORE INTO paper_meetings (account_id, market, decided_on, charter_hash, minutes_json, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)
DOSSIER_INSERT_SQL = (
    "INSERT OR IGNORE INTO paper_dossiers (market, as_of, stock_id, dossier_json, created_at) VALUES (?, ?, ?, ?, ?)"
)
#: **창마다 찰 때가 된 행만** (25.1453, 교차검증 [필수] 1) — 예전엔 `ret_60d IS NULL` 인 오래된 순 300행이라,
#: 5·20 은 채우고 60 을 기다리는 행이 앞자리를 차지해 새 결정의 20 거래일 성과가 두 달 넘게 밀렸다.
#: `ret_60d IS NULL` 은 부분 색인을 타게 둔다
PENDING_DECISIONS_SQL = (
    "SELECT id, stock_id, decided_on, ref_close, ref_date, ret_5d, ret_20d, ret_60d FROM paper_decisions"
    " WHERE ret_60d IS NULL AND outcome_note IS NULL AND market = ?"
    "   AND ((ret_5d IS NULL AND decided_on <= ?) OR (ret_20d IS NULL AND decided_on <= ?) OR decided_on <= ?)"
    " ORDER BY decided_on LIMIT ?"
)
#: 결정 원장 성과·가상 대조의 가격 — 기준일부터 **분할만 반영한** 계열(국내 수정주가·미국 야후 종가,
#: `adj.split_only_prices`, 25.1547 감사 [권고] 2). 국내 `close` 는 원 종가라 저장한 기준 종가(분할 전 단위)를 분할 뒤
#: 종가와 견주면 2:1 분할이 −50%, 1:10 병합이 +900% 로 남았다 — 성과 분해·결정 단위 증거·H 회사 가중치가 그 값을 쓴다
SPLIT_FROM_SQL = (
    "SELECT p.date, p.adj_close, p.close, s.country FROM prices p JOIN stocks s ON s.id = p.stock_id"
    " WHERE p.stock_id = ? AND p.date >= ? AND p.close > 0 ORDER BY p.date LIMIT ?"
)
INDEX_ON_OR_BEFORE_SQL = (
    "SELECT close, date FROM index_prices WHERE index_code = ? AND date <= ? ORDER BY date DESC LIMIT 1"
)
UPDATE_OUTCOME_SQL = (
    "UPDATE paper_decisions SET ret_5d = COALESCE(ret_5d, ?), ret_20d = COALESCE(ret_20d, ?),"
    " ret_60d = COALESCE(ret_60d, ?), bench_5d = COALESCE(bench_5d, ?), bench_20d = COALESCE(bench_20d, ?),"
    " bench_60d = COALESCE(bench_60d, ?), outcome_note = ?, outcome_at = ? WHERE id = ?"
)
#: 한 번에 성과를 채우는 결정 수 — 하루 읽기 상한(행당 최대 약 77 시세 행 = 60 + 덧 10 + 기준일 앞 약 7(25.1548) + 지수
#: 몇 행). 계좌가 일곱(25.1458)이라 결정도
#: 그만큼 늘어 300 에서 올렸다 — 같은 종목·기준일의 시세는 한 번만 읽으므로(캐시) 읽기는 계좌 수만큼 늘지 않는다.
#: 어림(25.1588): 전 계좌가 하루·시장마다 약 130~150행(위원회 네 곳 약 90 · H 15~25 · B·G·R 각 5 ·
#: 매도·hold_flag)을 남기고
#: 행마다 5·20·60 세 번 대기열에 들어 평소 하루 약 400~450행(휴장·지수 기다림이면 더).
#: 한도 공백(하루 약 400행씩 쌓임) 뒤에는 상한에 닿아 며칠 밀린다 — 닿으면 운용 줄에 경고를 낸다
OUTCOME_BATCH = 900
#: 성과 분해 (docs/paper.md 10장, 25.1450) — 성과가 찬 결정과 체결일 종가가 있는 매수만, 최근 1년
#: 성과 분해·부서 성적표가 보는 결정 — **지금 판의 것만**(판 번호가 빈 옛 행은 판 1).
#: 판 2 회사의 성적에 판 1 결정이 섞이지 않게 (docs/company.md 1장 검증 [필수] 7, 25.1472)
#: `market IN` 은 유일 색인 (account_id, market, decided_on…) 의 날짜 범위까지 타게 한다 — 없으면 계좌의 전 기간 행을
#: 읽었다(25.1571, 운용 감사 [권고] 5). 시장은 둘뿐이다
ATTR_DECISIONS_SQL = (
    "SELECT stock_id, market, decided_on, kind, reason, ret_20d, bench_20d, detail_json, control_pct"
    " FROM paper_decisions"
    " WHERE account_id = ? AND market IN ('KR', 'US') AND decided_on >= ? AND ret_20d IS NOT NULL"
    " AND COALESCE(rule_version, 1) = ?"
)
#: 진입 칸의 체결도 **지금 판의 주문만**(25.1547 감사 [참고] 4 — 위 주석과 달리 판을 거르지 않아 지난 판 체결이 섞였다)
ATTR_FILLS_SQL = (
    "SELECT f.order_id, f.price, f.day_close FROM paper_fills f JOIN paper_orders o ON o.id = f.order_id"
    " WHERE f.account_id = ? AND f.side = 'buy' AND f.day_close IS NOT NULL AND f.fill_date >= ?"
    " AND COALESCE(o.rule_version, 1) = ?"
)
SAVE_ATTR_SQL = "UPDATE paper_daily SET attribution_json = ? WHERE account_id = ? AND as_of = ?"
#: 성과 분해가 보는 기간(달력일)
ATTR_WINDOW_DAYS = 365

#: 사람이 켜는 모의 매매 멈춤 스위치 — /paper 화면의 단추가 쓴다 (docs/paper.md 11장, 25.1451)
HALT_SETTING_KEY = "paper_halt"

#: 결정의 기준 지수
BENCH_INDEX = {"KR": "KOSPI", "US": "SP500"}

#: 미국 환율이 이 날수보다 묵었으면 쓰지 않는다 — 2부와 같은 기준(`signals_job.load_settings` 7일)
FX_MAX_AGE_DAYS = 7

KST_MARKET = "KR"


def _원(v: float) -> float:
    return float(round(v))


def _결정(
    aid: str, market: str, decided_on: str, sid: int, kind: str, reason: str, detail: dict[str, Any] | None,
    ref_close: Any, ref_date: Any, now: str, rule_version: int | None = None,
) -> tuple[str, list[Any]]:
    """결정 원장 한 줄 (docs/paper.md 9장). 판 번호를 붙인다 — 판 1·2 결정을 성적표가 섞지 않게 (company.md 1장)."""
    return (DECISION_INSERT_SQL, [
        aid, market, decided_on, sid, kind, reason[:200],
        json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
        None if ref_close is None else float(ref_close), ref_date, now, rule_version,
    ])  # fmt: skip


def fill_outcomes(client: Any, market: str, today: str, now: str) -> dict[str, int]:
    """결정 원장의 5·20·60 거래일 성과를 채운다 (docs/paper.md 9장, 25.1449).

    달력일이 지나 찼을 수 있는 창만 읽는다(`paper.due_horizons`). 시세가 끊겨 `OUTCOME_GIVE_UP_DAYS` 가 지나도
    못 채우면 까닭을 적고 그만 본다
    """
    from datetime import date as _date

    오늘 = _date.fromisoformat(today)
    기한 = {n: (오늘 - timedelta(days=d)).isoformat() for n, d in pp.HORIZON_CALENDAR_DAYS.items()}
    rows = client.execute(PENDING_DECISIONS_SQL, [market, 기한[5], 기한[20], 기한[60], OUTCOME_BATCH]).dicts()
    # 미국 분할 재수집 대기 종목은 오늘 채우지 않는다(25.1548 반박 [권고] 4) — 일일 수집은 최근 10일만 분할 반영 값으로
    # 덮고 옛 행은 주간 재수집까지 분할 전 단위라, 그 사이 찬 창은 −50% 가 영구히 남았다
    # (한 번 채운 창은 다시 쓰지 않는다)
    대기 = db.split_pending_us(client) if market == "US" and rows else set()
    지수캐시: dict[str, tuple[float, str] | None] = {}
    코드 = BENCH_INDEX.get(market, "KOSPI")

    def _지수행(날: str) -> tuple[float, str] | None:
        if 날 not in 지수캐시:
            r = client.execute(INDEX_ON_OR_BEFORE_SQL, [코드, 날]).rows
            지수캐시[날] = (float(r[0][0]), str(r[0][1])[:10]) if r else None
        return 지수캐시[날]

    def 지수(날: str) -> float | None:
        r = _지수행(날)
        return r[0] if r else None

    def 기다림(날: str) -> bool:
        """그날 지수 행이 아직 없고(묵은 값뿐) 기다릴 기한 안이다 (25.1579)"""
        r = _지수행(날)
        return (r is None or r[1] < 날) and (오늘 - _date.fromisoformat(날)).days < pp.BENCH_WAIT_DAYS

    채움, 포기 = 0, 0
    문장: list[tuple[str, list[Any]]] = []
    시세캐시: dict[tuple[int, str, int], list] = {}
    for r in rows:
        if r["ref_close"] is None or not r["ref_date"]:
            # 기준 종가가 없는 행은 영영 못 채운다 — 대기열에 남아 자리를 먹지 않게 바로 까닭을 적는다 (25.1453)
            문장.append((UPDATE_OUTCOME_SQL, [None] * 6 + ["기준 종가 없음", now, r["id"]]))
            포기 += 1
            continue
        창 = pp.due_horizons(str(r["decided_on"]), today, r)
        if not 창 or int(r["stock_id"]) in 대기:
            continue
        # 같은 종목·기준일·창은 계좌가 일곱이어도 한 번만 읽는다 (25.1458, 검증 권장 — 결정이 계좌 수만큼 겹친다)
        열쇠 = (int(r["stock_id"]), str(r["ref_date"]), max(창))
        if 열쇠 not in 시세캐시:
            시세캐시[열쇠] = _분할맞춘(client, int(r["stock_id"]), str(r["ref_date"]), str(r["ref_date"]), max(창))
        계열기준, 뒤 = 시세캐시[열쇠]
        그날들 = {뒤[n - 1][0]: 지수(뒤[n - 1][0]) for n in 창 if len(뒤) >= n}
        # 기준일 값은 같은 계열에서 — 그날 행이 없을 때만 저장한 기준 종가(분할이 끼면 단위가 어긋날 수 있다)
        o = pp.outcomes(계열기준 if 계열기준 else r["ref_close"], 뒤, 지수(str(r["ref_date"])), 그날들)
        미룸 = [n for n in 창 if len(뒤) >= n and 기다림(뒤[n - 1][0])]
        for n in 미룸:
            o.pop(f"ret_{n}d", None)
            o.pop(f"bench_{n}d", None)
        지난 = (_date.fromisoformat(today) - _date.fromisoformat(str(r["decided_on"]))).days
        # 미룬 창이 하나라도 있으면 포기로 닫지 않는다 — 닫으면 미룬 5·20 창도 대기열에서 빠져 영영 빈다(25.1583)
        포기함 = "ret_60d" not in o and not 미룸 and 지난 >= pp.OUTCOME_GIVE_UP_DAYS
        # 이미 채운 창을 다시 계산한 것뿐이면 쓰지 않는다 — 날마다 같은 행을 다시 쓰고 `filled` 가 부풀었다 (25.1453)
        새것 = any(r.get(f"ret_{n}d") is None and o.get(f"ret_{n}d") is not None for n in pp.DECISION_HORIZONS)
        if not 새것 and not 포기함:
            continue
        문장.append((UPDATE_OUTCOME_SQL, [
            *(o.get(f"ret_{n}d") for n in pp.DECISION_HORIZONS), *(o.get(f"bench_{n}d") for n in pp.DECISION_HORIZONS),
            f"{pp.OUTCOME_GIVE_UP_DAYS}일 지나도 시세 {len(뒤)}거래일뿐" if 포기함 else None, now, r["id"],
        ]))  # fmt: skip
        채움 += 새것
        포기 += 포기함
    if 문장:
        client.batch(문장)
    return {"pending_read": len(rows), "filled": 채움, "gave_up": 포기}


#: 가상 대조 (docs/company.md 6장, 25.1476) — 성과가 찼는데 아직 대조를 안 본 매수
PENDING_CONTROL_SQL = (
    "SELECT id, stock_id, decided_on, ref_date, ret_20d FROM paper_decisions"
    " WHERE market = ? AND kind = 'buy' AND ret_20d IS NOT NULL AND control_n IS NULL AND ref_date IS NOT NULL"
    " ORDER BY decided_on LIMIT ?"
)
#: 결정일 이전 가장 최근 유니버스 스냅숏 날짜 — (snapshot_date, included) 색인의 최댓값 찾기라 한 번에 닿는다.
#: 시장마다 스냅숏 날이 다를 수 있어 그 시장 종목이 없으면 하나 더 앞으로 간다(`_대조_모집단`)
SNAPSHOT_AT_SQL = "SELECT MAX(snapshot_date) FROM universe_members WHERE snapshot_date <= ?"
UNIVERSE_AT_SQL = (
    "SELECT u.stock_id FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
    " WHERE u.snapshot_date = ? AND u.included = 1 AND s.country = ? AND s.asset_type = 'stock'"
)
CLOSE_ON_OR_BEFORE_SQL = (
    "SELECT date, close FROM prices WHERE stock_id = ? AND date <= ? AND close > 0 ORDER BY date DESC LIMIT 1"
)
CONTROL_UPDATE_SQL = "UPDATE paper_decisions SET control_pct = ?, control_n = ? WHERE id = ?"
#: 한 번에 대조를 보는 매수 수 — 같은 기준일의 매수는 대조군을 나눠 쓰므로 읽기는 기준일 수에 비례한다
#: (기준일마다 유니버스 수천 행 + 대조 20 × 21 행)
CONTROL_BATCH = 300
#: 대조 종목의 기준일 종가가 이보다 묵었으면(정지 등) 그 대조는 뺀다 — 위험 점검의 묵음 기준(7일)과 같은 값
CONTROL_STALE_DAYS = 7


def _대조_모집단(client: Any, market: str, decided_on: str) -> list[int]:
    """결정일 당시의 유니버스(편입·개별주) — 지금 유니버스를 쓰면 그 사이 빠진 종목이 사라져 생존편향."""
    날 = decided_on
    for _ in range(3):
        r = client.execute(SNAPSHOT_AT_SQL, [날]).rows
        if not r or not r[0][0]:
            return []
        모집단 = [int(x[0]) for x in client.execute(UNIVERSE_AT_SQL, [str(r[0][0]), market]).rows]
        if 모집단:
            return 모집단
        날 = (date.fromisoformat(str(r[0][0])[:10]) - timedelta(days=1)).isoformat()
    return []


def _분할맞춘(
    client: Any, sid: int, base_date: str, after: str, n: int,
) -> tuple[float | None, list[tuple[str, float]]]:
    """(기준일 값, `after` 뒤 n 거래일 [(날, 값)]) — 둘 다 같은 분할만 반영 계열에서(25.1547).
    기준일 행이 없으면 None"""
    from batch.services import adjust as adj

    # 기준일 앞 몇 행을 붙여 읽는다 — 창 맨 앞(기준일) 수정주가 행이 비면 `fill_adjusted` 가 원 종가로 두어 −50% 가
    # 났다(25.1548 반박 [참고] 5). 앞 행이 있으면 가운데 빈 행이 되어 메워진다
    앞 = (date.fromisoformat(base_date[:10]) - timedelta(days=OUTCOME_LEAD_DAYS)).isoformat()
    rows = client.execute(SPLIT_FROM_SQL, [sid, 앞, n + OUTCOME_ROW_PAD + OUTCOME_LEAD_DAYS]).dicts()
    if not rows:
        return None, []
    값 = adj.split_only_prices(rows, str(rows[0]["country"]))
    쌍 = [(str(r["date"])[:10], float(v)) for r, v in zip(rows, 값, strict=True)
         if v is not None and float(v) > 0 and str(r["date"])[:10] >= base_date[:10]]  # fmt: skip
    기준 = 쌍[0][1] if 쌍 and 쌍[0][0] == base_date[:10] else None
    return 기준, [x for x in 쌍 if x[0] > after[:10]][:n]


#: 기준일과 `after` 사이(가상 대조의 묵은 기준 종가 — 그날 이하 마지막 종가라 사이에 행이 없다)를 덮을 덧 행
OUTCOME_ROW_PAD = 10
#: 기준일 앞으로 붙여 읽는 달력일 — 기준일 수정주가 행이 비었을 때 앞 행으로 메운다(25.1548). 신호 단위 비율의 앞뒤 10일
#: (`monitor_targets.UNIT_PAD_DAYS`)과 같은 길이
OUTCOME_LEAD_DAYS = 10


def fill_controls(client: Any, market: str) -> dict[str, int]:
    """매수마다 가상 대조 분위를 채운다 (docs/company.md 6장, 25.1476). 결정 원장의 성과가 찬 매수만, 한 번."""
    rows = client.execute(PENDING_CONTROL_SQL, [market, CONTROL_BATCH]).dicts()
    # 분할 재수집 대기(미국) 대조 종목은 성과를 모름으로 — 단위가 섞인다(25.1548)
    대기 = db.split_pending_us(client) if market == "US" and rows else set()
    모집단캐시: dict[str, list[int]] = {}
    수익캐시: dict[tuple[int, str], float | None] = {}

    def 수익(sid: int, ref_date: str) -> float | None:
        if sid in 대기:
            return None
        if (sid, ref_date) not in 수익캐시:
            기준 = client.execute(CLOSE_ON_OR_BEFORE_SQL, [sid, ref_date]).rows
            값: float | None = None
            묵음 = (date.fromisoformat(ref_date) - date.fromisoformat(str(기준[0][0])[:10])).days if 기준 else None
            if 묵음 is not None and 묵음 <= CONTROL_STALE_DAYS:
                # 기준·뒤 모두 분할만 반영 계열 — 매수 쪽 성과와 같은 잣대(25.1547)
                값기준, 뒤 = _분할맞춘(client, sid, str(기준[0][0]), ref_date, pp.ATTRIBUTION_HORIZON)
                if 값기준 and len(뒤) >= pp.ATTRIBUTION_HORIZON:
                    값 = (뒤[-1][1] / 값기준 - 1) * 100
            수익캐시[(sid, ref_date)] = 값
        return 수익캐시[(sid, ref_date)]

    문장: list[tuple[str, list[Any]]] = []
    낸것 = 0
    for r in rows:
        결정일, 기준일 = str(r["decided_on"]), str(r["ref_date"])[:10]
        if 결정일 not in 모집단캐시:
            모집단캐시[결정일] = _대조_모집단(client, market, 결정일)
        대조 = pp.control_picks(모집단캐시[결정일], market, 기준일, int(r["stock_id"]))
        rets = [x for x in (수익(c, 기준일) for c in 대조) if x is not None]
        분위 = pp.control_pct(float(r["ret_20d"]), rets)
        문장.append((CONTROL_UPDATE_SQL, [분위, len(rets), r["id"]]))
        낸것 += 분위 is not None
    if 문장:
        client.batch(문장)
    return {"pending_read": len(rows), "filled": 낸것, "snapshots": len(모집단캐시)}


def _쓰기(client: Any, statements: list[tuple[str, list[Any]]]) -> None:
    """한 번에 — `batch_atomic` 이 있으면 그것으로, 서버가 안 받으면 문장마다(유일 제약이 두 번 적기를 막는다)."""
    if not statements:
        return
    원자 = getattr(client, "batch_atomic", None)
    if 원자 is not None:
        from batch.core.turso import AtomicUnsupported

        try:
            원자(statements)
            return
        except AtomicUnsupported:
            pass
    client.batch(statements)


def _flows(client: Any) -> tuple[list[pp.CashFlow], dict[int, str]]:
    rows = client.execute(FLOWS_SQL).dicts()
    흐름 = [pp.CashFlow(int(r["id"]), str(r["flow_date"]), float(r["amount_krw"]), str(r["kind"]),
                      int(r["cancels_id"]) if r["cancels_id"] is not None else None) for r in rows]  # fmt: skip
    return 흐름, {int(r["id"]): str(r["created_at"]) for r in rows}


def _fills(client: Any) -> tuple[list[pp.Fill], dict[int, str]]:
    rows = client.execute(FILLS_SQL).dicts()
    out = [
        pp.Fill(
            order_id=int(r["order_id"]), account_id=str(r["account_id"]), stock_id=int(r["stock_id"]),
            side=str(r["side"]), fill_date=str(r["fill_date"]), price=float(r["price"]), quantity=int(r["quantity"]),
            currency=str(r["currency"]), fx_rate=float(r["fx_rate"]), fee=float(r["fee"]), tax=float(r["tax"]),
            slippage=float(r["slippage"]), cash_krw=float(r["cash_krw"]), horizon=r["horizon"],
            score_at_trade=r["score_at_trade"], snapshot_as_of=r["snapshot_as_of"],
        )  # fmt: skip
        for r in rows
    ]
    return out, {int(r["order_id"]): str(r["created_at"]) for r in rows}


def _order(r: dict[str, Any]) -> pp.Order:
    return pp.Order(
        id=int(r["id"]), account_id=str(r["account_id"]), stock_id=int(r["stock_id"]), side=str(r["side"]),
        order_type=str(r["order_type"]), limit_price=r["limit_price"], quantity=int(r["quantity"]),
        currency=str(r["currency"]), fill_session=str(r["fill_session"]), horizon=r["horizon"],
        score_at_order=r["score_at_order"], score_as_of=r["score_as_of"], fill_basis=str(r["fill_basis"] or "open"),
        ref_price=r.get("ref_price"), deferrals=int(r.get("deferrals") or 0),
    )  # fmt: skip


def _fx(client: Any, day: str) -> tuple[float, str] | None:
    """그날(또는 직전) USDKRW — `FX_MAX_AGE_DAYS` 넘게 묵었으면 None."""
    row = client.execute(FX_SQL, [day]).rows
    if not row:
        return None
    rate, 날 = float(row[0][0]), str(row[0][1])
    if (date.fromisoformat(day[:10]) - date.fromisoformat(날[:10])).days > FX_MAX_AGE_DAYS:
        return None
    return rate, 날


def _수수료(market: str, fees: dict | None) -> tuple[float, float]:
    """매수·매도 수수료(%)를 **따로** — 사용자 설정(`{kr|us}_{buy|sell}_pct`), 빈 쪽만 백테스트 기본값. 예전엔
    백테스트의 편도 평균(`commission_pct`)을 양쪽에 붙여 매수 0.015·매도 0.25 면 둘 다 0.1325, 매도만 채우면 매수에도
    0.25 가 붙었다(25.1620, 체결 판정 감사 [필수] 3). 백테스트는 왕복을 편도 ×2 로 내므로 평균이 맞고, 체결마다 붙이는
    가상모의투자는 쪽마다가 맞다"""
    앞 = "us" if market.upper() == "US" else "kr"
    잎, _ = sr.잎마다(fees, (f"{앞}_buy_pct", f"{앞}_sell_pct"))
    매수, 매도 = (bt._pct(잎.get(f"{앞}_{side}_pct")) for side in ("buy", "sell"))
    기본 = bt.Costs.commission_pct
    return (기본 if 매수 is None else 매수), (기본 if 매도 is None else 매도)


def costs_for(market: str, fees: dict | None, taxes: dict | None, day: str) -> pp.Costs:
    """그 시장 비용 — 사용자 설정, 비면 백테스트 기본값(국내 매도세는 시행일 표) (docs/paper.md 2.3)."""
    c = bt.Costs.for_country(market, fees, taxes)
    매수, 매도 = _수수료(market, fees)
    return pp.Costs(buy_fee_pct=매수, sell_fee_pct=매도, sell_tax_pct=c.tax_at(day), slippage_pct=c.slippage_pct)


def cost_snapshot(market: str, fees: dict | None, taxes: dict | None, day: str) -> dict[str, Any]:
    """주문 근거에 남기는 비용 — 그 장(`day`) 기준 매수·매도 수수료·매도세·슬리피지와 **기본값을 쓴 항목**
    (docs/paper.md 2.3 — 문서는 적는다고 했는데 주문 스냅샷에 없었다, 25.1595). 실제 비용은 체결일 설정으로 붙는다.
    `fee_pct` 는 예전 칸(편도 평균) — 옛 근거와 견주려고 남긴다(25.1620)"""
    c = bt.Costs.for_country(market, fees, taxes)
    매수, 매도 = _수수료(market, fees)
    return {"fee_pct": c.commission_pct, "buy_fee_pct": 매수, "sell_fee_pct": 매도, "sell_tax_pct": c.tax_at(day),
            "slippage_pct": c.slippage_pct, "defaults": list(c.defaults), "as_of": day}  # fmt: skip


def _보유와_열린매수(
    rows: list[dict[str, Any]], open_buys: list[pp.Order], fx: dict[str, float | None],
) -> dict[int, float]:
    """사전 위험 점검의 종목별 원화 — 평가 + 미체결 매수 예약(25.1549).
    달러 환율을 모르면 평가만(그날 매수는 이미 안 함)"""
    out = {int(r["stock_id"]): float(r["value_krw"]) for r in rows}
    for sid, x in (pp.reserved_by_stock(open_buys, fx) or {}).items():
        out[sid] = out.get(sid, 0.0) + x
    return out


def frozen_ids(client: Any, stock_ids: set[int], market_of: dict[int, str]) -> set[int]:
    """기업행위 경계(분할·병합 의심)에 든 종목 — 판 1 은 멈춘다 (docs/paper.md 2.3, 검증 [필수] 6)."""
    from batch.jobs import monitor_targets

    out: set[int] = set()
    for sid in sorted(stock_ids):
        try:
            if monitor_targets.action_factors(client, sid, market_of.get(sid, "KR")) is None:
                out.add(sid)
        except Exception as exc:  # noqa: BLE001 — 모르면 멈춘다(보수 쪽). 까닭은 기록에
            log.warning("기업행위 경계를 읽지 못해 멈춥니다 (종목 %s): %s", sid, exc)
            out.add(sid)
    return out


#: 보유 중에 수량이 바뀌는 기업행위가 난 종목 — {종목: 기업행위 날}. 경계 창(`monitor_targets.ACTION_GUARD_ROWS`,
#: 20 거래일)이 지나도 멈춤을 이어 간다(25.1547 감사 [필수] 1). 장부 수량·단가를 고치는 `paper_adjustments`(판 2)가
#: 생기기 전까지는 분할 전 수량 × 분할 뒤 종가로 평가해 2:1 분할이면 −50% 손절, 1:10 병합이면 없던 현금 약 100만 원이
#: 장부에 들어왔다
ACTION_STICKY_KEY = "paper_action_frozen"


#: 이어 멈춤 표의 날을 다시 보는 기간(달력일) — 감지 창(`ACTION_GUARD_ROWS` 21행 ≈ 29 달력일) 안쪽이라 그 날의 행이
#: 아직 보인다. 그보다 오래된 날은 다시 볼 길이 없어 그대로 둔다(25.1560)
ACTION_STICKY_RECHECK_DAYS = 20


def sticky_frozen(
    client: Any, fresh: set[int], first_buy: dict[int, str], market_of: dict[int, str],
) -> tuple[set[int], dict[str, str]]:
    """(이어 멈출 종목, 저장한 표). `fresh` = 오늘 기업행위 경계에 든 종목, `first_buy` = 지금 든 종목 → 가장 이른
    첫 매수일.

    경계에 든 보유 종목 가운데 **기업행위 날보다 먼저 산 것**만 표에 적는다(분할 뒤에 산 몫은 수량이 맞다). 표의 종목은
    아무 계좌도 들지 않게 되면 지운다. 적힌 종목은 원가 평가·매도 판정 안 함·새 매수 안 함 — 경계 안과 같은 멈춤이다
    """
    from batch.jobs import monitor_targets
    from batch.services import adjust_drift as adjd

    저장 = db.get_setting(client, ACTION_STICKY_KEY, {}) or {}
    표 = {k: str(v) for k, v in (저장.items() if isinstance(저장, dict) else []) if int(k) in first_buy}
    # 경계 안 실행을 놓쳐도(가상모의투자 단계가 20 거래일 내내 실패) 수집이 남긴 감지 기록으로 다시 본다
    # (25.1548 반박 [권고] 1)
    기록된 = {int(k) for key in (adjd.KR_ACTION_LOG_KEY, adjd.SPLIT_LOG_KEY)
              for k in (db.get_setting(client, key, {}) or {})}  # fmt: skip
    for sid in sorted((set(fresh) | 기록된) & set(first_buy)):
        if sid not in first_buy or (str(sid) in 표 and sid not in fresh):
            # 표에 있고 오늘 새 경계도 아니면 그대로 — 경계에 다시 들면(두 번째 분할) 날을 늦춰 적는다(25.1550 [참고] 2)
            continue
        try:
            날들 = monitor_targets.action_dates(client, sid, market_of.get(sid, "KR"))
        except Exception as exc:  # noqa: BLE001 — 모르면 적지 않는다(오늘은 경계 멈춤이 이미 막는다), 내일 다시 본다
            log.warning("기업행위 날을 읽지 못했습니다 (종목 %s): %s", sid, exc)
            continue
        먼저 = [d for d in 날들 if first_buy[sid] < d]
        if 먼저 and 먼저[-1] > 표.get(str(sid), ""):
            표[str(sid)] = 먼저[-1]
    # **거짓 감지 되돌리기**(25.1560 — 25.1554 반박 [권고]): 야후 대체 날(등락률 빈칸)이 시세 구멍 위에 서면 누적
    # 움직임이 ±30% 를 넘어 기업행위로 적히고, 다음 날 그 구멍을 거래소 값으로 메워도 표는 그대로라 그 보유가 영영
    # 원가 평가·손절 꺼짐으로 남았다. 적은 날이 `ACTION_STICKY_RECHECK_DAYS` 안이면(감지 창 21행이 아직 덮는다)
    # 다시 보고, 그 날이 더는 기업행위로 안 나오면 지운다. 창 밖이면 다시 볼 길이 없어 그대로 둔다(보수 쪽)
    오늘 = cal.local_today("KR")
    for k, 날 in sorted(표.items()):
        sid = int(k)
        if sid in fresh or (오늘 - date.fromisoformat(날[:10])).days > ACTION_STICKY_RECHECK_DAYS:
            continue
        try:
            if 날 not in monitor_targets.action_dates(client, sid, market_of.get(sid, "KR")):
                del 표[k]
        except Exception as exc:  # noqa: BLE001 — 모르면 지우지 않는다
            log.warning("이어 멈춤을 다시 보지 못했습니다 (종목 %s): %s", sid, exc)
    if 표 != 저장:
        db.set_setting(client, ACTION_STICKY_KEY, 표)
    return {int(k) for k in 표}, 표


def fill_orders(
    client: Any, market: str, trade_date: str, flows: list[pp.CashFlow], fills: list[pp.Fill],
    fees: dict | None, taxes: dict | None, frozen: set[int], now: str, 기록: dict[str, Any],
    frozen_by_account: dict[str, set[int]] | None = None,
) -> list[pp.Fill]:
    """이 시장의 미체결 주문 가운데 판정할 날이 된 것을 판정한다. 새 체결 목록을 돌려준다(장부에도 적었다)."""
    전부 = client.execute(OPEN_ORDERS_SQL).dicts()
    대상 = [r for r in 전부 if r["market"] == market and str(r["fill_session"]) <= trade_date]
    if not 대상:
        return []
    시장날: dict[str, bool] = {}
    새것: list[pp.Fill] = []
    오늘환율 = _fx(client, trade_date)
    # 다른 시장의 미체결 매수도 현금을 묶고 있다 — 체결 검사에서 함께 뺀다 (25.1447, 교차검증 권장)
    남은매수 = {int(r["id"]): _order(r) for r in 전부 if r["side"] == "buy"}
    # 장 순서 먼저, 그 안에서 매도 먼저(현금이 생긴다), 그다음 매수 — 같은 날 안에서는 주문 순서. 장 순서가 없으면 두
    # 장의 주문을 한 실행에서 판정할 때(어제 시세가 늦은 아침) 뒤 장 매도가 앞 장 매수보다 먼저 처리돼
    # 거래별 실현손익이 평균 원가 장부(`pp.realized`)와 어긋나고 앞 장 매수의 현금 검사에 뒤 장 매도 대금이 들어갔다
    # (25.1620, 체결 판정 감사 [필수] 2 — `pp.positions` 정렬과 맞춤)
    for r in sorted(대상, key=lambda x: (str(x["fill_session"]), x["side"] != "sell", int(x["id"]))):
        o = _order(r)
        날 = o.fill_session
        if any(f.order_id == o.id for f in fills):
            # 체결 행은 들어갔는데 주문 상태만 못 바꾼 채 끊겼던 것 — 상태만 맞추고 두 번 세지 않는다 (25.1447).
            # **맨 앞에서 본다**(25.1558, 25.1547 감사 남은 것): 예전엔 현금·보유 검사 뒤라, 이미 든 체결이
            # 장부에 한 번 더 얹혀 매수는 "현금 부족", 매도는 "팔 보유가 모자람" 으로 체결 행이 있는 주문을
            # 취소했다(장부와 주문 상태가 어긋남)
            남은매수.pop(o.id, None)
            _쓰기(client, [(FILLED_SQL, [trade_date, "체결 행 있음 — 상태만 맞춤", o.id])])
            continue
        if 날 not in 시장날:
            시장날[날] = bool(client.execute(MARKET_DAY_SQL, [날, market]).rows)
        if o.stock_id in frozen or o.stock_id in (frozen_by_account or {}).get(o.account_id, set()):
            _쓰기(client, [(EXPIRE_SQL, [trade_date, "기업행위 경계 — 멈춤 (docs/paper.md 2.3)", o.id])])
            남은매수.pop(o.id, None)
            기록["expired"] = 기록.get("expired", 0) + 1
            continue
        if not 시장날[날]:
            if 날 < trade_date:
                # 그 시장 전체에 그날 시세가 없다 — 정지가 아니라 못 받은 날. 매수는 취소, 매도는 다음 장으로
                if o.side == "buy":
                    _쓰기(client, [(EXPIRE_SQL, [trade_date, "그날 시세를 받지 못해 판정 못 함", o.id])])
                    남은매수.pop(o.id, None)
                    기록["expired"] = 기록.get("expired", 0) + 1
                else:
                    _쓰기(client, [(DEFER_SQL, [cal.next_session(market, date.fromisoformat(날)).isoformat(),
                                               "그날 시세를 받지 못함 — 다음 장으로", o.id])])  # fmt: skip
                    기록["deferred"] = 기록.get("deferred", 0) + 1
            continue
        행 = client.execute(BAR_SQL, [o.stock_id, 날]).dicts()
        bar = (pp.Bar(str(행[0]["date"]), 행[0]["open"], float(행[0]["close"]), 행[0]["volume"],
                      행[0].get("change_pct")) if 행 and 행[0]["close"] is not None else None)  # fmt: skip
        가격, 까닭 = pp.judge(o, bar)
        그날종가 = bar.close if bar is not None else None  # 진입 이득을 잴 체결일 종가 (25.1450)
        # 하한가로 갇힌 시장가 매도도 정지처럼 다음 장으로 미룬다(25.1553) — 취소하면 그 보유가 매도 없이 남는다
        if 가격 is None and o.side == "sell" and (pp.halted(bar) or 까닭.endswith(pp.LOCKED_SELL)):
            if int(r["deferrals"] or 0) + 1 >= pp.HALT_LIQUIDATE_SESSIONS:
                마지막 = client.execute(LAST_CLOSE_SQL, [o.stock_id, 날]).rows
                if 마지막:
                    가격 = float(마지막[0][0])
                    까닭 = f"정지·하한가로 {pp.HALT_LIQUIDATE_SESSIONS}거래일 미룸 — 마지막 종가로 청산"
            if 가격 is None:
                _쓰기(client, [(DEFER_SQL, [cal.next_session(market, date.fromisoformat(날)).isoformat(),
                                           f"{까닭} — 다음 장으로", o.id])])  # fmt: skip
                기록["deferred"] = 기록.get("deferred", 0) + 1
                continue
        if 가격 is None:
            문장 = [(EXPIRE_SQL, [trade_date, 까닭, o.id])]
            if o.side == "buy" and bar is not None and not pp.halted(bar):
                # 지정가가 안 찬 회차 — 그 뒤 얼마나 올랐나(놓친 기회)를 결정 원장으로 잰다 (25.1449)
                문장.append(_결정(o.account_id, market, 날, o.stock_id, "missed", 까닭,
                                {"limit_price": o.limit_price, "quantity": o.quantity, "order_id": o.id},
                                bar.close, 날, now, r.get("rule_version")))  # fmt: skip
            _쓰기(client, 문장)
            남은매수.pop(o.id, None)
            기록["expired"] = 기록.get("expired", 0) + 1
            continue
        환율, 환율날 = 1.0, None
        if o.currency != "KRW":
            fx = _fx(client, 날)
            if fx is None:
                if o.side == "buy":
                    _쓰기(client, [(EXPIRE_SQL, [trade_date, "쓸 수 있는 환율 없음", o.id])])
                    남은매수.pop(o.id, None)
                    기록["expired"] = 기록.get("expired", 0) + 1
                else:
                    _쓰기(client, [(DEFER_SQL, [cal.next_session(market, date.fromisoformat(날)).isoformat(),
                                               "쓸 수 있는 환율 없음 — 다음 장으로", o.id])])  # fmt: skip
                    기록["deferred"] = 기록.get("deferred", 0) + 1
                continue
            환율, 환율날 = fx
        체결 = pp.make_fill(o, 가격, 날, 환율, costs_for(market, fees, taxes, 날))
        if o.side == "buy":
            계좌체결 = [f for f in [*fills, *새것] if f.account_id == o.account_id]
            남은매수.pop(o.id, None)
            # 뒤 장 매수의 예약은 빼지 않는다 — 장 순서로 판정하니 뒤 장 매도 대금이 안 들어오는데 뒤 장 매수 예약만
            # 빼면 앞 장 매수가 현금 부족으로 취소됐다(25.1621, 25.1620 반박 [필수] 2)
            다른예약 = pp.reserved_krw([x for x in 남은매수.values() if x.account_id == o.account_id
                                    and x.fill_session <= o.fill_session],
                                   {"USD": 오늘환율[0] if 오늘환율 else None})  # fmt: skip
            # 다른 달러 예약의 환율을 모르면(None) 예약을 빼지 않고 빚만 막는다 — 주문 때 이미 현금 안에서 냈다
            if pp.cash(flows, 계좌체결) + 체결.cash_krw - (다른예약 or 0.0) < 0:
                _쓰기(client, [(EXPIRE_SQL, [trade_date, "현금 부족 (다른 미체결 매수 예약 포함)", o.id])])
                기록["expired"] = 기록.get("expired", 0) + 1
                continue
        실현 = None
        if o.side == "sell":
            보유 = pp.positions([f for f in [*fills, *새것] if f.account_id == o.account_id]).get(o.stock_id)
            if 보유 is not None and 보유.quantity > 0:
                # 매도의 실현손익(원) — 평균 원가 기준, 화면의 거래내역 (25.1455)
                실현 = _원(체결.cash_krw - 보유.cost_krw * o.quantity / 보유.quantity)
            if 보유 is None or 보유.quantity < o.quantity:
                _쓰기(client, [(EXPIRE_SQL, [trade_date, "팔 보유가 모자람", o.id])])
                기록["expired"] = 기록.get("expired", 0) + 1
                continue
        _쓰기(client, [
            (INSERT_FILL_SQL, [체결.order_id, 체결.account_id, 체결.stock_id, 체결.side, 체결.fill_date, 체결.price,
                               체결.quantity, 체결.currency, 체결.fx_rate, 환율날, 체결.fee, 체결.tax, 체결.slippage,
                               체결.cash_krw, 체결.horizon, 체결.score_at_trade, 체결.snapshot_as_of, now,
                               그날종가, 실현]),
            (FILLED_SQL, [trade_date, 까닭, o.id]),
        ])  # fmt: skip
        새것.append(체결)
        기록["filled"] = 기록.get("filled", 0) + 1
    return 새것


def _stock(client: Any, sid: int, 캐시: dict[int, dict[str, Any]]) -> dict[str, Any]:
    if sid not in 캐시:
        rows = client.execute(STOCK_SQL, [sid]).dicts()
        캐시[sid] = rows[0] if rows else {"id": sid, "ticker": str(sid), "name": str(sid), "sector": None,
                                          "country": None, "currency": None, "asset_type": "stock"}  # fmt: skip
    return 캐시[sid]


def _열린_그림자(
    client: Any, flows: list[pp.CashFlow], shadows: dict[str, tuple[dict[int, float | None], float | None]],
    opened: str, cache: dict[tuple[str, str], float | None],
) -> dict[str, tuple[dict[int, float | None], float | None]]:
    """늦게 연 계좌(G·H 등)의 그림자 기준 — 계좌를 열기 전의 입금은 **연 날**의 지수로 산 것으로 본다
    (25.1484, 검증 [권장] 1).

    입금 장부는 모든 계좌 공통이라 그대로 두면 계좌가 생기기 전의 시장 움직임까지 "같은 돈 KOSPI" 에 들어가,
    연 첫날부터 지수 대비 몇 %p 뒤진 것으로 보였다. 처음부터 있던 계좌(열린 날 ≤ 입금일)는 바뀌지 않는다
    """
    if not opened or not any(f.flow_date < opened for f in flows):
        return shadows
    out: dict[str, tuple[dict[int, float | None], float | None]] = {}
    for code, (lv, now_lv) in shadows.items():
        if (code, opened) not in cache:
            가짜 = [pp.CashFlow(-1, opened, 1.0, "deposit")]
            cache[(code, opened)] = _level_of(client, 가짜, code, fx=code == "SP500")[0].get(-1)
        시작 = cache[(code, opened)]
        out[code] = ({f.id: (시작 if f.flow_date < opened else lv.get(f.id))
                      for f in flows if f.kind == "deposit"}, now_lv)  # fmt: skip
    return out


def _level_of(
    client: Any, flows: list[pp.CashFlow], code: str, fx: bool
) -> tuple[dict[int, float | None], float | None]:
    """그림자 기준 — 입금마다 그 돈이 처음 투자 가능해진 장의 원화 기준 값, 그리고 지금 값 (docs/paper.md 4장)."""
    지금 = client.execute(INDEX_NOW_SQL, [code]).rows
    if not 지금:
        return {}, None
    지금값, 지금날 = float(지금[0][0]), str(지금[0][1])
    if fx:
        환 = _fx(client, 지금날)
        if 환 is None:
            return {}, None
        지금값 *= 환[0]
    out: dict[int, float | None] = {}
    for f in flows:
        if f.kind != "deposit":
            continue
        행 = client.execute(INDEX_FROM_SQL, [code, f.flow_date]).rows
        if not 행:
            # 아직 장이 닫히지 않았다 — 지금 값으로 산 것으로 본다(평가 = 넣은 돈). 다음 날 진짜 값으로 바뀐다
            out[f.id] = 지금값
            continue
        값 = float(행[0][0])
        if fx:
            환 = _fx(client, str(행[0][1]))
            값 = 값 * 환[0] if 환 else 0.0
        out[f.id] = 값 or None
    return out, 지금값


def real_comparison(client: Any) -> dict[str, Any] | None:
    """모의 계좌와 실제 포트폴리오를 같은 기간 시간가중으로 (docs/paper.md 4장, 2026-10-15 사용자 지시 25.1448).

    읽기만 한다. 실제 포트폴리오가 없거나 표를 못 읽으면 None — 리포트는 그 줄만 빠진다
    """
    모의: dict[str, list[tuple[str, float]]] = {}
    for r in client.execute(PAPER_TWR_SQL).dicts():
        모의.setdefault(str(r["account_id"]), []).append((str(r["as_of"]), float(r["twr_index"])))
    if not 모의:
        return None
    처음 = min(v[0][0] for v in 모의.values())
    부터 = (date.fromisoformat(처음) - timedelta(days=REAL_LOOKBACK_DAYS)).isoformat()
    실제 = [(str(r[0]), float(r[1])) for r in client.execute(REAL_TWR_SQL, [부터]).rows if r[1] is not None]
    return pp.compare_real(모의, 실제)


def save_attribution(client: Any, aid: str, today: str, rule_version: int = 1) -> dict[str, Any]:
    """그 계좌의 성과 분해를 내 오늘 평가 행에 적는다 (docs/paper.md 10장, 25.1450).

    투자사(판 2)면 부서 성적표도 — 결정 원장의 부서 입장과 20 거래일 지수 대비 초과로 (docs/company.md 6장, 25.1472)
    """
    부터 = (date.fromisoformat(today) - timedelta(days=ATTR_WINDOW_DAYS)).isoformat()
    결정 = client.execute(ATTR_DECISIONS_SQL, [aid, 부터, rule_version]).dicts()
    체결행 = client.execute(ATTR_FILLS_SQL, [aid, 부터, rule_version]).dicts()
    a = pp.attribution(결정, [(float(r["price"]), r["day_close"]) for r in 체결행])
    성적 = department_scorecard(결정)
    if 성적:
        a["departments"] = 성적
    # 결정 단위 선정 증거(docs/paper.md 10.1, 25.1518) — 같은 행으로(읽기 0). 관문·챔피언 비교가 운용 요약에서 읽는다
    a["evidence"] = pp.selection_evidence(결정)
    a["rule_version"] = rule_version  # 이 판의 결정만 셌다 — 화면이 "판을 올리면 처음부터" 를 말한다(25.1602)
    _쓰기(client, [(SAVE_ATTR_SQL, [json.dumps(a, ensure_ascii=False), aid, today])])
    return a


def department_scorecard(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """결정 원장 → 부서 성적표 (docs/company.md 6장, 25.1472). 위원회 기록이 없는 계좌(B·R)는 빈 목록.

    성과는 % 단위(`ret_20d`·`bench_20d`)라 소수로 바꿔 `company.scorecard` 에 넘긴다. 한 회사 안에서는 판단식 매개변수가
    하나라 열쇠를 비운다
    """
    행: list[dict[str, Any]] = []
    for d in decisions:
        try:
            위원회 = (json.loads(d.get("detail_json") or "{}") or {}).get("committee") or {}
        except ValueError:
            continue
        if d.get("ret_20d") is None or d.get("bench_20d") is None:
            continue
        초과 = (float(d["ret_20d"]) - float(d["bench_20d"])) / 100
        for 부서, 입장 in (위원회.get("opinions") or {}).items():
            자료없음 = len(입장) > 2 and str(입장[2]).startswith("자료 없음")
            행.append({"dept": 부서, "param": "", "stock_id": d["stock_id"], "day": d["decided_on"],
                      "stance": None if 자료없음 else int(입장[0]), "excess": 초과})  # fmt: skip
    return [{"dept": k[0], **v} for k, v in sorted(co.scorecard(행).items())]


def mark(
    client: Any, account: str, today: str, flows: list[pp.CashFlow], flow_created: dict[int, str],
    fills: list[pp.Fill], fill_created: dict[int, str], new_fills: int, frozen: set[int],
    stock_cache: dict[int, dict[str, Any]], shadows: dict[str, tuple[dict[int, float | None], float | None]], now: str,
) -> tuple[pp.Valuation, dict[str, Any]]:
    """평가·검산·시간가중·그림자 기준을 `paper_daily` 에 (한국 날짜 하루 한 행, 뒤 실행이 덮는다)."""
    계좌체결 = [f for f in fills if f.account_id == account]
    보유 = pp.positions(계좌체결)
    종가: dict[int, tuple[float, str] | None] = {}
    for sid in 보유:
        행 = client.execute(LAST_CLOSE_SQL, [sid, today]).rows
        종가[sid] = (float(행[0][0]), str(행[0][1])) if 행 else None
    fx = _fx(client, today)
    v = pp.value(flows, 계좌체결, 종가, {"USD": fx[0] if fx else None}, frozen)
    어제 = client.execute(PREV_DAILY_SQL, [account, today]).dicts()
    p = 어제[0] if 어제 else None
    if p is None:
        ok, 글 = True, "첫 기록"
        지수 = 1.0
    else:
        기준 = str(p["updated_at"])
        새입금 = sum(f.amount_krw for f in flows if pp.after(flow_created.get(f.id, ""), 기준))
        새체결 = sum(f.cash_krw for f in 계좌체결 if pp.after(fill_created.get(f.order_id, now), 기준))
        ok, 글 = pp.reconcile(float(p["cash_krw"]), 새입금, 새체결, v.cash_krw)
        # **어제 행을 적은 뒤에 들어온 입금**이 흐름이다 (25.1447, 교차검증 [필수] 1) — 입금 날짜(`flow_date > as_of`)로
        # 고르면 미국 배치 뒤(같은 한국 날짜) 넣은 돈이 흐름에서 빠져 평가에만 더해져 지수가 +100% 로 뛰었다
        지수 = pp.twr_step(float(p["twr_index"]), float(p["equity_krw"]), v.equity_krw, 새입금)
    그림자 = {code: pp.shadow(flows, *shadows[code]) if code in shadows else None for code in ("KOSPI", "SP500")}
    _쓰기(client, [(UPSERT_DAILY_SQL, [
        account, today, v.invested_krw, v.cash_krw, v.holdings_krw, v.equity_krw, v.pnl_krw, 지수,
        그림자["KOSPI"], 그림자["SP500"], json.dumps(v.rows, ensure_ascii=False), new_fills, int(ok), 글,
        json.dumps(v.unknown, ensure_ascii=False) if v.unknown else None, now,
        # 계좌의 실현손익 합 · 보유 평가손익 합 (25.1455)
        float(sum(r["pnl_krw"] for r in pp.realized(계좌체결))), float(sum(r["unrealized_krw"] for r in v.rows)),
    ])])  # fmt: skip
    return v, {"recon_ok": ok, "recon_note": 글, "twr": 지수, "kospi": 그림자["KOSPI"], "sp500": 그림자["SP500"],
               "prev_twr": float(p["twr_index"]) if p else None}


def sell_decisions(
    client: Any, market: str, sell_rule: str, account_fills: list[pp.Fill], frozen: set[int],
    stock_cache: dict[int, dict[str, Any]], targets: dict | None, today: date, enrich_cache: dict[str, Any],
    notes: dict[int, dict[str, Any]] | None = None,
) -> dict[int, tuple[list[str], list[dict[str, Any]]]]:
    """이 시장 보유의 매도 판정 — 실제 보유와 같은 보강·같은 `evaluate` (docs/paper.md 2.2).

    `notes` 를 주면 종목마다 {ref: (종가, 날짜), hold: [계좌 규칙이 따르지 않은 플래그], flags: 근거} 를 채운다
    — 결정 원장용(25.1449)
    """
    from batch.jobs import sell_flags as sf_job

    행들: list[dict[str, Any]] = []
    for sid, p in sorted(pp.positions(account_fills).items()):
        s = _stock(client, sid, stock_cache)
        if s.get("country") != market or sid in frozen or s.get("asset_type") not in (None, "stock"):
            continue
        종가 = client.execute(LAST_CLOSE_SQL, [sid, today.isoformat()]).rows
        점수 = client.execute(SCORE_NOW_SQL, [sid]).rows
        로트 = [(f.fill_date, float(f.quantity), f.price) for f in account_fills
                if f.stock_id == sid and f.side == "buy" and f.fill_date >= p.first_buy_date]  # fmt: skip
        행들.append({
            "stock_id": sid, "name": s.get("name"), "country": market, "horizon": p.horizon,
            "first_buy_date": p.first_buy_date, "currency": p.currency, "cost": p.cost,
            "market_value": float(종가[0][0]) * p.quantity if 종가 else None,
            "price_date": str(종가[0][1]) if 종가 else None,
            "score_now": 점수[0][0] if 점수 else None, "score_now_date": 점수[0][1] if 점수 else None,
            "score_at_buy": p.score_at_buy, "score_at_buy_date": p.score_at_buy_date,
            "horizon_since": p.first_buy_date, "lots": 로트, "quantity": p.quantity,
        })  # fmt: skip
    out: dict[int, tuple[list[str], list[dict[str, Any]]]] = {}
    for 행 in 행들:
        # A·B 가 같은 로트를 들고 있으면 한 번만 보강한다 — 읽기를 두 번 쓰지 않는다
        열쇠 = json.dumps({k: v for k, v in 행.items() if k != "quantity"}, sort_keys=True, default=str)
        if 열쇠 not in enrich_cache:
            입력, 경고 = sf_job.enrich(client, [행], today, dividends=False)
            enrich_cache[열쇠] = (입력[0] if 입력 else None, 경고)
        h, _경고 = enrich_cache[열쇠]
        if h is None:
            continue
        flags = sf.evaluate(h, today, targets)
        코드들 = {f.reason_code for f in flags}
        if sell_rule == "trend" and _추적손절(client, h, 행, targets, today):
            코드들.add(pp.TRAIL_CODE)
            글 = "목표에 닿은 뒤 고점 대비 손절폭만큼 빠짐 (추세 익절, docs/paper.md 2.2)"
            flags = [*flags, sf.Flag(stock_id=h.stock_id, reason_code=pp.TRAIL_CODE, level="red", rationale_text=글)]
        팔것 = pp.sells_for(sell_rule, {h.stock_id: 코드들})
        근거 = [{"reason_code": f.reason_code, "level": f.level, "rationale": f.rationale_text,
                "criteria": f.criteria} for f in flags]  # fmt: skip
        if 팔것:
            out[h.stock_id] = (팔것[h.stock_id], 근거)
        if notes is not None and flags:
            따름 = set(팔것.get(h.stock_id, []))
            notes[h.stock_id] = {"ref": (행["market_value"] / 행["quantity"] if 행["market_value"] else None,
                                         행["price_date"]),
                                 "hold": sorted({f.reason_code for f in flags} - 따름), "flags": 근거}  # fmt: skip
    return out


#: 추세 익절(G)의 고점 — 첫 매수일부터 오늘까지 종가 최댓값 (보유마다 한 번, 25.1481)
PEAK_SQL = "SELECT MAX(close) FROM prices WHERE stock_id = ? AND date >= ? AND date <= ? AND close > 0"


def _추적손절(client: Any, h: Any, 행: dict[str, Any], targets: dict | None, today: date) -> bool:
    """G 의 추적 손절 판정 — 고점은 따로 읽는다(`enrich` 는 시세 이력을 읽지 않는다).

    목표·손절은 `evaluate` 와 같은 표에서
    """
    if not 행.get("quantity") or not 행.get("market_value"):
        return False
    고점 = client.execute(PEAK_SQL, [h.stock_id, 행["first_buy_date"], today.isoformat()]).rows
    기간 = h.horizon if h.horizon in sf.DEFAULT_TARGETS else "long"
    표 = {**sf.DEFAULT_TARGETS[기간], **((targets or {}).get(기간) or {})}
    return pp.trend_exit(float(행["cost"]) / float(행["quantity"]), 고점[0][0] if 고점 else None,
                         float(행["market_value"]) / float(행["quantity"]), float(표["target_pct"]),
                         float(표["stop_pct"]), float(getattr(h, "rights_scale", 1.0) or 1.0))  # fmt: skip


#: 살 때부터 매도 규칙에 걸리는지 미리 보는 계좌 규칙 — 보유와 무관한 플래그(재무악화·감성급락·점검·상장폐지)로 판다.
#: `core`·`time` 은 손절·목표·기간(보유가 있어야 서는 것)만 따라 산 날 걸릴 수 없다 (25.1480)
PREBUY_CHECK_RULES = frozenset({"all", "value", "momentum"})


def prebuy_sell_flags(
    client: Any, market: str, sell_rule: str, rows: list[report_picks.SignalRow], held: set[int], frozen: set[int],
    stock_cache: dict[int, dict[str, Any]], targets: dict | None, today: date, enrich_cache: dict[str, Any],
) -> dict[int, list[str]]:
    """사면 **다음 배치에 바로 팔** 후보 — {종목: 걸린 사유}. 보유처럼 꾸며(오늘 종가에 1주, 점수 기준점 = 신호 점수)
    `sell_decisions` 와 같은 보강·같은 `evaluate`·같은 계좌 규칙으로 본다.

    매수 쪽이 매도 규칙을 보지 않아, 이미 적자 전환 공시가 난 종목을 A(value)·C(momentum)가 사고 다음 날 재무악화로 팔고
    쉼(5거래일) 뒤 위원회가 또 가결해 사는 회전이 생겼다 — 왕복 비용이 A−B·C−B 비교에 섞인다 (25.1480, 감사 [필수] 1).
    보유 중인 종목은 `sell_decisions` 가 이미 보았다(팔면 쉼에 든다)
    """
    if sell_rule not in PREBUY_CHECK_RULES:
        return {}
    from batch.jobs import sell_flags as sf_job

    out: dict[int, list[str]] = {}
    for r in {r.stock_id: r for r in rows}.values():
        s = _stock(client, r.stock_id, stock_cache)
        if r.stock_id in held or r.stock_id in frozen or s.get("country") != market:
            continue
        종가 = client.execute(LAST_CLOSE_SQL, [r.stock_id, today.isoformat()]).rows
        if not 종가:
            continue
        점수 = client.execute(SCORE_NOW_SQL, [r.stock_id]).rows
        값 = float(종가[0][0])
        행 = {
            "stock_id": r.stock_id, "name": s.get("name"), "country": market, "horizon": r.horizon,
            "first_buy_date": today.isoformat(), "currency": r.currency, "cost": 값, "market_value": 값,
            "price_date": str(종가[0][1]), "score_now": 점수[0][0] if 점수 else None,
            "score_now_date": 점수[0][1] if 점수 else None, "score_at_buy": r.total_score,
            "score_at_buy_date": r.as_of_date, "horizon_since": today.isoformat(),
            "lots": [(today.isoformat(), 1.0, 값)], "quantity": 1,
        }  # fmt: skip
        열쇠 = json.dumps({k: v for k, v in 행.items() if k != "quantity"}, sort_keys=True, default=str)
        if 열쇠 not in enrich_cache:
            입력, 경고 = sf_job.enrich(client, [행], today, dividends=False)
            enrich_cache[열쇠] = (입력[0] if 입력 else None, 경고)
        h, _경고 = enrich_cache[열쇠]
        if h is None:
            continue
        팔것 = pp.sells_for(sell_rule, {h.stock_id: {f.reason_code for f in sf.evaluate(h, today, targets)}})
        if 팔것:
            out[h.stock_id] = 팔것[h.stock_id]
    return out


#: 무작위 대조(R)가 고르는 모집단 — 그 나라 최신 주간 유니버스의 편입 개별주 (docs/paper.md 2.1.2, 25.1458)
#: 스냅샷 날짜 서브쿼리는 `db.latest_snapshot_sql()` 과 같은 글이다(동적 질의를 늘리지 않으려고 적어 둔다 — 시험이 대조)
UNIVERSE_SQL = (
    "SELECT u.stock_id FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
    " WHERE s.country = ? AND u.included = 1 AND s.asset_type = 'stock'"
    " AND u.snapshot_date = (SELECT um.snapshot_date FROM universe_members um"
    " CROSS JOIN stocks su ON su.id = um.stock_id WHERE su.country = ? ORDER BY um.snapshot_date DESC LIMIT 1)"
)


#: 무작위 대조가 한 자리에서 자료 문제(묵은 종가·개별주 아님·멈춤)로 건너뛰는 최대 후보 수 — 시세가 통째로 묵은 날
#: 모집단 전체를 한 종목씩 읽지 않게 (25.1458, 검증 권장). 넘으면 그 자리를 비우고 까닭을 적는다
RANDOM_MAX_TRIES = 50


def random_substitute(
    client: Any, rows: list[report_picks.SignalRow], *, account: str, market: str, pool: list[int],
    cooldown: set[int], stock_cache: dict[int, dict[str, Any]], today: date, 기록: dict[str, Any],
    held: dict[int, int] | None = None,
) -> list[report_picks.SignalRow]:
    """고른 신호 행의 종목을 무작위 종목으로 바꾼다 (docs/paper.md 2.1.2). 비중·기간·회차 비율은 그대로.

    씨앗은 `계좌|시장|신호 종목` — 같은 신호 종목은 늘 같은 대체 종목으로 간다(C 가 X 를 채우는 날 R 은 Y 를 채운다).
    **자료 문제**(개별주 아님·종가가 묵음·기업행위 멈춤)는 씨앗 순서의 다음 후보로 간다. **계좌 상태**(판 뒤 쉼
    `cooldown`)는 다시 뽑지 않고 그 자리를 비운다 — C 가 X 를 쉬는 날과 같은 뜻이다. 다시 뽑으면 R 만 쉬는 동안
    다른 종목을 사고 쉼이 끝나면 또 사서 종목 수·투자 속도가 C 와 갈렸다(검증 [필수] 3).
    **이미 든 대체 종목**(`held` = {신호 종목: 그 자리로 사서 지금 든 종목}, 주문 근거의 `substitute_of`)이 있으면
    씨앗을 다시 보지 않고 그 종목을 잇는다 — 그 종목이 자료 문제(멈춤·묵은 종가·유니버스에서 빠짐)에 걸리면
    **자리를 비운다**. 다음 후보로 넘어가면 든 종목은 원가로 남은 채 새 종목을 또 사서, 그 자리만 R 의 돈이
    두 배가 됐다(25.1458 재검증 [필수] 1).
    같은 날 다른 자리가 고른 종목·다른 자리가 이미 든 종목은 건너뛴다.
    `기록["random"]` 에 {신호 종목: 대체 종목}, 빈 자리는 `기록["random_empty"]` 에 까닭과 함께
    """
    held = held or {}
    바꿈: dict[int, int] = {}
    빈자리: dict[str, str] = {}
    기준일 = {r.stock_id: (r.close_date or today.isoformat())[:10] for r in rows}

    def 자료(c: int, sid: int) -> dict[str, Any] | None:
        """자료가 맞으면 종목 정보(종가 포함), 아니면 None."""
        s = _stock(client, c, stock_cache)
        if s.get("country") != market or s.get("asset_type") not in (None, "stock"):
            return None
        # 기준 종가는 **신호의 종가 날짜까지** — C 의 기준가와 같은 시점
        # (검증 권장: 장 끝난 뒤 손 실행에서 하루 어긋남)
        종가 = client.execute(LAST_CLOSE_SQL, [c, 기준일[sid]]).rows
        if not 종가 or (today - date.fromisoformat(str(종가[0][1])[:10])).days > rg.STALE_PRICE_DAYS:
            return None
        if c in frozen_ids(client, {c}, {c: market}):
            return None
        return {**s, "_close": (float(종가[0][0]), str(종가[0][1]))}

    남의것 = set(held.values())
    for sid in dict.fromkeys(r.stock_id for r in rows):
        if sid in held:
            c = held[sid]
            정보 = 자료(c, sid)
            if 정보 is None:
                빈자리[str(sid)] = f"든 대체 종목 {c} 자료 문제(멈춤·묵은 종가) — 새 종목을 사지 않음"
            elif c in cooldown:
                빈자리[str(sid)] = f"대체 종목 {c} 판 뒤 {pp.REBUY_COOLDOWN_SESSIONS}거래일 안"
            else:
                stock_cache[c] = 정보
                바꿈[sid] = c
            continue
        까닭 = f"자료가 맞는 후보를 {RANDOM_MAX_TRIES}개 안에서 못 찾음"
        시도 = 0
        for c in pp.random_order(pool, f"{account}|{market}|{sid}"):
            if c in 바꿈.values() or c in 남의것:
                continue
            if 시도 >= RANDOM_MAX_TRIES:
                break
            시도 += 1
            정보 = 자료(c, sid)
            if 정보 is None:
                continue
            if c in cooldown:
                까닭 = f"대체 종목 {c} 판 뒤 {pp.REBUY_COOLDOWN_SESSIONS}거래일 안"
                break
            stock_cache[c] = 정보
            바꿈[sid] = c
            break
        if sid not in 바꿈:
            빈자리[str(sid)] = 까닭
    out: list[report_picks.SignalRow] = []
    for r in rows:
        c = 바꿈.get(r.stock_id)
        if c is None:
            continue
        s = stock_cache[c]
        종가, 날 = s["_close"]
        out.append(dc_replace(
            r, stock_id=c, ticker=str(s.get("ticker")), name=str(s.get("name")), sector=s.get("sector"),
            close=종가, close_date=날, total_score=None, rank_in_market=None, factor_scores={}, sentiment=None,
            buy_zone_low=종가, buy_zone_high=종가, cagr=None, mdd=None, sharpe=None, perf_window=None, perf_as_of=None,
            rationale_text=f"무작위 대조 — 원래 자리: {r.name}({r.ticker})",
            # 근거표 모양은 sell_flags._criterion 과 같다 — CriteriaTable 이 display·threshold 를 읽는다 (검증 권장)
            criteria=[{**sf._criterion("무작위 대조",
                                       f"원래 자리 {r.name}({r.ticker}) · 비중 {r.suggested_weight_pct}%",
                                       "추천이 아니라 대조 실험 (docs/paper.md 2.1.2)", "paper", 날),
                       "substitute_of": r.stock_id}],
            # 회차 가격 키는 신호의 `price` — build_portfolio 의 1주 검사가 이것을 읽는다(검증 [필수] 2)
            tranche_plan=[{**t, "price": 종가, "price_at_or_below": 종가} for t in r.tranche_plan],
        ))  # fmt: skip
    기록["random"] = {str(k): v for k, v in 바꿈.items()}
    if 빈자리:
        기록["random_empty"] = 빈자리
    return out


#: 무작위 대조 계좌가 산 매수 주문의 근거 — 어느 신호 자리로 산 종목인가(`substitute_of`)를 다시 읽는다
SUBSTITUTE_ORDERS_SQL = (
    "SELECT stock_id, rationale_json FROM paper_orders WHERE account_id = ? AND side = 'buy' AND status = 'filled'"
    " ORDER BY id"
)


def held_substitutes(client: Any, account: str, holding: set[int]) -> dict[int, int]:
    """{신호 종목: 그 자리로 사서 지금 든 종목} — 무작위 대조가 든 종목을 잇게 (25.1458 재검증 [필수] 1).

    나중 주문이 이긴다
    """
    out: dict[int, int] = {}
    for r in client.execute(SUBSTITUTE_ORDERS_SQL, [account]).dicts():
        if int(r["stock_id"]) not in holding:
            continue
        try:
            근거 = json.loads(r["rationale_json"] or "{}")
            원래 = next((c.get("substitute_of") for c in 근거.get("signal", {}).get("criteria") or []
                         if isinstance(c, dict) and c.get("substitute_of") is not None), None)  # fmt: skip
        except (ValueError, AttributeError):
            continue
        if 원래 is not None:
            out[int(원래)] = int(r["stock_id"])
    return out


def rescale_rows(rows: list[report_picks.SignalRow], total: float, min_order: float) -> list[report_picks.SignalRow]:
    """신호 금액을 **모의 계좌 평가 총액 × 신호 비중**으로 다시 만든다 (docs/paper.md 2.1, 검증 [필수] 1).

    회차 금액도 같은 비율(40/30/30)로. 최소 주문 금액 미만이면 신호와 같이 비운다(`signals.py` 와 같은 규칙)
    """
    out: list[report_picks.SignalRow] = []
    for r in rows:
        금액 = total * float(r.suggested_weight_pct) / 100 if r.suggested_weight_pct and total > 0 else None
        if 금액 is not None and (금액 < min_order or 금액 <= 0):
            금액 = None
        회차 = [{**t, "amount": (금액 * float(t.get("ratio") or 0)) if 금액 is not None else None}
                for t in r.tranche_plan]  # fmt: skip
        out.append(dc_replace(r, suggested_amount=금액, tranche_plan=회차))
    return out


#: 열린 매수 주문이 있는 종목을 다시 고르지 않은 까닭(25.1549) — 결정 원장 skip
OPEN_BUY_SKIP_REASON = "열린 매수 주문 있음 — 같은 종목을 두 벌 사지 않음"


def buy_orders(
    client: Any, *, market: str, account: str, account_fills: list[pp.Fill], valuation: pp.Valuation,
    open_buys: list[pp.Order], signal_rows: list[report_picks.SignalRow], settings: dict[str, Any],
    price_series: dict[int, dict[str, float]], series_unknown: dict[int, str], fx_rate: float | None,
    stop_ids: set[int], sold_recently: set[int], stock_cache: dict[int, dict[str, Any]],
    top_n: int = report_picks.TOP_STOCKS, entry: str = "tranche",
    substitute: Callable[[list[report_picks.SignalRow]], list[report_picks.SignalRow]] | None = None,
    series_loader: Callable[[list[int]], dict] | None = None,
    preselected: list[report_picks.SignalRow] | None = None, preselected_reason: str = "위원회 가결",
    costs: dict[str, Any] | None = None,
    stop_pcts: dict[str, float] | None = None,
    frozen: set[int] | frozenset[int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """2부와 같은 함수로 이 계좌의 매수 회차 주문을 낸다 (docs/paper.md 2.1). (주문 행, 기록).

    `preselected` 는 투자위원회가 가결한 종목의 신호 행(표 순) — 있으면 점수 상위 `select_top` 대신 그것을 배분한다
    (docs/company.md 3장 7, 25.1466).

    계좌 칸(25.1458): `top_n` 상위 몇 종목(E 10), `entry` tranche·open(시가 일괄, 2.1.1),
    `substitute` 는 고른 신호 행을 무작위 종목으로 바꾼다(R, 2.1.2). 2부가 시세를 읽지 않은 후보(6~10위·무작위 종목)는
    `series_loader` 로 더 읽어 같은 상관 축소를 받는다

    `stop_pcts`({기간: 손절 %}, 그날 매도 판정이 쓰는 값)를 주면 산 결정 행에 손절 거리 σ 단위(`detail.vol`, 25.1539)를
    적는다 — 기록만, 주문은 그대로. 핵심 H 백테스트는 주지 않는다
    """
    통화 = "KRW" if market == "KR" else "USD"
    환율 = 1.0 if 통화 == "KRW" else fx_rate
    if not 환율:
        return [], {"skipped": "환율 없음"}
    예약 = pp.reserved_krw(open_buys, {"USD": fx_rate})
    if 예약 is None:
        return [], {"skipped": "미체결 달러 주문의 환율 모름"}
    if valuation.cash_krw < 0:
        return [], {"skipped": "장부 현금이 음수"}
    # **아직 안 찬 매수 주문도 보유처럼 센다**(25.1549 감사 [필수] 1) — 예전엔 현금 예약에만 들어가
    # 종목·섹터 상한 계산에서
    # 빠졌다. 장 끝난 뒤 손 실행 다음 아침·어제 시세가 늦은 아침에 같은 장(fill_session)으로 같은 종목 회차가 두 벌 나가
    # 종목 비중이 19% 까지 갔다(KIS모의투자도 그대로 베꼈다)
    열린금액 = pp.reserved_by_stock(open_buys, {"USD": fx_rate}) or {}
    평가 = {int(r["stock_id"]): float(r["value_krw"]) for r in valuation.rows}
    for sid, x in 열린금액.items():
        평가[sid] = 평가.get(sid, 0.0) + x
    보유 = [
        report_picks.Holding(
            stock_id=sid, ticker=str(_stock(client, sid, stock_cache).get("ticker")),
            name=str(_stock(client, sid, stock_cache).get("name")),
            sector=_stock(client, sid, stock_cache).get("sector"), value=x / 환율,
            country=_stock(client, sid, stock_cache).get("country"),
            asset_type=str(_stock(client, sid, stock_cache).get("asset_type") or "stock"),
        )  # fmt: skip
        for sid, x in 평가.items()
    ]
    총액 = valuation.equity_krw / 환율
    고른 = (preselected if preselected is not None
            else report_picks.select_top(report_picks.with_criteria(signal_rows), top_n))  # fmt: skip
    # 열린 매수 주문이 있는 종목은 새로 사지 않는다 — 같은 종목을 두 벌 사지 않게(25.1549).
    # 결정 원장에는 까닭과 함께 skip
    열린빠짐 = [r for r in 고른 if r.stock_id in 열린금액]
    고른 = [r for r in 고른 if r.stock_id not in 열린금액]
    후보수 = len({r.stock_id for r in 고른})  # 신호 종목이 top_n 보다 적은 날은 E 가 B 와 같다 — 기록에 남긴다
    다시 = rescale_rows(고른, 총액, float(settings.get("min_order_amount") or 0))
    if substitute is not None:
        다시 = substitute(다시)
        # 무작위 대체 종목에도 같은 막이 — 신호 종목으로 거른 뒤 바꾸므로 대체 종목의 열린 주문을 피해 갔다
        # (25.1550 [참고] 1)
        열린빠짐 += [r for r in 다시 if r.stock_id in 열린금액]
        다시 = [r for r in 다시 if r.stock_id not in 열린금액]
    # 판 뒤 쉼·기업행위 멈춤(계좌별) 종목은 **배분 전에** 뺀다 — 예전엔 배분이 그 몫에 여력·섹터 자리를 먼저 주고
    # `size_buys`·위험 점검에서야 걸러, 살 수 있는 다른 후보가 "섹터 상한 도달"·"투자 여력 부족" 으로 빠졌다(25.1618,
    # 매수 배분 감사 [필수]). 그 자리는 현금으로 남는다(다음 후보로 채우지 않는다 — 고른 종목 수는 그대로)
    # 손절이 선 종목은 그대로 둔다 — 배분(`stop_flags`)이 "보유 중 손절선 이탈" 로 빼 자리를 먹지 않고, 그 까닭이 쉼보다
    # 앞선다(핵심 H 백테스트 `test_core_strategy_1532` 가 지키는 순서)
    멈춤 = set(frozen or ()) - set(stop_ids)
    쉼 = set(sold_recently) - set(stop_ids)
    쉼빠짐 = [r for r in 다시 if r.stock_id in 쉼]
    멈춤빠짐 = [r for r in 다시 if r.stock_id in 멈춤 and r.stock_id not in 쉼]
    다시 = [r for r in 다시 if r.stock_id not in 쉼 and r.stock_id not in 멈춤]
    빠진후보 = sorted({r.stock_id for r in 다시} - set(price_series))
    if 빠진후보 and series_loader is not None:
        price_series = {**price_series, **series_loader(빠진후보)}
    # 미체결 매수는 위 보유 목록에 이미 들어갔다 — 예약으로 또 빼면 남은 여력에서 두 번 빠진다(25.1550 반박 [필수] 1:
    # 국내 아침엔 늘 전날 밤 미국 매수가 열려 있어 평상시 매일 그만큼 여력이 깎였다). 보유 목록에 못 넣은 몫만 예약으로
    남은예약 = max(0.0, 예약 - sum(열린금액.values()))
    pv = report_picks.build_portfolio(
        다시, 총액, currency=통화, max_sector_pct=settings["max_weight_per_sector"], holdings=보유,
        max_stock_pct=settings["max_weight_per_stock"], price_series=price_series, series_unknown=series_unknown,
        min_order_amount=float(settings.get("min_order_amount") or 0), fx_missing=False, reserved=남은예약 / 환율,
        stop_flags={sid: "가상모의투자 계좌 손절 판정" for sid in stop_ids},
    )  # fmt: skip
    by_ticker: dict[str, list[report_picks.SignalRow]] = {}
    for r in 다시:
        by_ticker.setdefault(r.ticker, []).append(r)
    배분: list[dict[str, Any]] = []
    for a in pv.allocations:
        행 = by_ticker.get(a.ticker) or []
        if not 행:
            continue
        best = max(행, key=lambda r: (r.suggested_amount or 0, -report_picks._기간_순.get(r.horizon, 9)))
        배분.append({"stock_id": best.stock_id, "amount": a.amount, "tranches": a.tranches, "row": best, "alloc": a,
                     "ref_price": best.close})  # fmt: skip
    주문, 빠짐 = pp.size_buys(배분, valuation.cash_krw - 예약, 환율, sold_recently, 통화, entry)
    사유 = "무작위 대조" if substitute is not None else preselected_reason if preselected is not None else "2부 배분"
    by_sid = {b["stock_id"]: b for b in 배분}
    # 결정 원장 (25.1449) — 산 후보·못 산 후보(까닭)와 그때의 기준 종가
    결정: list[dict[str, Any]] = []
    낸 = {o["stock_id"] for o in 주문}
    # 결정 때 이미 든 종목(상한까지 채우는 추가 매수) — 결정 단위 선정 증거가 새 선정만 세게 적는다(25.1570)
    든 = {int(r["stock_id"]) for r in valuation.rows if float(r.get("value_krw") or 0) > 0}
    for b in 배분:
        r0: report_picks.SignalRow = b["row"]
        까닭 = next((x["why"] for x in 빠짐 if x.get("stock_id") == b["stock_id"]), "주문 없음")
        결정.append({"stock_id": b["stock_id"], "kind": "buy" if b["stock_id"] in 낸 else "skip",
                     "reason": 사유 if b["stock_id"] in 낸 else 까닭,
                     "ref_close": r0.close, "ref_date": r0.close_date,
                     "detail": {"horizon": r0.horizon, "score": r0.total_score, "weight_pct": r0.suggested_weight_pct,
                                "amount": b["amount"], "entry": entry, "held": b["stock_id"] in 든,
                                "prices": [t.get("price_at_or_below") for t in b["tranches"]]}})  # fmt: skip
        if stop_pcts is not None and b["stock_id"] in 낸:
            # 손절 거리 σ 단위 (25.1539, 31회차 P2) — 2부가 든 상관 계열 그대로(읽기 0). 분할 재수집 대기는 모름
            결정[-1]["detail"]["vol"] = (
                {"unknown": "분할 재수집 대기"} if b["stock_id"] in series_unknown
                else sv.stop_vol(price_series.get(b["stock_id"]), stop_pcts.get(r0.horizon or "long"), market)
            )  # fmt: skip
    for r0 in {r.stock_id: r for r in 열린빠짐}.values():
        결정.append({"stock_id": r0.stock_id, "kind": "skip", "reason": OPEN_BUY_SKIP_REASON, "ref_close": r0.close,
                     "ref_date": r0.close_date,
                     "detail": {"horizon": r0.horizon, "score": r0.total_score}})  # fmt: skip
    for 줄들, kind, 까닭 in ((쉼빠짐, "skip", f"판 뒤 {pp.REBUY_COOLDOWN_SESSIONS}거래일 안"),
                          (멈춤빠짐, "blocked", "위험 점검: 기업행위 경계(분할·병합 의심)")):  # 예전 까닭 글 그대로
        for r0 in {r.stock_id: r for r in 줄들}.values():
            결정.append({"stock_id": r0.stock_id, "kind": kind, "reason": 까닭, "ref_close": r0.close,
                         "ref_date": r0.close_date,
                         "detail": {"horizon": r0.horizon, "score": r0.total_score,
                                    "held": r0.stock_id in 든}})  # fmt: skip
    for e in pv.excluded:
        r0 = (by_ticker.get(e.ticker) or [None])[0]  # type: ignore[assignment]
        if r0 is not None:
            결정.append({"stock_id": r0.stock_id, "kind": "skip", "reason": e.reason, "ref_close": r0.close,
                         "ref_date": r0.close_date,
                         "detail": {"horizon": r0.horizon, "score": r0.total_score, "note": e.detail[:200],
                                    "held": r0.stock_id in 든}})  # 이미 든 종목인가(25.1600 — 새 후보 풀에서 뺀다)
    out: list[dict[str, Any]] = []
    for o in 주문:
        b = by_sid[o["stock_id"]]
        r: report_picks.SignalRow = b["row"]
        # 위험 점검의 금액은 **낼 것으로 보는 값** — 시장가는 기준가. 틈 여유(`OPEN_BUY_BUFFER`)는 현금 예약의
        # 잣대라 `size_buys` 가 이미 그 잣대로 현금 안에서 냈다. 여유까지 넣으면 비중 10% 배분이 "사고 나면 11.1%"
        # 로 읽혀 C·R 매수가 모두 막혔다 (25.1458)
        단가 = o["limit_price"] if o.get("limit_price") is not None else o["ref_price"]
        out.append({
            **o, "currency": 통화, "horizon": r.horizon, "score": r.total_score, "score_as_of": r.as_of_date,
            "ref_date": r.close_date, "reason": 사유,
            # 위험 점검이 보는 원화 금액 — 수수료·환율 여유는 예약과 같은 잣대 (25.1451)
            "cost_krw": 단가 * o["quantity"] * (1 + pp.FEE_BUFFER) * 환율
            * (1 if 통화 == "KRW" else 1 + pp.FX_BUFFER),
            # 현금 검사는 예약과 같은 잣대 — 시장가는 틈 여유까지 (25.1458 재검증 권장 1)
            "cash_krw": (o["limit_price"] if o.get("limit_price") is not None
                         else o["ref_price"] * (1 + pp.OPEN_BUY_BUFFER))
            * o["quantity"] * (1 + pp.FEE_BUFFER) * 환율 * (1 if 통화 == "KRW" else 1 + pp.FX_BUFFER),
            "rationale": {
                "alloc": {"amount": b["alloc"].amount, "weight_pct": b["alloc"].weight_pct, "note": b["alloc"].note},
                "signal": {"horizon": r.horizon, "as_of": r.as_of_date, "total_score": r.total_score,
                           "weight_pct": r.suggested_weight_pct, "criteria": r.criteria},
                "entry": entry, "top_n": top_n, "ref_price": o.get("ref_price"),
                "settings": {k: settings.get(k) for k in ("max_weight_per_stock", "max_weight_per_sector",
                                                          "min_order_amount")},
                "equity": 총액, "reserved": 예약 / 환율, "fx": 환율, "costs": costs,
            },
        })  # fmt: skip
    return out, {"candidates": 후보수, "allocations": len(pv.allocations), "orders": len(out), "skipped": 빠짐[:10],
                 "excluded": len(pv.excluded), "frozen_blocked": len({r.stock_id for r in 멈춤빠짐}),
                 "_decisions": 결정}  # fmt: skip


def _조사_준비(
    client: Any, market: str, signal_rows: list[report_picks.SignalRow], charters: dict[str, dict[str, Any]],
    cache: dict[str, Any], trade_date: str,
) -> str:
    """조사 자료(모집단·가장 큰 안건)를 하루 실행에 한 번 — 위원회 회사들과 지주사(H)가 같이 쓴다. 기준일을 돌려준다."""
    from batch.jobs import company as cjob

    as_of = max((r.as_of_date for r in signal_rows if r.as_of_date), default=trade_date)
    if "agenda" not in cache:
        크기 = max(int(c["agenda"]) for c in charters.values())
        cache["population"] = cjob.population_dossiers(client, signal_rows, market, as_of)
        cache["agenda"] = cjob.load_dossiers(client, cjob.agenda_rows(signal_rows, 크기), market, as_of)
    return as_of


#: 지주사(H)의 성적 — H 자신의 결정 원장에서 표가 붙고 20 거래일 성과가 찬 행 (docs/company.md 10장, 25.1482)
HOLDING_RECORDS_SQL = (
    "SELECT stock_id, decided_on, ret_20d, bench_20d, detail_json FROM paper_decisions"
    " WHERE account_id = ? AND market = ? AND decided_on >= ? AND ret_20d IS NOT NULL"
    " AND detail_json LIKE '%\"votes\"%' AND rule_version = ?"
)


def holding_step(
    client: Any, *, market: str, account: str, config: dict[str, Any], charters: dict[str, dict[str, Any]],
    signal_rows: list[report_picks.SignalRow], trade_date: str, cache: dict[str, Any], now: str, rule_version: int,
    today: date, top_n: int, held: frozenset[int] = frozenset(),
) -> tuple[list[report_picks.SignalRow], dict[int, dict[str, Any]], list[tuple[str, list[Any]]], dict[str, Any]]:
    """지주사(H) 한 번 (docs/company.md 10장, 25.1482).

    (고른 종목의 신호 행 — 점수 순, {고른 종목: 지주 기록}, 추천받았지만 고르지 않은 종목의 결정 문장, 기록).
    투표는 H 가 **직접** 낸다 — A·C·D·E 는 그 헌장으로 `co.meet`(같은 조사 자료), B 는 점수 상위 `b_top_n`.
    다른 계좌의 실행 결과를 빌리지 않는다(다시 돌 때 표가 비었다, 25.1481 검증 [필수] 7).
    투표 회사 헌장 지문이 `config.voters` 와 다르면 사지 않는다(검증 [필수] 8)
    """
    voters: dict[str, str] = dict(config.get("voters") or {})
    if not voters:
        # 비면 B 혼자 투표해 늘 "두 곳 이상 0" — 오류 없이 영영 안 산다(25.1484 검증 [참고] 5)
        return [], {}, [], {"error": "H 의 투표 회사 목록(charter_json.voters)이 비었거나 읽지 못함"}
    없음 = sorted(c for c in voters if c not in charters)
    if 없음:
        return [], {}, [], {"error": "투표 회사 헌장을 읽지 못함(헌장 오류·없음): " + ",".join(없음)}
    바뀜 = sorted(c for c, h in voters.items() if co.charter_hash(charters[c]) != h)
    if 바뀜:
        return [], {}, [], {"error": "투표 회사 헌장이 H 에 적힌 지문과 다름(판을 올려야): " + ",".join(바뀜)}
    _조사_준비(client, market, signal_rows, charters, cache, trade_date)
    표: dict[str, dict[int, float]] = {}
    for c in sorted(voters):
        ch = charters[c]
        _items, approved = co.meet(cache["agenda"][: int(ch["agenda"])], ch, cache["population"])
        표[c] = {i.stock_id: hold.strength(i.vote, float(ch["threshold"])) for i in approved}
    행들: dict[int, list[report_picks.SignalRow]] = {}
    for r in report_picks.with_criteria(signal_rows):
        행들.setdefault(r.stock_id, []).append(r)
    b_top = int(config.get("b_top_n") or report_picks.TOP_STOCKS)
    표["B"] = {r.stock_id: 1.0 for r in report_picks.select_top(report_picks.with_criteria(signal_rows), b_top)}
    부터 = (today - timedelta(days=hold.HOLDING_WINDOW_DAYS)).isoformat()
    # 시장마다 따로 — 국내 결정은 KOSPI, 미국은 S&P500 대비라 섞으면 잣대가 다르다(25.1484 검증 [참고] 6)
    # 지금 판의 기록만 — 판이 바뀌면 투표 회사의 판단도 바뀌어 예전 적중률을 이어 쓰지 않는다(25.1577)
    기록행 = client.execute(HOLDING_RECORDS_SQL, [account, market, 부터, rule_version]).dicts()
    가중, 성적 = hold.weights(기록행, sorted(표))
    점수 = {sid: float(rs[0].total_score or 0) for sid, rs in 행들.items()}
    순위 = hold.rank(표, 가중, 점수)
    고른 = [x["stock_id"] for x in 순위 if x["eligible"] and x["stock_id"] in 행들][:top_n]
    기록 = {x["stock_id"]: {"votes": {c: 표[c][x["stock_id"]] for c in x["voters"]}, "score": x["score"],
                         "weights": 가중} for x in 순위}  # fmt: skip
    순서 = report_picks._기간_순
    고른행 = [r for sid in 고른 for r in sorted(행들.get(sid, []), key=lambda r: 순서.get(r.horizon, 9))]
    문장: list[tuple[str, list[Any]]] = []
    for x in 순위:
        sid = x["stock_id"]
        if sid in 고른 or sid not in 행들:
            continue
        r0 = 행들[sid][0]
        까닭 = ("지주사: 추천 회사 1곳" if not x["eligible"]
               else f"지주사: 점수 순위 밖(상위 {top_n})")  # fmt: skip
        문장.append(_결정(account, market, trade_date, sid, "skip", 까닭,
                        {"holding": 기록[sid], "horizon": r0.horizon, "score": r0.total_score, "held": sid in held},
                        r0.close, r0.close_date, now, rule_version))  # fmt: skip
    return 고른행, {sid: 기록[sid] for sid in 고른}, 문장, {
        "recommended": len(순위), "eligible": sum(x["eligible"] for x in 순위), "chosen": len(고른),
        "weights": 가중, "samples": {c: s["n"] for c, s in 성적.items()}}


def committee_step(
    client: Any, *, market: str, account: str, charter: dict[str, Any], charters: dict[str, dict[str, Any]],
    signal_rows: list[report_picks.SignalRow], trade_date: str, cache: dict[str, Any], now: str, rule_version: int,
    held: frozenset[int] = frozenset(),
) -> tuple[list[report_picks.SignalRow], dict[int, dict[str, Any]], list[tuple[str, list[Any]]], dict[str, Any]]:
    """투자위원회 한 번 (docs/company.md 3장, 25.1466).

    (가결 종목의 신호 행 — 표 순, {가결 종목: 위원회 기록}, 회의록·조사 자료·부결 결정 문장, 기록).
    조사 자료는 하루 실행에 한 번(`cache`) — 모집단(신호 종목 전체)과 헌장들 가운데 가장 큰 안건.
    회사는 그 앞부분을 쓴다.
    문장은 부르는 쪽이 주문·결정과 **한 묶음으로** 쓴다(3장 8)
    """
    from batch.jobs import company as cjob

    as_of = _조사_준비(client, market, signal_rows, charters, cache, trade_date)
    문장: list[tuple[str, list[Any]]] = []
    items, approved = co.meet(cache["agenda"][: int(charter["agenda"])], charter, cache["population"])
    if not cache.get("dossiers_written"):
        # 회의가 성공한 뒤에 켠다 — 먼저 켜면 첫 회사가 회의에서 실패할 때 그날 근거 행이 통째로 빠졌다
        # (25.1468 검증 권장)
        문장 += [(DOSSIER_INSERT_SQL, [market, as_of, d.stock_id, json.dumps(cjob.dossier_json(d), ensure_ascii=False,
                                                                            default=str), now])
                 for d in cache["agenda"]]  # fmt: skip
        cache["dossiers_written"] = True
    메모 = co.memo(str(charter.get("name") or account), items, approved)
    지문 = co.charter_hash(charter)
    # 회의록에 조사 자료 기준일을 함께 — 화면이 근거(시장의 최신 조사 자료)가 이 회의의 것인지 가른다(25.1582).
    # 회의 날(`trade_date`)과 조사 기준일은 보통 하루 다르다
    회의록 = {**co.minutes_json(items, approved, charter, 메모), "as_of": as_of}
    문장.append((MEETING_INSERT_SQL, [account, market, trade_date, 지문,
                                     json.dumps(회의록, ensure_ascii=False, default=str), now]))  # fmt: skip
    # 부서 입장 = [입장, 확신(, 자료 없음 까닭)] — 회의록과 같은 모양. 까닭을 빼면 자료 없음이 결정 원장에서
    # "중립" 으로 섞여 부서 성적표의 중립 표본이 부풀었다 (25.1471)
    기록 = {i.stock_id: {"vote": i.vote, "issue": i.issue, "vetoed_by": list(i.vetoed_by), "minority": list(i.minority),
                        "opinions": {k: [o.stance, o.conviction] + ([o.note] if o.note else [])
                                     for k, o in i.opinions.items()},
                        "charter_hash": 지문} for i in items}  # fmt: skip
    행들: dict[int, list[report_picks.SignalRow]] = {}
    for r in report_picks.with_criteria(signal_rows):
        행들.setdefault(r.stock_id, []).append(r)
    가결 = [i.stock_id for i in approved]
    순서 = report_picks._기간_순
    가결행 = [r for sid in 가결 for r in sorted(행들.get(sid, []), key=lambda r: 순서.get(r.horizon, 9))]
    for i in items:
        if i.stock_id in 가결:
            continue
        r0 = 행들[i.stock_id][0]
        까닭 = i.reason if not i.passed else f"위원회: 가결이나 표 순 상위 {int(charter['top_n'])} 밖"
        문장.append(_결정(account, market, trade_date, i.stock_id, "skip", 까닭,
                        {"committee": 기록[i.stock_id], "horizon": r0.horizon, "score": r0.total_score,
                         "held": i.stock_id in held},  # 이미 든 종목 — 새 후보 풀 밖(25.1601)
                        r0.close, r0.close_date, now, rule_version))  # fmt: skip
    return 가결행, {sid: 기록[sid] for sid in 가결}, 문장, {
        "memo": 메모, "agenda": len(items), "passed": sum(i.passed for i in items), "approved": len(approved)}


def run_step(
    client: Any, *, market: str, trade_date: str, late: bool, signal_rows: list[report_picks.SignalRow],
    settings: dict[str, Any] | None, price_series: dict[int, dict[str, float]], series_unknown: dict[int, str],
    allocate: bool, now: datetime | None = None, series_loader: Callable[[list[int]], dict] | None = None,
    after_close: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """일일 배치의 모의 계좌 단계. (리포트 끝에 붙일 줄, `step_log.paper`).

    `late` 는 오늘 장이 열린 뒤 만든 리포트(매도는 종가), `after_close` 는 오늘 장이 끝난 뒤의 손 실행(주문은 다음 장)

    `series_loader` 는 2부가 읽지 않은 모의 보유 종목의 시세를 읽는다(상관 축소용) — 없으면 그 보유는 대조하지 않는다
    (2부와 같은 "모르면 줄이지 않는다")
    """
    지금 = (now or datetime.now(UTC)).isoformat()
    읽기0 = getattr(getattr(client, "reads", None), "total", None)
    오늘 = cal.local_today(KST_MARKET).isoformat()
    기록: dict[str, Any] = {"trade_date": trade_date}
    flows, flow_created = _flows(client)
    if not flows:
        return [], {"skipped": "입금 없음"}
    accounts = client.execute(ACCOUNTS_SQL).dicts()
    fills, fill_created = _fills(client)
    fees = db.get_setting(client, "fees", {})
    taxes = db.get_setting(client, "taxes", {})
    stock_cache: dict[int, dict[str, Any]] = {}
    # 기업행위 경계는 **지금 든 것과 미체결 주문만** 본다 — 판 종목까지 보면 R 의 회전만큼 날마다 읽기가 늘었다
    # (25.1458, 검증 권장)
    든것 = {sid for aid in {f.account_id for f in fills}
            for sid in pp.positions([f for f in fills if f.account_id == aid])}  # fmt: skip
    # 오늘 새 후보도 — 빠지면 분할 의심 종목을 B 등이 사서 결정 원장에 buy 로 남기고 다음 날 주문만 멈춤으로 닫혔다
    # (R 은 제 대체 고르기에서 따로 본다, 25.1480 감사 [권장] 2). 신호 종목은 수십 개라 읽기가 크게 늘지 않는다
    관심 = (든것 | {int(r["stock_id"]) for r in client.execute(OPEN_ORDERS_SQL).dicts()}
            | {int(r.stock_id) for r in signal_rows})  # fmt: skip
    market_of = {sid: str(_stock(client, sid, stock_cache).get("country") or "KR") for sid in 관심}
    frozen = frozen_ids(client, 관심, market_of)
    # 보유 중 기업행위 — 경계 창이 지나도 장부 수량을 고치기 전까지 멈춤을 이어 간다(25.1547). **계좌마다**(25.1548 반박
    # [권고] 2): 그 종목을 기업행위 날보다 먼저 사서 든 계좌만 멈춘다 — 한 계좌 때문에 아홉 계좌가 그 종목을
    # 영영 못 샀다
    첫매수: dict[int, str] = {}
    계좌첫매수: dict[str, dict[int, str]] = {}
    for aid in {f.account_id for f in fills}:
        for sid, p in pp.positions([f for f in fills if f.account_id == aid]).items():
            첫매수[sid] = min(첫매수.get(sid, p.first_buy_date), p.first_buy_date)
            계좌첫매수.setdefault(aid, {})[sid] = p.first_buy_date
    이어멈춤, 이어표 = sticky_frozen(client, frozen, 첫매수, market_of)
    계좌멈춤: dict[str, set[int]] = {
        aid: {sid for sid, d in 든.items() if str(sid) in 이어표 and d < 이어표[str(sid)]}
        for aid, 든 in 계좌첫매수.items()
    }
    if 이어멈춤 - frozen:
        기록["action_sticky"] = sorted(이어멈춤 - frozen)
    경계멈춤 = set(frozen)
    # 전체 멈춤(기록·리포트 줄·KIS모의투자 베끼기) — 경계 + 이어 멈춤 모두. 계좌 판단은 `계좌별멈춤`
    frozen = 경계멈춤 | 이어멈춤

    def 계좌별멈춤(aid: str) -> set[int]:
        return 경계멈춤 | 계좌멈춤.get(aid, set())

    새체결 = fill_orders(client, market, trade_date, flows, fills, fees, taxes, 경계멈춤, 지금, 기록,
                      frozen_by_account=계좌멈춤)  # fmt: skip
    fills = [*fills, *새체결]
    for f in 새체결:
        fill_created[f.order_id] = 지금

    shadows = {"KOSPI": _level_of(client, flows, "KOSPI", fx=False),
               "SP500": _level_of(client, flows, "SP500", fx=True)}  # fmt: skip
    열림캐시: dict[tuple[str, str], float | None] = {}
    targets, _ = sr.기간별_목표(db.get_setting(client, "horizon_targets", None))
    # 그날 매도 판정이 쓰는 손절폭 — 매수 결정 행의 σ 단위 기록(25.1539)이 같은 값을 적는다
    손절폭 = {h: {**sf.DEFAULT_TARGETS[h], **((targets or {}).get(h) or {})}["stop_pct"] for h in sf.DEFAULT_TARGETS}
    fx = _fx(client, 오늘)
    열린 = [_order(r) for r in client.execute(OPEN_ORDERS_SQL).dicts()]
    다음장 = cal.next_session(market, date.fromisoformat(trade_date)).isoformat()
    기준 = "close" if late else "open"
    if after_close:
        # **장이 끝난 뒤의 손 실행** (25.1447, 교차검증 [필수] 3) — 오늘 장의 시가·종가 모두 주문보다 앞선다.
        # 다음 장 시가로
        다음장, 기준 = cal.next_session(market, date.fromisoformat(다음장)).isoformat(), "open"
    비용표 = cost_snapshot(market, fees, taxes, 다음장)  # 주문 근거에 남기는 비용(25.1595) — 체결할 장 기준
    쉼_시작 = trade_date
    for _ in range(pp.REBUY_COOLDOWN_SESSIONS - 1):
        쉼_시작 = cal.previous_session(market, date.fromisoformat(쉼_시작)).isoformat()
    보강캐시: dict[str, Any] = {}
    멈춤값 = db.get_setting(client, "paper_halt", None)
    멈춤 = bool(멈춤값.get("on")) if isinstance(멈춤값, dict) else bool(멈춤값)
    기록["halted"] = 멈춤
    줄: list[str] = []
    계좌기록: dict[str, Any] = {}
    유니버스: list[int] | None = None  # 무작위 대조의 모집단 — 하루 실행에 한 번만 읽어 R1·R2 가 같이 쓴다
    # 투자사 헌장 (docs/company.md, 25.1466) — 헌장이 맞는 계좌만 위원회를 연다.
    # 틀리면 그 계좌는 판 1 그대로(기록에 까닭)
    헌장들: dict[str, dict[str, Any]] = {}
    헌장오류: dict[str, list[str]] = {}
    지주설정: dict[str, dict[str, Any]] = {}
    for acct in accounts:
        try:
            ch = json.loads(acct.get("charter_json") or "null")
        except ValueError:
            ch, 헌장오류[str(acct["id"])] = None, ["헌장 JSON 을 읽지 못함"]
        if str(acct.get("selection") or "") == "weighted":
            # 지주사(H) — charter_json 은 위원회 헌장이 아니라 투표 회사 헌장 지문 (docs/company.md 10장, 25.1482)
            지주설정[str(acct["id"])] = ch if isinstance(ch, dict) else {}
            continue
        if isinstance(ch, dict):
            틀림 = co.charter_errors(ch)
            if 틀림:
                헌장오류[str(acct["id"])] = 틀림
            else:
                헌장들[str(acct["id"])] = ch
    조사: dict[str, Any] = {}  # 모집단·안건 조사 자료 — 하루 실행에 한 번, 회사들이 같이 쓴다
    메모줄: list[str] = []  # 운용역 메모 — 회사마다 한 줄 (docs/company.md 7장, 텔레그램 길이)
    for acct in accounts:
        aid, 규칙, 판 = str(acct["id"]), str(acct["sell_rule"]), int(acct["rule_version"])
        _결정판 = partial(_결정, rule_version=판)
        진입 = str(acct.get("entry_rule") or "tranche")
        종목수 = int(acct.get("top_n") or report_picks.TOP_STOCKS)
        무작위 = str(acct.get("selection") or "signal") == "random"
        a기록: dict[str, Any] = {"fills": sum(1 for f in 새체결 if f.account_id == aid)}
        멈춘 = 계좌별멈춤(aid)
        v, 검 = mark(client, aid, 오늘, flows, flow_created, fills, fill_created, a기록["fills"], 멈춘, stock_cache,
                    _열린_그림자(client, flows, shadows, str(acct.get("created_at") or "")[:10], 열림캐시),
                    지금)  # fmt: skip
        a기록.update({"recon_ok": 검["recon_ok"], "unknown": len(v.unknown)})
        계좌체결 = [f for f in fills if f.account_id == aid]
        # 사전 위험 점검의 문맥 (docs/paper.md 11장, 25.1451) — 배분과 따로 한 번 더 막는다
        상한 = settings or {}
        예약 = pp.reserved_krw([o for o in 열린 if o.account_id == aid and o.side == "buy"],
                              {"USD": fx[0] if fx else None})
        문맥 = rg.Context(
            # 하루 수익은 **입출금을 뺀** 시간가중 지수로 (25.1453, 교차검증 [필수] 2) — 평가 총액끼리 견주면
            # 입금 취소일이 −50% 로 읽혀 그날 매수가 모두 막혔고, 입금일엔 진짜 −10% 가 +90% 로 가려졌다
            equity_krw=v.equity_krw,
            day_return_pct=(검["twr"] / 검["prev_twr"] - 1) * 100 if 검["prev_twr"] else None,
            cash_available_krw=v.cash_krw - (예약 if 예약 is not None else v.cash_krw),
            # 열린 매수 주문도 보유처럼 — 종목·섹터 상한이 아직 안 찬 주문을 보게(25.1549)
            holding_krw=_보유와_열린매수(v.rows, [o for o in 열린 if o.account_id == aid and o.side == "buy"],
                                     {"USD": fx[0] if fx else None}),
            sector_of={sid: _stock(client, sid, stock_cache).get("sector")
                       for sid in {int(r["stock_id"]) for r in v.rows}
                       | {o.stock_id for o in 열린 if o.account_id == aid and o.side == "buy"}},
            max_stock_pct=float(상한.get("max_weight_per_stock") or 10.0),
            max_sector_pct=float(상한.get("max_weight_per_sector") or 30.0),
            today=cal.local_today(market).isoformat(), halted=멈춤, frozen=frozenset(멈춘),
        )  # fmt: skip
        if int(client.execute(ORDERS_EXIST_SQL, [aid, market, trade_date] * 3).scalar() or 0) > 0:
            a기록["orders"] = "이미 냄"
        else:
            판정메모: dict[int, dict[str, Any]] = {}
            매도 = sell_decisions(client, market, 규칙, 계좌체결, 멈춘, stock_cache, targets,
                               cal.local_today(market), 보강캐시, 판정메모)  # fmt: skip
            이미팖 = {o.stock_id for o in 열린 if o.account_id == aid and o.side == "sell"}
            보유 = pp.positions(계좌체결)
            문장: list[tuple[str, list[Any]]] = []
            # 이미 매도가 열린 종목은 새 매도를 내지 않으니 위험 점검에도 넣지 않는다 — 넣으면 멈춤 스위치 날
            # "막힘" 으로 원장에 적혔는데 그 열린 매도는 다음 장에 그대로 찼다(25.1612, 매도 경로 감사 [필수] 1)
            _통과, 막힌매도 = rg.gate([rg.Candidate(sid, "sell", 0.0) for sid in sorted(매도) if sid not in 이미팖],
                                  문맥)  # fmt: skip
            막힌매도id = {c.stock_id: 까닭 for c, 까닭 in 막힌매도}
            막힌수 = len(막힌매도)
            for sid, (사유, 근거) in sorted(매도.items()):
                if sid in 이미팖 or sid not in 보유 or sid in 막힌매도id:
                    continue
                문장.append((INSERT_ORDER_SQL, [
                    aid, market, sid, "sell", "market", None, 보유[sid].quantity, 보유[sid].currency, trade_date,
                    다음장, 기준, 0, ",".join(사유), None, None, None,
                    json.dumps({"flags": 근거, "sell_rule": 규칙, "costs": 비용표}, ensure_ascii=False,
                               default=str), 판, 지금, None,
                ]))  # fmt: skip
            a기록["sells"] = len(문장)  # 이 자리까지는 매도 주문만 있다
            # 전량 매도를 내는 종목의 남은 매수 회차는 거둔다 — 매도는 그때 보유 수량만 팔아, 같은 장에 남은 회차가
            # 함께 차면 판 종목을 새로 들고 남았다(장 끝난 뒤 손 실행이 낸 회차 → 다음 아침 손절, 25.1591 감사 [권고] 1)
            파는 = {sid for sid in 매도 if sid not in 이미팖 and sid in 보유 and sid not in 막힌매도id}
            # **아직 오지 않은 장**의 회차만 — 시세가 아직 없어 열어 둔 지나간 장의 회차는 그 장에서 찼을 수 있고
            # KIS 에도 이미 나갔다(소급 취소가 된다, 25.1592 — 25.1591 반박 [필수])
            # 손절이 선 보유도 — 손절을 따르지 않는 계좌(D)나 매도가 막힌 날엔 `파는` 에 없어, 장 끝난 뒤 손 실행이 낸
            # 회차가 다음 장에 차 손절 보유에 더 샀다("손절이 선 보유에는 더 사지 않는다" 를 어김, 25.1612 매도 경로
            # 감사 [필수] 2). 판정은 아래 새 배분과 같은 **계좌 규칙 전의** 플래그
            손절 = {sid for sid, 메모 in 판정메모.items() if any(f["reason_code"] == "손절" for f in 메모["flags"])}
            손절 |= {sid for sid, (사유, _근거) in 매도.items() if "손절" in 사유}
            거둘곳 = ({sid: WITHDRAWN_NOTE for sid in 파는}
                    | {sid: STOP_WITHDRAWN_NOTE for sid in 손절 - 파는 if sid in 보유})  # fmt: skip
            거둠 = [o for o in 열린 if o.account_id == aid and o.side == "buy" and o.stock_id in 거둘곳
                  and str(o.fill_session) > trade_date]  # fmt: skip
            문장 += [(EXPIRE_SQL, [trade_date, 거둘곳[o.stock_id], o.id]) for o in 거둠]
            if 거둠:
                a기록["buys_withdrawn"] = len(거둠)
                # 같은 실행의 매수 배분이 거둔 회차를 열린 것으로 보지 않게(현금 예약·같은 종목 두 벌 막이 까닭)
                거둔id = {o.id for o in 거둠}
                열린 = [o for o in 열린 if o.id not in 거둔id]
            # 결정 원장 (25.1449) — 판 것과, 플래그가 섰는데 계좌 규칙이 따르지 않아 들고 간 것(B 의 황색)
            for sid, 메모 in sorted(판정메모.items()):
                종가, 날 = 메모["ref"]
                if sid in 막힌매도id:
                    # 위험 점검이 매도를 막았다 — 들고 간 것으로 남긴다 (25.1451)
                    문장.append(_결정판(aid, market, trade_date, sid, "blocked",
                                    "위험 점검(매도): " + "; ".join(t for _c, t in 막힌매도id[sid]),
                                    {"flags": [f["reason_code"] for f in 메모["flags"]]}, 종가, 날, 지금))  # fmt: skip
                elif sid in 매도 and sid not in 이미팖:
                    문장.append(_결정판(aid, market, trade_date, sid, "sell", ",".join(매도[sid][0]),
                                    {"flags": [f["reason_code"] for f in 메모["flags"]]}, 종가, 날, 지금))  # fmt: skip
                # 그날 파는(또는 매도가 막힌·이미 매도가 열린) 종목은 들고 간 것이 아니다 — 함께 적으면 성과 분해의
                # "판 뒤" 와 "들고 간 뒤" 에 같은 결정이 두 번 들었다(25.1600, 매도·성과 분해 감사 [권고] 2 —
                # G·H 추적손절인데 목표 위, C 기간초과와 목표도달이 함께 선 날)
                if 메모["hold"] and sid not in 매도 and sid not in 이미팖:  # 열린 매도대로 팔린다(25.1612)
                    문장.append(_결정판(aid, market, trade_date, sid, "hold_flag", ",".join(메모["hold"]),
                                    {"flags": [f["reason_code"] for f in 메모["flags"]], "sell_rule": 규칙},
                                    종가, 날, 지금))  # fmt: skip
            # 미뤄진 열린 매도(하한가·정지)도 판 것으로 — 빠지면 다음 배치가 같은 종목을 다시 사 같은 장에 매도와 매수가
            # 함께 찼다(25.1591 감사 [권고] 1 길 B)
            최근판 = ({f.stock_id for f in 계좌체결 if f.side == "sell" and f.fill_date >= 쉼_시작} | set(매도)
                   | 이미팖)  # fmt: skip
            if allocate and settings is not None and signal_rows:
                # 손절이 선 보유에 더 사지 않는다 — **계좌 규칙 전의** 판정에서 (검증 [필수] 4: 규칙으로 거른 뒤의
                # 매도에서 뽑으면 손절을 따르지 않는 D 는 늘 비어, 떨어진 보유를 목표 비중까지 날마다 더 샀다 —
                # B−D 에 물타기가 섞였다)
                # (`손절` 은 위 회차 거둠에서 같은 식으로 만들었다)
                빠진시세 = sorted({int(r["stock_id"]) for r in v.rows} - set(price_series))
                if 빠진시세 and series_loader is not None:
                    price_series = {**price_series, **series_loader(빠진시세)}
                바꾸기 = None
                if 무작위:
                    if 유니버스 is None:
                        유니버스 = [int(r[0]) for r in client.execute(UNIVERSE_SQL, [market, market]).rows]
                    바꾸기 = partial(random_substitute, client, account=aid, market=market, pool=유니버스,
                                    held=held_substitutes(client, aid, set(pp.positions(계좌체결))),
                                    cooldown=최근판, stock_cache=stock_cache, today=cal.local_today(market),
                                    기록=a기록)  # fmt: skip
                가결행: list[report_picks.SignalRow] | None = None
                위원회: dict[int, dict[str, Any]] = {}
                if aid in 헌장들:
                    try:
                        가결행, 위원회, 회의문장, 메모 = committee_step(
                            client, market=market, account=aid, charter=헌장들[aid], charters=헌장들,
                            signal_rows=signal_rows, trade_date=trade_date, cache=조사, now=지금, rule_version=판,
                            held=frozenset(pp.positions(계좌체결)),
                        )  # fmt: skip
                    except Exception as exc:  # noqa: BLE001 — 그 회사만 오늘 사지 않는다. 다른 계좌·매도는 그대로
                        if db.quota_reason(exc):
                            raise
                        # 위원회가 못 열린 날 판 1(점수 상위)로 사면 그 회사의 기록에 판 1 매수가 섞인다
                        # — 사지 않고 까닭만 남긴다
                        log.exception("모의 투자사 %s 위원회 실패", aid)
                        가결행, 위원회, 회의문장, 메모 = [], {}, [], {"error": str(exc)[:200]}
                    문장 += 회의문장
                    a기록["committee"] = 메모
                    종목수 = int(헌장들[aid]["top_n"])
                elif aid in 헌장오류 and 판 >= 2:
                    # 헌장이 있는 판(2~) 회사의 헌장이 검사에 걸리면 위원회 없이 판 1(점수 상위)로 사면서 판 2 표시를
                    # 붙였다(25.1549 감사 [권고] 2) — 위원회가 못 열린 날과 같이 사지 않고 까닭만 남긴다
                    가결행 = []
                    # 리포트 줄은 아래 위원회 메모 줄("⚠️ … 투자위원회를 열지 못함 — 헌장 검사 실패 …") 하나로 —
                    # 머리 줄에도 같은 까닭을 붙여 두 번 나갔다(25.1559)
                    a기록["committee"] = {"error": "헌장 검사 실패 — " + "; ".join(헌장오류[aid])[:180]}
                지주: dict[int, dict[str, Any]] = {}
                if aid in 지주설정:
                    # 지주사(H) — 회사들의 추천을 성적 가중치로 합쳐 고른다 (docs/company.md 10장, 25.1482).
                    # 표가 모자란 날(헌장 바뀜·회의 실패) 사면 그 기록이 섞이므로 사지 않는다
                    try:
                        가결행, 지주, 지주문장, 지주메모 = holding_step(
                            client, market=market, account=aid, config=지주설정[aid], charters=헌장들,
                            signal_rows=signal_rows, trade_date=trade_date, cache=조사, now=지금, rule_version=판,
                            today=cal.local_today(market), top_n=종목수, held=frozenset(pp.positions(계좌체결)),
                        )  # fmt: skip
                    except Exception as exc:  # noqa: BLE001 — 그 계좌만 오늘 사지 않는다
                        if db.quota_reason(exc):
                            raise
                        log.exception("가상 %s 지주사 단계 실패", aid)
                        가결행, 지주, 지주문장, 지주메모 = [], {}, [], {"error": str(exc)[:200]}
                    if 지주메모.get("error"):
                        가결행 = []
                    문장 += 지주문장
                    a기록["holding"] = 지주메모
                # 사면 바로 팔 후보는 사지 않는다 (25.1480) — 결정 원장에 skip 으로. 자리는 위원회 없는 계좌면 다음
                # 후보가, 위원회 계좌(A·C)는 상위 N 을 자른 뒤라 현금으로 남는다(25.1588)
                즉시매도 = prebuy_sell_flags(client, market, 규칙, 가결행 if 가결행 is not None else signal_rows,
                                         set(pp.positions(계좌체결)), 멈춘, stock_cache, targets,
                                         cal.local_today(market), 보강캐시)  # fmt: skip
                계좌신호 = signal_rows
                if 즉시매도:
                    계좌신호 = [r for r in signal_rows if r.stock_id not in 즉시매도]
                    가결행 = None if 가결행 is None else [r for r in 가결행 if r.stock_id not in 즉시매도]
                    a기록["prebuy_sell"] = len(즉시매도)
                    for sid, 사유 in sorted(즉시매도.items()):
                        행 = next(r for r in signal_rows if r.stock_id == sid)
                        # 점수·기간도 다른 결정 줄처럼 — 이 줄만 빠져 점수 순서를 결정 원장에서
                        # 다시 낼 수 없었다(25.1584)
                        detail = {"flags": 사유, "sell_rule": 규칙, "horizon": 행.horizon,
                                  "score": 행.total_score}
                        if sid in 위원회:
                            detail["committee"] = 위원회[sid]
                        문장.append(_결정판(aid, market, trade_date, sid, "skip",
                                        "사면 바로 매도 규칙에 걸림: " + ",".join(사유), detail,
                                        행.close, 행.close_date, 지금))  # fmt: skip
                주문, b기록 = buy_orders(
                    client, market=market, account=aid, account_fills=계좌체결, valuation=v,
                    open_buys=[o for o in 열린 if o.account_id == aid and o.side == "buy"], signal_rows=계좌신호,
                    settings=settings, price_series=price_series, series_unknown=series_unknown,
                    fx_rate=fx[0] if fx else None, stop_ids=손절, sold_recently=최근판, stock_cache=stock_cache,
                    top_n=종목수, entry=진입, substitute=바꾸기, series_loader=series_loader, preselected=가결행,
                    preselected_reason="지주사 합의" if aid in 지주설정 else "위원회 가결", stop_pcts=손절폭,
                    costs=비용표, frozen=멈춘,
                )  # fmt: skip
                # 사전 위험 점검 (25.1451) — 통과한 회차만 주문. 한 회차도 못 지난 종목은 결정 원장에서 blocked 로
                후보 = [rg.Candidate(o["stock_id"], "buy", o["cost_krw"], o["ref_date"], i, o["cash_krw"])
                        for i, o in enumerate(주문)]  # fmt: skip
                # 후보 종목의 업종도 — 보유만 채우면 새 종목은 업종이 비어 섹터 상한이 꺼졌다 (25.1453, 검증 [필수] 3)
                문맥 = dc_replace(문맥, sector_of={**문맥.sector_of, **{
                    c.stock_id: _stock(client, c.stock_id, stock_cache).get("sector") for c in 후보}})
                통과, 막힘 = rg.gate(후보, 문맥)
                # 배분 전에 뺀 기업행위 멈춤 종목도 관문이 막은 것으로 센다(종목당 하나 — 예전엔 회차마다 셌다) — 11장
                # `risk_blocked` 는 "관문이 얼마나 자주 걸리나" 의 근거다(25.1619, 25.1618 교차검증 [권고] 1)
                막힌수 += len(막힘) + int(b기록.get("frozen_blocked") or 0)
                주문 = [주문[c.key] for c in 통과]
                낸종목 = {o["stock_id"] for o in 주문}
                막힌까닭 = {c.stock_id: "위험 점검: " + "; ".join(t for _c, t in 까닭) for c, 까닭 in 막힘}
                결정들 = b기록.pop("_decisions", None)
                if 결정들 is None and 가결행 and b기록.get("skipped"):
                    # 매수 단계가 일찍 끝난 날(환율 모름·장부 현금 음수) — 위원회·지주사가 고른 종목의 줄이 하나도
                    # 안 남아 그 표(`committee`·`holding`)가 원장에서 빠졌다. 고르지 않은 종목만 남아 H 가중치 표본·
                    # 부서 성적표가 치우쳤고, 같은 날 다시 돌면 "이미 냄" 으로 그날 표본을 영영 잃었다(25.1622,
                    # H 감사 [필수])
                    든것 = set(pp.positions(계좌체결))
                    까닭0 = f"{BUY_STEP_SKIP_PREFIX}{b기록['skipped']}"
                    결정들 = [{"stock_id": r.stock_id, "kind": "skip", "reason": 까닭0,
                             "ref_close": r.close, "ref_date": r.close_date,
                             "detail": {"horizon": r.horizon, "score": r.total_score, "held": r.stock_id in 든것}}
                            for r in {r.stock_id: r for r in 가결행}.values()]  # fmt: skip
                for d in 결정들 or []:
                    if d["kind"] == "buy" and d["stock_id"] not in 낸종목:
                        # 규칙은 사려 했는데 관문이 막았다 — skip 과 섞지 않는다 (25.1453)
                        d = {**d, "kind": "blocked", "reason": 막힌까닭.get(d["stock_id"], "위험 점검")}
                    if d["stock_id"] in 위원회:
                        # 가결된 안건 — 배분·관문 결과에 위원회 기록(부서 입장·표)을 붙인다 (docs/company.md 5장)
                        d = {**d, "detail": {**(d["detail"] or {}), "committee": 위원회[d["stock_id"]]}}
                    if d["stock_id"] in 지주:
                        # 지주사가 고른 종목 — 표·점수·그날 가중치를 붙인다(가중치를 다시 낼 때 이 행을 쓴다, 25.1482)
                        d = {**d, "detail": {**(d["detail"] or {}), "holding": 지주[d["stock_id"]]}}
                    문장.append(_결정판(aid, market, trade_date, d["stock_id"], d["kind"], d["reason"], d["detail"],
                                    d["ref_close"], d["ref_date"], 지금))  # fmt: skip
                a기록["buy"] = b기록
                for o in 주문:
                    문장.append((INSERT_ORDER_SQL, [
                        aid, market, o["stock_id"], "buy", "limit" if o.get("limit_price") is not None else "market",
                        o.get("limit_price"), o["quantity"], o["currency"], trade_date, 다음장, 기준, o["tranche"],
                        o["reason"], o["horizon"], o["score"], o["score_as_of"],
                        json.dumps(o["rationale"], ensure_ascii=False, default=str), 판, 지금, o.get("ref_price"),
                    ]))  # fmt: skip
            else:
                a기록["buy"] = {"skipped": "배분하지 않는 날(신호 없음·묵음·추천 못 읽음)"}
            _쓰기(client, 문장)
            a기록["risk_blocked"] = 막힌수
            a기록["orders"] = sum(1 for sql, _a in 문장 if sql == INSERT_ORDER_SQL)
            a기록["decisions"] = sum(1 for sql, _a in 문장 if sql == DECISION_INSERT_SQL)
        if aid in 헌장오류:
            a기록["charter_errors"] = 헌장오류[aid]
        계좌기록[aid] = a기록
        if isinstance(a기록.get("committee"), dict):
            위 = a기록["committee"]
            메모줄.append(f"🏛 {aid} {위['memo']}" if "memo" in 위
                       else f"⚠️ 가상 {aid} 투자위원회를 열지 못함 — {위.get('error')}")  # fmt: skip
        if isinstance(a기록.get("holding"), dict):
            지 = a기록["holding"]
            가중글 = " ".join(f"{c} {w:g}" for c, w in sorted((지.get("weights") or {}).items()))
            메모줄.append(f"🏢 {aid} 지주사: 추천 {지['recommended']} · 두 곳 이상 {지['eligible']}"
                       f" · 고름 {지['chosen']} · 가중 {가중글}" if "error" not in 지
                       else f"⚠️ 가상 {aid} 지주사가 고르지 못함 — {지.get('error')}")  # fmt: skip
        손익률 = f"{v.pnl_pct:+.1f}%" if v.pnl_pct is not None else "—"
        줄.append(f"{aid}: 평가 {v.equity_krw:,.0f}원 (손익 {손익률}, 시간가중 {(검['twr'] - 1) * 100:+.1f}%,"
                 f" 오늘 체결 {a기록['fills']}건)")  # fmt: skip
        if not 검["recon_ok"]:
            줄.append(f"⚠️ 가상 {aid} 장부 검산 어긋남 — {검['recon_note']}")
    기록["accounts"] = 계좌기록
    기록["frozen"] = sorted(frozen)
    # 계좌마다의 멈춤 — KIS모의투자 베끼기는 베끼는 계좌(H·도전자)의 것을 쓴다(25.1550 반박 [권고] 3: 전체 멈춤을
    # 쓰면 다른
    # 계좌의 이어 멈춤 때문에 H 의 정상 매수를 판 2 전까지 영영 베끼지 않아 H 와 KIS 가 갈렸다)
    기록["frozen_by_account"] = {str(a["id"]): sorted(계좌별멈춤(str(a["id"]))) for a in accounts}
    # 결정 원장 성과 채우기 (25.1449) — 곁다리. 실패해도 오늘 단계는 그대로
    try:
        기록["decision_outcomes"] = fill_outcomes(client, market, 오늘, 지금)
        if 기록["decision_outcomes"]["pending_read"] >= OUTCOME_BATCH:
            # 상한에 닿았다 = 대기열이 밀렸다(긴 휴장·한도 공백 뒤). 다음 배치가 이어 채우지만 H 가중치·성적표가
            # 늦어진다는 것을 보인다(25.1588 — 평소 하루 약 400행, 아홉 날 공백이면 일주일쯤 밀린다)
            줄.append(f"⚠️ 결정 원장 성과 채우기 밀림 — 한 번에 {OUTCOME_BATCH}행 상한에 닿음(다음 배치가 이어 채움)")
    except Exception as exc:  # noqa: BLE001 — 성과 채우기만 빠진다. 까닭은 기록에
        기록["decision_outcomes"] = {"error": str(exc)[:200]}
    # 가상 대조 (docs/company.md 6장, 25.1476) — 곁다리. 0079 전 DB·읽기 실패면 그 칸만 빈다
    try:
        기록["virtual_control"] = fill_controls(client, market)
    except Exception as exc:  # noqa: BLE001
        if db.quota_reason(exc):
            raise
        기록["virtual_control"] = {"error": str(exc)[:200]}
    # 성과 분해 (25.1450) — 계좌마다 오늘 평가 행에. 곁다리. **계좌마다 따로 잡는다** — 한 try 로 묶여 있어 A~G 하나가
    # 실패하면 일곱째인 H 의 결정 단위 증거가 그날 비어 관문·챔피언 비교가 "모름" 이 됐다(25.1523, 검토 3)
    for acct in accounts:
        try:
            a = save_attribution(client, str(acct["id"]), 오늘, int(acct["rule_version"]))
            기록.setdefault("attribution_n", {})[str(acct["id"])] = a["selection"]["n"]
        except Exception as exc:  # noqa: BLE001 — 그 계좌 분해만 빠진다
            if db.quota_reason(exc):
                raise
            기록.setdefault("attribution_error", {})[str(acct["id"])] = str(exc)[:200]
    if 읽기0 is not None:
        기록["reads"] = getattr(client.reads, "total", 0) - 읽기0
    머리 = ["🧪 가상모의투자 " + " · ".join(글 for 글 in 줄 if not 글.startswith("⚠️"))]
    # 실제 투자와 같은 기간 비교 (2026-10-15 사용자 지시, 25.1448) — 곁다리. 못 읽으면 그 줄만 빠진다
    try:
        비교 = real_comparison(client)
    except Exception as exc:  # noqa: BLE001 — 비교 줄만 빠진다. 까닭은 기록에
        비교 = None
        기록["real_compare_error"] = str(exc)[:200]
    if 비교:
        기록["real_compare"] = 비교
        머리.append(str(pp.compare_line(비교)))
    if frozen:
        머리.append(f"가상모의투자 멈춘 종목 {len(frozen)}개(기업행위 경계 — 원가로 평가, docs/paper.md 2.3)")
    return 머리 + 메모줄 + [글 for 글 in 줄 if 글.startswith("⚠️")], 기록


PAPER_SUMMARY_SQL = (
    "SELECT account_id, as_of, invested_krw, equity_krw, pnl_krw, twr_index, kospi_krw, sp500_krw, positions_json,"
    " attribution_json FROM paper_daily WHERE as_of >= ? ORDER BY account_id, as_of"
)
#: 월간 복기 — 한국 날짜로 그달 1~7일에 보내는 주간 요약이 지난달을 돌아본다 (docs/paper.md 6장)
MONTHLY_REVIEW_DAYS = 7
#: 그 주 요약이 못 나갔으면(DB 한도 등으로 건너뜀) 이날까지의 다음 요약이 따라잡는다(25.1571)
MONTHLY_CATCHUP_DAYS = 14
#: 월간 복기를 실어 보낸 달(한국 날짜 "YYYY-MM") — 따라잡기가 두 번 싣지 않게
MONTHLY_REVIEW_KEY = "paper_monthly_review_sent"


def review_due(client: Any, 오늘: date) -> bool:
    """이번 주 요약에 지난달 월간 복기를 싣나 — 1~7일이면 싣고, 8~14일이면 이번 달에 아직 안 실었을 때만(따라잡기).

    월간 복기는 그 달 첫 일요일 요약 한 번에만 걸려, 그 실행이 한도로 건너뛰면 통째로 사라졌다(25.1571, 운용 감사
    [확인필요] 7 — 2026-11-01 실행은 10-31 22:41 UTC 라 10월 한도가 찬 채일 수 있다)
    """
    if 오늘.day <= MONTHLY_REVIEW_DAYS:
        return True
    if 오늘.day > MONTHLY_CATCHUP_DAYS:
        return False
    return db.get_setting(client, MONTHLY_REVIEW_KEY, None) != 오늘.strftime("%Y-%m")


def summary_section(client: Any, now: datetime) -> list[str]:
    """주간 운영 요약의 모의 계좌 절 (docs/paper.md 6장). 입금이 없으면 빈 목록."""
    오늘 = now.astimezone(cal.market_tz("KR")).date()
    주초 = (오늘 - timedelta(days=7)).isoformat()
    달초 = ((오늘.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
            if review_due(client, 오늘) else None)  # fmt: skip
    # 월간 복기는 그달 앞 마지막 행부터 센다(25.1609) — 달초보다 2주 앞까지 읽는다. 그 안에 행이 없으면 달초부터
    시작 = min(x for x in ((오늘 - timedelta(days=14)).isoformat(),
                          달초 and (date.fromisoformat(달초) - timedelta(days=14)).isoformat()) if x)
    rows = client.execute(PAPER_SUMMARY_SQL, [시작]).dicts()
    if not rows:
        return []
    daily: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        daily.setdefault(str(r["account_id"]), []).append(r)
    fills, _ = _fills(client)
    by_acct: dict[str, list[pp.Fill]] = {}
    for f in fills:
        by_acct.setdefault(f.account_id, []).append(f)
    캐시: dict[int, dict[str, Any]] = {}
    보유id = {int(p["stock_id"]) for r in rows for p in json.loads(r.get("positions_json") or "[]")}
    for sid in {f.stock_id for f in fills} | 보유id:
        _stock(client, sid, 캐시)
    rules, _ = sr.기간별_목표(db.get_setting(client, "horizon_targets", None))
    손절 = {h: {**sf.DEFAULT_TARGETS[h], **((rules or {}).get(h) or {})}["stop_pct"] for h in sf.DEFAULT_TARGETS}
    limits = {
        "max_stock_pct": db.get_setting_in_range(client, "max_weight_per_stock", 10.0)[0],
        "max_sector_pct": db.get_setting_in_range(client, "max_weight_per_sector", 30.0)[0],
        "stop_pct": 손절,
    }
    규칙 = {str(r["id"]): str(r["sell_rule"]) for r in client.execute(ACCOUNTS_SQL).dicts()}
    # 늦게 연 계좌의 짝 비교 (25.1481) — 계좌마다 첫 평가일, 짝의 시작이 다르면 늦은 시작일의 지수
    시작일 = {str(r[0]): str(r[1]) for r in client.execute(FIRST_DAILY_SQL).rows}
    지수: dict[tuple[str, str], float] = {}
    for 앞, 뒤, *_ in pp.PAIRS:
        if 앞 in 시작일 and 뒤 in 시작일 and 시작일[앞] != 시작일[뒤]:
            공통 = max(시작일[앞], 시작일[뒤])
            for a in (앞, 뒤):
                if (a, 공통) not in 지수:
                    r = client.execute(TWR_AT_SQL, [a, 공통]).rows
                    if r and r[0][0]:
                        지수[(a, 공통)] = float(r[0][0])
    줄 = pp.summary_lines(daily, by_acct, 주초, 달초, {k: str(v.get("name")) for k, v in 캐시.items()},
                         {k: v.get("sector") for k, v in 캐시.items()}, limits, 규칙, 시작일, 지수)  # fmt: skip
    # 실제 투자와 같은 기간 비교 (25.1448) — 모의 절 바로 아래
    try:
        비교줄 = pp.compare_line(real_comparison(client))
    except Exception as exc:  # noqa: BLE001 — 비교 줄만 빠진다
        비교줄 = f"  실제 투자와 비교를 못 했습니다 ({type(exc).__name__})"
    if 줄 and 비교줄:
        자리 = next((i for i, x in enumerate(줄) if x.startswith("[가상모의투자 월간")), len(줄))
        줄.insert(자리, "  " + 비교줄.strip())
    # 월간 복기에 성과 분해 (25.1450) — 계좌마다 마지막 평가 행의 것
    if 달초 and 줄:
        분해 = [x for aid in sorted(daily)
                for x in pp.attribution_lines(aid, json.loads(daily[aid][-1].get("attribution_json") or "null"))]
        if 분해:
            줄 += [f"[노하우 성적표 — {pp.ATTRIBUTION_HORIZON} 거래일 뒤, 지수 대비]", *분해]
    # 투자위원회 주간 복기 (docs/company.md 7장, 25.1473) — 곁다리. 0076 전 DB·읽기 실패면 그 절만 빠진다
    if 줄:
        try:
            줄 += committee_review(client, 오늘, 캐시)
        except Exception as exc:  # noqa: BLE001
            줄.append(f"  투자위원회 복기를 못 했습니다 ({type(exc).__name__})")
    # KIS모의투자 비교 (docs/paper.md 15.3, 25.1478) — 배치가 낸 요약을 읽기만. 키를 넣기 전이면 빈다
    try:
        from batch.jobs import broker_mirror

        줄 += broker_mirror.summary_lines(client)
    except Exception as exc:  # noqa: BLE001 — 0078 전 DB 면 표가 없다
        if "no such" not in str(exc).lower():
            줄.append(f"  KIS모의투자 비교를 못 읽었습니다 ({type(exc).__name__})")
    return 줄


#: 계좌마다 첫 평가일과 그날 이하 마지막 시간가중 지수 — 늦게 연 계좌의 짝 비교 (25.1481)
FIRST_DAILY_SQL = "SELECT account_id, MIN(as_of) FROM paper_daily GROUP BY account_id"
TWR_AT_SQL = "SELECT twr_index FROM paper_daily WHERE account_id = ? AND as_of <= ? ORDER BY as_of DESC LIMIT 1"
#: 이번 주 회의록 · 20 거래일 성과가 이번 주쯤 찬 결정(결정일이 28~35일 전 — 20 거래일 ≈ 28 달력일) (25.1473)
WEEK_MEETINGS_SQL = "SELECT account_id, market, decided_on, minutes_json FROM paper_meetings WHERE decided_on >= ?"
REVIEW_DECISIONS_SQL = (
    "SELECT account_id, stock_id, decided_on, kind, reason, ret_20d, bench_20d, detail_json FROM paper_decisions"
    " WHERE decided_on >= ? AND decided_on < ? AND ret_20d IS NOT NULL AND detail_json LIKE '%committee%'"
)


def committee_review(client: Any, today: date, stock_cache: dict[int, dict[str, Any]]) -> list[str]:
    """주간 요약의 투자사 절 — 회사마다 이번 주 회의 수, 소수의견이 옳았던 결정 (services/company.review_lines)."""
    헌장 = {}
    for r in client.execute(ACCOUNTS_SQL).dicts():
        if str(r.get("selection") or "") == "weighted":
            continue  # 지주사(H)의 charter_json 은 투표 회사 지문이지 위원회가 아니다 (25.1484, 검증 [권장] 2)
        try:
            ch = json.loads(r.get("charter_json") or "null")
        except ValueError:
            ch = None
        if isinstance(ch, dict):
            헌장[str(r["id"])] = ch
    if not 헌장:
        return []
    회의 = client.execute(WEEK_MEETINGS_SQL, [(today - timedelta(days=7)).isoformat()]).dicts()
    결정 = client.execute(REVIEW_DECISIONS_SQL, [(today - timedelta(days=35)).isoformat(),
                                                (today - timedelta(days=28)).isoformat()]).dicts()  # fmt: skip
    이름 = {int(d["stock_id"]): str(_stock(client, int(d["stock_id"]), stock_cache).get("name")) for d in 결정}
    return co.review_lines(회의, 결정, 이름, 헌장)
