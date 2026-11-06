"""ETF 전술 가상계좌의 엔진 단계 — 국면으로 매도·매수 주문 꼴을 만든다 (docs/paper.md 17장, 2026-11-06 사용자 지시).

판정은 순수 함수(`services/etf_tactical`)가 하고, 이 파일은 DB 에서 지수·ETF·시세를 읽어 `jobs/paper` 의 계좌
단계가 쓰는 꼴(매도 `{종목: ([까닭], [근거])}`, 매수 주문 행)로 돌려준다.
장부·위험 점검·주문 적기는 다른 계좌와 같은 길이다.

- 계좌의 시장 실행에서만 판다·산다(X 국내 실행, Y 미국 실행). 다른 시장 실행에서는 아무것도 하지 않는다
- 갈아탈 때는 오늘 팔고 판 돈이 장부에 든 다음 실행에 산다(`etf_tactical.step`)
- 같은 국면이면 다시 맞추지 않는다(목표 ETF 를 조금이라도 들고 있으면 그대로)
"""

from __future__ import annotations

import math
from typing import Any

from batch.services import etf_tactical as et
from batch.services import paper as pp

SELECTION = "etf_tactical"
SELL_RULE = "etf_switch"
#: 매도 까닭 코드 — `pp.SELL_RULES["etf_switch"]` 와 같다
SWITCH_CODE = "국면전환"

INDEX_SQL = "SELECT date, close FROM index_prices WHERE index_code = ? AND date <= ? ORDER BY date DESC LIMIT ?"
#: 국내는 거래소 ETF 목록의 이름으로(코드를 박지 않는다), 미국은 티커로 — `etf_link.TACTICAL_ETFS` 가 이어 둔 줄
KR_ETF_SQL = (
    "SELECT s.id FROM etfs e JOIN stocks s ON s.id = e.stock_id"
    " WHERE e.country = 'KR' AND e.name = ? AND s.asset_type = 'etf' ORDER BY s.id LIMIT 1"
)
US_ETF_SQL = "SELECT id FROM stocks WHERE country = 'US' AND ticker = ? AND asset_type = 'etf' ORDER BY id LIMIT 1"
LAST_CLOSE_SQL = (
    "SELECT close, date FROM prices WHERE stock_id = ? AND date <= ? AND close > 0 ORDER BY date DESC LIMIT 1"
)
#: 판정에 읽는 지수 행 — 200일선 + 넉넉히(휴장·빠진 날)
INDEX_ROWS = et.SMA_DAYS + 40


def is_etf_account(acct: dict[str, Any]) -> bool:
    return str(acct.get("selection") or "") == SELECTION


def etf_ids(client: Any, aid: str) -> dict[str, int | None]:
    """계좌의 두 몫(`long`·`inverse`) → `stocks.id`. 못 찾으면 None(이어지지 않음 — `etf_link` 의 `tactical_missing`)"""
    a = et.ACCOUNTS[aid]
    sql = KR_ETF_SQL if a["market"] == "KR" else US_ETF_SQL
    return {몫: (int(v) if (v := client.execute(sql, [a[몫]]).scalar()) else None) for 몫 in ("long", "inverse")}


def plan(client: Any, aid: str, market: str, trade_date: str, positions: dict[int, pp.Position],
         open_sells: set[int]) -> dict[str, Any]:  # fmt: skip
    """오늘의 계획 — {regime, want, held, action, sells, buy_role, ids, skip}.

    `positions` = 이 계좌 보유, `open_sells` = 이미 매도가 열린 종목(다시 내지 않는다 — 열린 매도가 차면 다음
    실행에 산다)"""
    a = et.ACCOUNTS.get(aid)
    if a is None or a["market"] != market:
        return {"skip": "이 계좌의 시장이 아님"}
    ids = etf_ids(client, aid)
    몫of = {sid: 몫 for 몫, sid in ids.items() if sid is not None}
    줄 = [(str(d)[:10], float(c)) for d, c in client.execute(INDEX_SQL, [a["index"], trade_date, INDEX_ROWS]).rows]
    r = et.regime(줄, trade_date)
    want = next(iter(et.target(r.state)), None)
    든 = [sid for sid, p in positions.items() if p.quantity > 0]
    모르는 = [sid for sid in 든 if sid not in 몫of]
    held = 몫of[든[0]] if len(든) == 1 and not 모르는 else (None if not 든 else "?")
    out: dict[str, Any] = {"regime": r, "want": want, "held": held, "ids": ids, "sells": {}, "buy_role": None}
    if held == "?":
        # 몫이 둘이거나 모르는 ETF 를 든 날 — 엔진이 만든 꼴이 아니다. 모두 판다(그다음 실행에 목표를 산다)
        파는 = [sid for sid in 든 if sid not in open_sells]
        out["sells"] = {sid: ([SWITCH_CODE], [_근거(r, "정리", sid)]) for sid in 파는}
        out["action"] = "sell"
        return out
    할일, 몫 = et.step(held, want)
    out["action"] = 할일
    if 할일 == "sell":
        sid = ids[몫]  # type: ignore[index]
        if sid is not None and sid not in open_sells:
            out["sells"] = {sid: ([SWITCH_CODE], [_근거(r, f"{et.LABEL[r.state]} 국면 → {몫} 정리", sid)])}
    elif 할일 == "buy":
        if ids.get(몫) is None:  # type: ignore[arg-type]
            out["skip"] = f"{몫} ETF 가 종목표에 이어지지 않음(etf_link tactical_missing)"
        else:
            out["buy_role"] = 몫
    return out


