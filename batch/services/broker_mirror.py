"""증권사 모의투자에 내부 모의 계좌의 주문을 베껴 내기 — 순수 함수 (docs/paper.md 15장, docs/infra.md 25.1475).

무엇을 낼지(`plan`)와 어제 낸 것이 어떻게 됐는지(`reconcile`)만 정한다. 호출·DB 는 `jobs/broker_mirror`.

- 같은 주문을 그대로(종목·수량·지정가·하루짜리) — 내부가 "찼다" 고 본 주문이 증권사 모의에서 실제로 찼는지 주문 하나씩
견주려고
- **정리**: 증권사 모의에만 남은 수량(내부는 팔았는데 증권사에서 안 찬 것 등)은 시장가로 판다. 우리가 산 적 있는
종목만 —
  사람이 그 모의계좌에서 손으로 산 종목까지 팔지 않게
- 매도(정리 → 베낀 매도) 먼저, 매수는 그 뒤. 하루 상한을 넘는 매수는 내지 않고 까닭을 남긴다
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from batch.services.signals import to_tick

#: 하루에 증권사 모의로 내는 주문 상한 — 내부 B 의 최대(매수 5종목 × 3회차 + 매도 몇)보다 넉넉하고,
#: 결함으로 주문이 쏟아질 때 모의 계좌·호출 한도를 지키는 벽 (내부 하루 매수 상한 30 은 `risk_gate`)
MAX_ORDERS_PER_DAY = 40
REASON_MIRROR = "베낌"
REASON_CLEANUP = "정리"


@dataclass(frozen=True)
class Intent:
    paper_order_id: int | None  # 정리는 None
    stock_id: int
    code: str
    side: str  # buy · sell
    quantity: int
    price: float | None  # None = 시장가
    reason: str


def plan(
    open_orders: list[dict[str, Any]], codes: dict[int, str], internal_qty: dict[int, int],
    broker: dict[int, tuple[int, int]], bought_before: set[int], *, already: set[int], cleaned_today: set[int],
    halted: bool, max_orders: int = MAX_ORDERS_PER_DAY, bought_today: dict[int, int] | None = None,
    frozen: set[int] | None = None,
) -> tuple[list[Intent], list[tuple[int | None, int, str]]]:
    """(낼 것, [(내부 주문 id, 종목, 안 내는 까닭)]).

    `open_orders` = 오늘 체결 판정할 내부 주문(paper_orders 행), `internal_qty` = 내부 보유 수량,
    `broker` = {종목: (증권사 보유, 팔 수 있는 수량)}, `already` = 이미 증권사 주문 줄이 있는 내부 주문 id,
    `cleaned_today` = 오늘 정리 주문을 이미 낸 종목, `bought_today` = 오늘 이미 낸 베낀 매수 수량(같은 날 다시 돌 때 —
    시가에 찬 오늘 매수는 증권사 잔고에 있지만 내부 장부엔 다음 배치에야 들어와 "증권사에만 남은 것" 으로 보인다,
    25.1475 검증 [필수] 1), `frozen` = 기업행위 경계 종목(분할로 증권사 수량만 늘어난 것을 팔지 않게)
    """
    안냄: list[tuple[int | None, int, str]] = []
    if halted:
        return [], [(int(o["id"]), int(o["stock_id"]), "멈춤 스위치가 켜져 있음") for o in open_orders]
    팔수있음 = {sid: max(int(v[1]), 0) for sid, v in broker.items()}
    매도: list[Intent] = []
    for sid, (보유, _s) in sorted(broker.items()):
        남는것 = int(보유) - int(internal_qty.get(sid, 0)) - int((bought_today or {}).get(sid, 0))
        if (남는것 <= 0 or sid not in bought_before or sid in cleaned_today or sid in (frozen or set())
                or not codes.get(sid)):
            continue
        q = min(남는것, 팔수있음.get(sid, 0))
        if q > 0:
            매도.append(Intent(None, sid, codes[sid], "sell", q, None, REASON_CLEANUP))
            팔수있음[sid] -= q
    매수: list[Intent] = []
    for o in open_orders:
        oid, sid = int(o["id"]), int(o["stock_id"])
        if oid in already:
            continue
        code = codes.get(sid)
        if not code:
            안냄.append((oid, sid, "종목 코드 없음"))
            continue
        시장가 = o.get("order_type") == "market" or o.get("limit_price") is None
        if o["side"] == "sell":
            q = min(int(o["quantity"]), 팔수있음.get(sid, 0))
            if q <= 0:
                안냄.append((oid, sid, "증권사 모의에 팔 수량 없음"))
                continue
            팔수있음[sid] -= q
            가격 = None if 시장가 else to_tick(float(o["limit_price"]), "KRW", "up")
            매도.append(Intent(oid, sid, code, "sell", q, 가격, REASON_MIRROR))
        else:
            # 매수 지정가는 호가 단위로 내린다 — 내부 회차 가격은 이미 호가 단위지만(25.657), 어긋나면 증권사가 거부한다
            가격 = None if 시장가 else to_tick(float(o["limit_price"]), "KRW", "down")
            매수.append(Intent(oid, sid, code, "buy", int(o["quantity"]), 가격, REASON_MIRROR))
    낼것 = 매도 + 매수
    if len(낼것) > max_orders:
        for x in 낼것[max_orders:]:
            안냄.append((x.paper_order_id, x.stock_id, f"하루 주문 상한 {max_orders}"))
        낼것 = 낼것[:max_orders]
    return 낼것, 안냄


@dataclass(frozen=True)
class Outcome:
    row_id: int
    status: str  # filled · partial · unfilled · rejected · unknown
    order_no: str | None
    filled: int
    avg_price: float | None
    message: str | None


def _번호(no: Any) -> str:
    """주문번호 — 주문 응답(ODNO)과 체결 조회(odno)의 앞 0 채움이 같다는 근거가 없어 떼고 견준다(검증 [참고] 9)."""
    return str(no or "").strip().lstrip("0")


def reconcile(rows: list[dict[str, Any]], executions: list[Any], complete: bool = True) -> list[Outcome]:
    """어제까지 낸 증권사 주문(`sending`·`accepted`) → 체결 조회 결과로 마감. 하루짜리라 지난 거래일 주문은 끝났다.

    주문번호가 있으면 (주문일, 번호)로 찾는다. 응답을 못 받아 번호가 없는 줄(`sending`)은 같은 날·종목·방향·수량의
    남은 체결 줄을 하나 잡는다 — 못 찾으면 `unknown`(보냈는지 모른다). 체결 조회를 다 못 받았으면(`complete=False`,
    쪽 상한) 못 찾은 줄은 닫지 않고 다음 날 다시 본다
    """
    쓴: set[tuple[str, str]] = set()
    by_no = {(e.order_date, _번호(e.order_no)): e for e in executions}
    out: list[Outcome] = []
    for r in sorted(rows, key=lambda x: (x.get("broker_order_no") is None, int(x["id"]))):
        날 = str(r["trade_date"]).replace("-", "")
        e = by_no.get((날, _번호(r["broker_order_no"]))) if r.get("broker_order_no") else None
        if e is None and not r.get("broker_order_no"):
            e = next((x for x in executions if (x.order_date, _번호(x.order_no)) not in 쓴 and x.order_date == 날
                      and x.code == r["code"] and x.side == r["side"] and x.quantity == int(r["quantity"])),
                     None)  # fmt: skip
        if e is None:
            if complete:
                out.append(Outcome(int(r["id"]), "unknown", r.get("broker_order_no"), 0, None, "체결 조회에 없음"))
            continue
        쓴.add((e.order_date, _번호(e.order_no)))
        if e.filled <= 0 and e.rejected > 0:
            상태 = "rejected"
        elif e.filled >= int(r["quantity"]):
            상태 = "filled"
        elif e.filled > 0:
            상태 = "partial"
        else:
            상태 = "unfilled"
        out.append(Outcome(int(r["id"]), 상태, e.order_no, e.filled, e.avg_price,
                           "취소됨" if e.cancelled and e.filled <= 0 else None))  # fmt: skip
    return out


def holdings_diff(broker: dict[int, int], internal: dict[int, int]) -> dict[int, tuple[int, int]]:
    """{종목: (증권사, 내부)} — 수량이 다른 종목만."""
    return {sid: (broker.get(sid, 0), internal.get(sid, 0))
            for sid in sorted(set(broker) | set(internal)) if broker.get(sid, 0) != internal.get(sid, 0)}  # fmt: skip