def _근거(r: et.Regime, 글: str, sid: int) -> dict[str, Any]:
    return {"reason_code": SWITCH_CODE, "level": "red", "rationale": f"ETF 전술 판 1 — {글} (paper.md 17.1)",
            "criteria": criteria(r), "stock_id": sid}  # fmt: skip


def criteria(r: et.Regime) -> list[dict[str, Any]]:
    """근거표 줄 — 어느 지수 값·200일선·20일 수익으로 정했는지(절대 규칙: 근거표).

    웹 꼴 label·display·threshold·source(`criteria.CRITERION_REQUIRED_KEYS`)"""
    if r.close is None or r.sma is None or r.mom is None:
        return [{"label": "지수 국면", "display": "모름", "threshold": "200일치 미만이거나 7일 넘게 묵음 → 현금",
                 "source": "index_prices", "as_of": r.as_of, "passed": None}]  # fmt: skip
    return [
        {"label": "지수 종가 대 200일선", "display": f"{r.close:,.2f} / {r.sma:,.2f}",
         "threshold": "종가 ≥ 200일선 → 위",
         "source": "index_prices", "as_of": r.as_of, "passed": r.close >= r.sma},
        {"label": "20거래일 수익", "display": f"{r.mom * 100:+.2f}%", "threshold": "> 0 → 오름",
         "source": "index_prices", "as_of": r.as_of, "passed": r.mom > 0},
        {"label": "국면", "display": r.label, "threshold": "둘 다 위·오름 → 상승, 둘 다 아래·내림 → 하락, 그 밖 현금",
         "source": "services/etf_tactical", "as_of": r.as_of, "passed": None},
    ]  # fmt: skip


def buy_order(client: Any, p: dict[str, Any], *, market: str, valuation: pp.Valuation, open_buys: list[pp.Order],
              fx_rate: float | None, trade_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:  # fmt: skip
    """목표 ETF 의 시가 시장가 매수 한 줄 — 평가 총액 × 목표 비중, 쓸 수 있는 현금 안에서.
    값을 모르는 시장가는 다른 계좌와 같이 기준가 × (1 + `OPEN_BUY_BUFFER`) 로 잰다(2.1.1)"""
    몫 = p.get("buy_role")
    if 몫 is None:
        return [], {"skipped": p.get("skip") or "살 것 없음"}
    sid = int(p["ids"][몫])
    통화 = "KRW" if market == "KR" else "USD"
    환율 = 1.0 if 통화 == "KRW" else fx_rate
    if not 환율:
        return [], {"skipped": "환율 없음"}
    if any(o.stock_id == sid for o in open_buys):
        return [], {"skipped": "같은 ETF 매수가 이미 열려 있음"}
    예약 = pp.reserved_krw(open_buys, {"USD": fx_rate})
    if 예약 is None:
        return [], {"skipped": "미체결 달러 주문의 환율 모름"}
    현금 = valuation.cash_krw - 예약
    if 현금 <= 0:
        return [], {"skipped": "쓸 현금 없음"}
    시세 = client.execute(LAST_CLOSE_SQL, [sid, trade_date]).rows
    if not 시세:
        return [], {"skipped": "ETF 시세 없음(이은 날부터 쌓인다)"}
    종가, 날 = float(시세[0][0]), str(시세[0][1])[:10]
    비중 = et.target(p["regime"].state)[몫]
    목표 = min(valuation.equity_krw * 비중, 현금)
    주당 = 종가 * 환율 * (1 + pp.OPEN_BUY_BUFFER)
    수량 = math.floor(목표 / 주당) if 주당 > 0 else 0
    if 수량 < 1:
        return [], {"skipped": "1주도 못 삼"}
    r = p["regime"]
    return [{
        "stock_id": sid, "limit_price": None, "quantity": 수량, "currency": 통화, "tranche": 0,
        "reason": f"{et.RULE}: {r.label} 국면 → {et.ACCOUNTS_ROLE_LABEL[몫]} {비중 * 100:.0f}%",
        "horizon": "long", "score": None, "score_as_of": None,
        "rationale": {"rule": et.RULE, "regime": r.state, "role": 몫, "weight": 비중, "criteria": criteria(r)},
        "ref_price": 종가, "cost_krw": 수량 * 주당, "ref_date": 날, "cash_krw": 현금,
    }], {"regime": r.state, "role": 몫, "quantity": 수량}  # fmt: skip
