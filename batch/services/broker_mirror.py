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

from collections import Counter
from dataclasses import dataclass
from typing import Any

from batch.services.signals import to_tick

#: 하루에 증권사 모의로 내는 주문 상한 — 내부 B 의 최대(매수 5종목 × 3회차 + 매도 몇)보다 넉넉하고,
#: 결함으로 주문이 쏟아질 때 모의 계좌·호출 한도를 지키는 벽 (내부 하루 매수 상한 30 은 `risk_gate`)
MAX_ORDERS_PER_DAY = 40
REASON_MIRROR = "베낌"
REASON_CLEANUP = "정리"
#: 주문가능 수량이 모자라 정리를 일부만 낸 까닭(25.1607)
PARTIAL_CLEANUP = "증권사 주문가능 수량 모자람 — 일부만 정리"
#: 미국은 시장가가 없다(모의 미국 주문은 지정가만, 예약주문도 단가를 받는다) — 시장가 주문(매도·정리·시가 매수)은
#: 마지막 종가에서 이만큼(%) 불리한 지정가로 낸다(매도 아래·매수 위). 개장가가 그 안이면 개장가에 찬다. 하루 ±10% 넘는
#: 갭은 드물다 — 넘으면 안 차고 다음 거래일 맞추기에서 `unfilled` 로 보인다 `[확인필요]`
US_MARKETABLE_PCT = 10.0


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
    frozen: set[int] | None = None, currency: str = "KRW", closes: dict[int, float] | None = None,
    sent_today: int = 0,
) -> tuple[list[Intent], list[tuple[int | None, int, str]]]:
    """(낼 것, [(내부 주문 id, 종목, 안 내는 까닭)]).

    `open_orders` = 오늘 체결 판정할 내부 주문(paper_orders 행), `internal_qty` = 내부 보유 수량,
    `broker` = {종목: (증권사 보유, 팔 수 있는 수량)}, `already` = 이미 증권사 주문 줄이 있는 내부 주문 id,
    `cleaned_today` = 오늘 정리 주문을 이미 낸 종목, `bought_today` = 오늘 이미 낸 베낀 매수 수량(같은 날 다시 돌 때 —
    시가에 찬 오늘 매수는 증권사 잔고에 있지만 내부 장부엔 다음 배치에야 들어와 "증권사에만 남은 것" 으로 보인다,
    25.1475 검증 [필수] 1), `frozen` = 기업행위 경계 종목(분할로 증권사 수량만 늘어난 것을 팔지 않게).
    `currency="USD"` 면 시장가를 없앤다 — 마지막 종가(`closes`) ± `US_MARKETABLE_PCT` 지정가. 종가가 없으면 내지 않는다
    """
    안냄: list[tuple[int | None, int, str]] = []
    # 증권사에만 남은 몫(정리 대상) — 정리를 못 낼 때도 까닭을 남긴다. 예전엔 멈춤·기업행위 경계·코드 없음·
    # 주문가능 0 이면 기록 없이 빠져 어긋남이 날마다 남는데 왜인지 실행 기록으로 알 수 없었다
    # (25.1605, KIS 미국 감사 [권고] 2)
    남음 = {sid: int(보유) - int(internal_qty.get(sid, 0)) - int((bought_today or {}).get(sid, 0))
           for sid, (보유, _s) in broker.items()}  # fmt: skip
    정리대상 = sorted(sid for sid, n in 남음.items() if n > 0 and sid in bought_before and sid not in cleaned_today)
    if halted:
        return [], [(int(o["id"]), int(o["stock_id"]), "멈춤 스위치가 켜져 있음") for o in open_orders] + [
            (None, sid, "멈춤 스위치가 켜져 있음 — 정리 안 함") for sid in 정리대상]
    미국 = currency == "USD"

    def 대신가(sid: int, side: str) -> float | None:
        """미국 시장가 대신 낼 지정가. 국내는 None(시장가 그대로). 종가가 없으면 0 — 부르는 쪽이 내지 않는다."""
        if not 미국:
            return None
        c = (closes or {}).get(sid)
        if not c or c <= 0:
            return 0.0
        폭 = US_MARKETABLE_PCT / 100
        return to_tick(c * (1 - 폭), currency, "down") if side == "sell" else to_tick(c * (1 + 폭), currency, "up")

    팔수있음 = {sid: max(int(v[1]), 0) for sid, v in broker.items()}
    매도: list[Intent] = []
    for sid in sorted(broker):
        if sid not in 정리대상:  # 위 `남음` 과 같은 식 하나로(25.1607)
            continue
        남는것 = 남음[sid]
        if sid in (frozen or set()):
            안냄.append((None, sid, "기업행위 경계 — 정리 안 함"))
            continue
        if not codes.get(sid):
            안냄.append((None, sid, "종목 코드 없음 — 정리 못 함"))
            continue
        q = min(남는것, 팔수있음.get(sid, 0))
        가격 = 대신가(sid, "sell")
        if q <= 0:
            안냄.append((None, sid, "증권사 주문가능 수량 0 — 정리 못 함"))
        elif 가격 == 0:
            안냄.append((None, sid, "종가 없음 — 미국 정리 지정가를 못 정함"))
        elif q > 0:
            매도.append(Intent(None, sid, codes[sid], "sell", q, 가격, REASON_CLEANUP))
            팔수있음[sid] -= q
            if q < 남는것:  # 일부만 — 나머지 몫도 까닭을(25.1607, 25.1605 반박 [참고])
                안냄.append((None, sid, PARTIAL_CLEANUP))
    매수: list[Intent] = []
    for o in open_orders:
        oid, sid = int(o["id"]), int(o["stock_id"])
        if oid in already:
            continue
        code = codes.get(sid)
        if not code:
            안냄.append((oid, sid, "종목 코드 없음"))
            continue
        if sid in (frozen or set()):
            # 기업행위 경계 종목 — 가상모의투자는 다음 날 그 주문을 멈춤으로 닫는다(jobs/paper). 분할 전 수량으로
            # 증권사에서만 팔리지 않게 베끼지도 않는다(25.1495, 감사 [참고] 1)
            안냄.append((oid, sid, "기업행위 경계 — 베끼지 않음"))
            continue
        시장가 = o.get("order_type") == "market" or o.get("limit_price") is None
        # 매수 지정가는 호가 단위로 내린다 — 내부 회차 가격은 이미 호가 단위지만(25.657), 어긋나면 증권사가 거부한다
        가격 = (대신가(sid, str(o["side"])) if 시장가
                else to_tick(float(o["limit_price"]), currency, "up" if o["side"] == "sell" else "down"))  # fmt: skip
        if 가격 == 0:
            안냄.append((oid, sid, "종가 없음 — 미국 시장가 대신 낼 지정가를 못 정함"))
            continue
        if o["side"] == "sell":
            q = min(int(o["quantity"]), 팔수있음.get(sid, 0))
            if q <= 0:
                안냄.append((oid, sid, "증권사 모의에 팔 수량 없음"))
                continue
            팔수있음[sid] -= q
            매도.append(Intent(oid, sid, code, "sell", q, 가격, REASON_MIRROR))
        else:
            매수.append(Intent(oid, sid, code, "buy", int(o["quantity"]), 가격, REASON_MIRROR))
    낼것 = 매도 + 매수
    # 상한은 **하루** 단위 — 같은 날 앞 실행이 이미 낸 줄(`sent_today`, 거부 포함)을 뺀 몫만 낸다. 예전엔 실행마다 세어
    # 같은 날 손 실행이 상한을 넘겨 냈다(25.1567, KIS 경로 감사 [참고] 3)
    남은 = max(max_orders - int(sent_today), 0)
    if len(낼것) > 남은:
        for x in 낼것[남은:]:
            안냄.append((x.paper_order_id, x.stock_id, f"하루 주문 상한 {max_orders}"))
        안냄 = without_partial(안냄, 낼것[남은:])
        낼것 = 낼것[:남은]
    return 낼것, 안냄


def split_holding(
    quantity: int, sellable: int, ids: list[int], held: dict[int, int], sell_first: set[int] | None = None
) -> dict[int, tuple[int, int]]:
    """증권사 잔고 한 줄(티커 하나)을 같은 티커의 행들(`ids`, 마지막이 이긴 행)에 나눈다 — 이긴 행 아닌 행에는 그 행의
    몫(`held` — jobs 에서 증권사 쪽 순매수와 내부 보유 가운데 작은 것)만큼 먼저, 나머지는 이긴 행에. 거래소를 옮긴
    종목을 두 행에 들고 있으면 한 행에 다 붙여 남는 몫을 정리로 팔았다(25.1611). 내부 보유만으로 나누면 KIS 가 산 적
    없는 행이 몫을 가져갔다(25.1613). **주문가능은 이긴 행부터** — 베낀 매도는 대개 상장 중인 이긴 행으로 나가는데,
    주문가능이 모자란 날 옛 행에 먼저 붙이면 그 매도가 막혔다(25.1614, 25.1613 교차검증 [권고] 4). 단 **오늘 베낄
    매도가 걸린 행**(`sell_first`)이 먼저 — 고정 순서면 옛 행 매도가 막혔다(25.1615). 행이 하나면 그대로."""
    수량: dict[int, int] = {}
    q = int(quantity)
    for sid in ids[:-1]:
        a = min(q, max(int(held.get(sid, 0)), 0))
        if a > 0:
            수량[sid] = a
            q -= a
    if q > 0 or not 수량:  # 다 나눠 0 이 남은 이긴 행은 보유가 아니다
        수량[ids[-1]] = q
    out: dict[int, tuple[int, int]] = {}
    s = int(sellable)
    순서 = [ids[-1], *ids[:-1]]
    for sid in sorted(순서, key=lambda x: x not in (sell_first or set())):  # 안정 정렬 — 그 안에선 이긴 행부터
        if sid in 수량:
            b = min(s, 수량[sid])
            out[sid] = (수량[sid], b)
            s -= b
    return out


def without_partial(
    not_sent: list[tuple[int | None, int, str]], cut: list[Intent]
) -> list[tuple[int | None, int, str]]:
    """잘려 나가지 못한 정리 주문의 "일부만 정리" 까닭을 지운다 — 하나도 안 냈는데 일부는 냈다고 읽혔다(25.1608,
    25.1607 교차검증 [권고] 1). 잘린 까닭(상한·예약 마감)은 따로 남는다."""
    잘림 = {x.stock_id for x in cut if x.paper_order_id is None}
    return [r for r in not_sent if not (r[0] is None and r[1] in 잘림 and r[2] == PARTIAL_CLEANUP)]


@dataclass(frozen=True)
class Outcome:
    row_id: int
    status: str  # filled · partial · unfilled · rejected · unknown
    order_no: str | None
    filled: int
    avg_price: float | None
    message: str | None


#: 단가가 같다고 보는 차 — 미국 지정가는 센트 단위
PRICE_TOL = 0.005


def _단가_맞음(e: Any, r: dict[str, Any]) -> bool:
    """번호 없이 맞출 때 단가도 본다.

    미국 분할 회차는 수량이 같은 일이 흔해(2·2·2주) 날·종목·방향·수량만으로는 회차가 뒤바뀌었다(101달러 회차가 찼는데
    99달러 회차가 찬 것으로, 25.1490). 국내도 같다 — 9,600원 줄이 "체결 @9,800" 이 됐다
    (25.1566, KIS 경로 감사 [참고] 1).
    어느 쪽 단가든 모르면(시장가 줄·단가 칸 없는 응답) 보지 않는다
    """
    k, mine = getattr(e, "price", None), r.get("limit_price")
    if mine is None:
        # 내 줄이 시장가면 체결 줄도 단가가 없어야(시장가) 맞다 — 시장가 줄이 지정가 체결 줄을 가져갔다(25.1569, 25.1566
        # 반박 [권고]). 단가 칸이 아예 안 오는 응답이면 모두 None 이라 예전과 같다
        return k is None
    return k is None or abs(float(k) - float(mine)) < PRICE_TOL


def _번호(no: Any) -> str:
    """주문번호 — 주문 응답(ODNO)과 체결 조회(odno)의 앞 0 채움이 같다는 근거가 없어 떼고 견준다(검증 [참고] 9)."""
    return str(no or "").strip().lstrip("0")


#: 체결 조회에 없는 줄의 글. 미국 예약주문은 접수 때 증거금·매도수량을 보지 않고 개장 때 모자라면
#: **전송하지 않는다** — 그러면 체결 조회에 줄이 아예 없다(order_resv.py L80-83). 모의는 예약 목록 조회도
#: 못 쓴다(order_resv_list "모의투자는 사용 불가") — 25.1485 검증 [권장] 1
MISSING = "체결 조회에 없음"
MISSING_US = "체결 조회에 없음 — 예약이 개장 때 전송되지 않은 것으로 봄(증거금·매도수량 부족 등)"


def reconcile(
    rows: list[dict[str, Any]], executions: list[Any], complete: bool = True, missing: str = MISSING,
) -> list[Outcome]:
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
                      and x.code == r["code"] and x.side == r["side"] and x.quantity == int(r["quantity"])
                      and _단가_맞음(x, r)),
                     None)  # fmt: skip
        if e is None:
            if complete:
                out.append(Outcome(int(r["id"]), "unknown", r.get("broker_order_no"), 0, None, missing))
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


# ── 비교 보고 (docs/paper.md 15.3, 25.1478) ────────────────────────────────────

#: 비교 창(달력일) — 성과 분해의 에피소드(28일)와 같은 길이. 주문이 하루 수십 건이라 한 달이면 표본이 수백
COMPARE_DAYS = 28
#: 거부 까닭을 몇 개까지 싣나
COMPARE_TOP_REASONS = 5
_끝난 = ("filled", "partial", "unfilled", "rejected", "unknown")
#: 가상 쪽 만료 가운데 **체결 판정이 아닌 것**(`jobs/paper.fill_orders` 의 만료 까닭 머리) — 체결 규칙과 무관해
#: 일치율·실현율·체결 칸 표본에서 뺀다. 넣으면 그날 KIS 가 찬 주문이 "KIS만 참" 으로 세져 괴리 알림이
#: "가상 판정이 비관" 이라고 엉뚱한 방향을 말했다(25.1561 — KIS 경로 감사 [권고] 1)
NON_JUDGMENT_EXPIRY = (
    "현금 부족", "기업행위 경계", "쓸 수 있는 환율 없음", "그날 시세를 받지 못해", "팔 보유가 모자람",
    "같은 종목 전량 매도",  # 25.1592 — 거둔 매수 회차(`jobs/paper.WITHDRAWN_NOTE`)
    "손절이 선 보유",  # 25.1612 — 손절 보유의 거둔 매수 회차(`jobs/paper.STOP_WITHDRAWN_NOTE`)
)  # fmt: skip


def _판정밖(r: dict[str, Any]) -> bool:
    """체결 판정과 무관한 줄 — 가상의 판정 아닌 만료이거나 KIS 거부(주문 경로 실패 — 따로 센다), 또는 장 열린 뒤 낸
    시장가(가상은 종가 판정 — KIS 는 장중 체결이라 같은 잣대가 아니다. 체결 칸이 이미 빼던 것을 비교도, 25.1561)"""
    if r.get("status") == "rejected":
        return True
    if r.get("i_fill_basis") == "close" and not (r.get("side") == "buy" and r.get("i_order_type") != "market"):
        return True
    return r.get("i_status") == "expired" and str(r.get("i_note") or "").startswith(NON_JUDGMENT_EXPIRY)


def _까닭(message: str | None) -> str:
    """거부 문장에서 KIS 오류 코드(`APBK0919` 꼴)를 뽑는다. 없으면 앞 30자."""
    import re

    m = re.search(r"\b[A-Z]{2,}\d{3,}\b", message or "")
    return m.group(0) if m else (message or "까닭 없음")[:30]


#: 가상과 KIS 가 어긋난 주문의 까닭 (25.1736, 2026-10-23 사용자 "그렇게 진행해줘" — 아이디어 ⑤). 그날 일봉만으로 가른다
#: — 장중 순서·호가 대기열은 모른다. 가상 매수(지정가)는 **종가 ≤ 회차 가격**이면 찬다(`paper.judge`)
MISMATCH_CAUSES = {
    "intraday_touch": "장중엔 지정가에 닿았는데 종가는 위(가상 잣대가 보수 — KIS만 참)",
    "exact_touch": "지정가에 딱 닿음(호가 대기 순서 — 가상만 참)",
    "through_unfilled": "지정가 아래로 내려왔는데 KIS 안 참(접수·시각·수량 — 가상만 참)",
    "market_unfilled": "시장가가 KIS 에서 안 참(접수·정지 — 가상만 참)",
    "gap_beyond_limit": "시장가 대신 낸 지정가(종가 ±10%)에 일봉이 못 미침(갭 — 가상만 참)",
    "bar_suspect": "일봉이 지정가와 너무 멀어 분할 의심(가르지 않음)",
    "unexplained": "일봉으로 설명 안 됨",
    "no_bar": "그날 일봉 없음",
}
#: 종가가 지정가의 이 배 아래(또는 그 역수 배 위)면 분할 뒤 다시 받은 일봉으로 보고 가르지 않는다 — 미국 시세는 분할을
#: 감지하면 지난 저가·종가를 분할 반영 값으로 덮는데 주문의 지정가는 그대로다(25.1737, 25.1736 교차검증 [권고] 2).
#: 값은 `desk.PI_CLOSE_FLOOR`(25.1517 지정가 개선 칸의 같은 방어)와 같다 — desk 가 이 모듈을 읽어 거꾸로 읽지 않는다
BAR_RATIO_FLOOR = 0.8


def _값(v: Any) -> float:
    """미국 일봉은 yfinance 값을 반올림 없이 저장한다(`123.44999694824219`) — 0.01 단위 지정가와 같다/작다를 날값으로
    견주면 딱 닿은 것이 다른 까닭으로 갔다(25.1737, 25.1736 교차검증 [권고] 1). 소수 넷째 자리에서 맞춘다"""
    return round(float(v), 4)


def mismatch_cause(r: dict[str, Any], internal: bool) -> str:
    """어긋난 주문 한 줄(가상만 참 `internal=True` / KIS만 참)의 까닭 — `MISMATCH_CAUSES` 열쇠.

    잣대는 **KIS 에 실제로 보낸 지정가**(`b_limit`, 호가 단위로 맞춘 것)다 — 없으면 내부 지정가(25.1737, 교차검증
    [권고] 3). 국내 지정가 매수는 둘이 같다
    """
    side = str(r.get("side"))
    보낸 = r.get("b_limit")
    if r.get("i_order_type") == "market" or r.get("i_limit") is None:
        # 미국 시장가(매도·시가 일괄 매수)는 KIS 에 종가 ±10% 지정가로 나간다(`plan` 의 대신가) — 그날 고가·저가가
        # 그 값에 못 미쳤으면 접수 문제가 아니라 갭이다
        재료 = internal and 보낸 is not None and r.get("p_low") is not None and r.get("p_high") is not None
        # 분할 뒤 다시 받은 일봉을 "갭" 으로 짓지 않는다 — 보낸 값은 직전 종가 ±10% 라 정상 비율은 0.91~1.11, 10% 넘는
        # 실제 갭도 0.8~1.25 안이다(25.1741, 25.1737 교차검증 [권고] 1)
        if 재료 and r.get("p_close") is not None and float(보낸) > 0 and not (
                BAR_RATIO_FLOOR <= _값(r["p_close"]) / _값(보낸) <= 1 / BAR_RATIO_FLOOR):
            return "bar_suspect"
        if 재료 and ((side == "sell" and _값(r["p_high"]) < _값(보낸))
                   or (side == "buy" and _값(r["p_low"]) > _값(보낸))):  # fmt: skip
            return "gap_beyond_limit"
        return "market_unfilled" if internal else "unexplained"
    if r.get("p_low") is None or r.get("p_close") is None:
        return "no_bar"
    한도 = _값(보낸 if 보낸 is not None else r["i_limit"])
    저가, 종가 = _값(r["p_low"]), _값(r["p_close"])
    if 한도 <= 0 or not (BAR_RATIO_FLOOR <= 종가 / 한도 <= 1 / BAR_RATIO_FLOOR):
        return "bar_suspect"
    if side != "buy":
        return "unexplained"
    if internal:
        if 저가 < 한도:
            return "through_unfilled"
        return "exact_touch" if 저가 == 한도 else "unexplained"
    return "intraday_touch" if 저가 <= 한도 < 종가 else "unexplained"


def compare(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """베낀 주문마다 내부 판정 vs KIS모의투자 → 체결 일치율·가격 차이·거부 까닭.

    `rows` = 증권사 주문 줄에 내부 주문 상태(`i_status`)·내부 체결가(`i_price`, 슬리피지 전)를 붙인 것.
    끝난 것만 센다 — 내부가 아직 열려 있거나(미룬 매도) 증권사 줄이 `sending`·`accepted` 이면 뺀다.
    부분 체결은 "KIS 참" 으로 세고 따로 센다. 가격 차이는 **불리한 쪽이 +**: 매수는 (KIS 평균가 − 내부 체결가)/내부,
    매도는 (내부 − KIS)/내부, % — 양수면 내부가 낙관
    """
    칸 = Counter[str]()
    차이: dict[str, list[float]] = {"buy": [], "sell": []}
    까닭 = Counter[str]()
    어긋남 = Counter[str]()
    for r in rows:
        if r.get("reason") == REASON_CLEANUP:
            칸["cleanup"] += 1
            continue
        if r.get("status") not in _끝난 or r.get("i_status") not in ("filled", "expired"):
            continue
        # 그 거래일에 찬 것만 "가상 참" — 미룬 매도가 뒤 장에서 찬 것은 이 날 주문과 짝이 아니다
        내부 = r["i_status"] == "filled" and str(r.get("i_fill_date") or "")[:10] == str(r.get("trade_date") or "")[:10]
        if r["status"] in ("unknown", "rejected") and 내부:
            # 가상은 찼는데 경로가 실패한 주문 — 경로 포함 실현율(`desk.combine_kept.path_pct`)의 분모에만(25.1627 —
            # 가상도 안 찬 주문까지 넣으면 "최악 경계" 보다 낮게 나왔다, 25.1624 반박 [필수] 2)
            칸["path_fail"] += 1
        if r["status"] == "unknown":
            칸["unknown"] += 1
            continue
        if r["status"] == "rejected":
            # 거부는 주문 경로 실패라 체결 규칙 표본이 아니다 — 수와 까닭만(25.1561)
            칸["rejected"] += 1
            까닭[_까닭(r.get("message"))] += 1
            continue
        if _판정밖(r):
            칸["excluded"] += 1
            continue
        칸["n"] += 1
        kis = r["status"] in ("filled", "partial") and int(r.get("filled_qty") or 0) > 0
        칸["partial"] += r["status"] == "partial"
        칸[("both" if kis else "internal_only") if 내부 else ("kis_only" if kis else "neither")] += 1
        if 내부 != kis:
            어긋남[mismatch_cause(r, 내부)] += 1
        if 내부 and kis and r.get("avg_price") and r.get("i_price"):
            k, i = float(r["avg_price"]), float(r["i_price"])
            차이[str(r["side"])].append(((k - i) if r["side"] == "buy" else (i - k)) / i * 100)
    n = 칸["n"]

    def 평균(xs: list[float]) -> dict[str, Any]:
        return {"n": len(xs), "avg": sum(xs) / len(xs) if xs else None}

    가상참 = 칸["both"] + 칸["internal_only"]
    return {
        "days": COMPARE_DAYS, "n": n, "match_pct": (칸["both"] + 칸["neither"]) / n * 100 if n else None,
        # 가상 체결이 KIS 에서도 찬 비율 — 실현율(docs/paper.md 15.8, 25.1504 — 처음엔 "할인율" 이라 불렀다, 25.1507).
        # 100 이면 가상 체결을 그대로 믿어도 된다
        "kept_pct": 칸["both"] / 가상참 * 100 if 가상참 else None,
        **{k: 칸[k] for k in ("both", "internal_only", "kis_only", "neither", "partial", "rejected", "unknown",
                             "cleanup", "excluded", "path_fail")},
        "slip_buy": 평균(차이["buy"]), "slip_sell": 평균(차이["sell"]),
        "reject_reasons": dict(까닭.most_common(COMPARE_TOP_REASONS)),
        "mismatch_causes": dict(어긋남.most_common()),
    }  # fmt: skip


#: 체결 칸 (docs/paper.md 15.9, 25.1514) — 칸의 값을 "잰 것" 으로 쓰려면 이만큼 거래일이 쌓여야 한다.
#: 같은 날 주문끼리는 함께 움직여 실효 표본은 거래일 수에 가깝다
CELL_MIN_DAYS = 20


def cells(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """베낀 주문을 (방향, **내부** 주문 유형) 칸으로 — 베끼기를 시작한 날부터 누적 (docs/paper.md 15.9, 25.1514).

    `rows` 는 비교 줄(`compare` 와 같음)에 내부 주문 유형 `i_order_type`·판정 잣대 `i_fill_basis`·장부가 이미 얹은
    슬리피지 `i_slip_pct`(%)를 붙인 것. 칸 키는 `buy_limit`·`buy_market`·`sell_market` 등. 장 열린 뒤 낸 주문
    (`fill_basis='close'`)은 종가 판정이라 다른 칸이므로 뺀다(지정가 매수는 판정이 같아 넣는다). 정리 주문도 뺀다.

    칸마다: n(끝난 주문)·days(거래일 수)·둘 다 참·가상만 참·KIS만 참·둘 다 안 참·모름 ·
    `miss` = 가상만 참 ÷ (둘 다 참 + 가상만 참) — 가상이 찼다는데 KIS 는 안 찬 몫 ·
    `extra` = KIS만 참 ÷ (KIS만 참 + 둘 다 안 참) — 가상은 놓쳤는데 KIS 는 찬 몫(가상 지정가는 **종가 ≤ 지정가** 로만
    차서 장중에 닿기만 한 날을 놓친다 — 이 몫이 클 수 있다) ·
    `excess` = 둘 다 찬 주문의 (불리한 쪽 + 가격 차이 − 장부가 이미 얹은 슬리피지)의 **금액 가중** 평균(%) — 양수면
    장부의 비용 가정보다 KIS 가 더 나빴다 · `median` · `se` = 거래일 묶음 표준오차
    (거래일마다 평균 → 표준편차 ÷ √거래일).
    지정가 매수는 지정가보다 비싸게 찰 수 없어 excess 가 구조상 0 이하다(가격 개선) — 보정에 안 쓴다(desk.md 8장)
    """
    import statistics

    from batch.services.desk import measured

    칸: dict[str, dict[str, Any]] = {}
    달: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        지정가매수 = r.get("side") == "buy" and r.get("i_order_type") != "market"
        # 지정가 매수는 판정 잣대와 상관없이 칸에 넣는다 — `judge` 가 지정가 매수에서 `fill_basis` 를 보지 않는다
        # (보정의 `desk._칸키` 와 같은 잣대, 25.1517 검토 5)
        if r.get("reason") == REASON_CLEANUP or (r.get("i_fill_basis") == "close" and not 지정가매수):
            continue
        if r.get("status") not in _끝난 or r.get("i_status") not in ("filled", "expired"):
            continue
        키 = f"{r['side']}_{'market' if r.get('i_order_type') == 'market' else 'limit'}"
        c = 칸.setdefault(키, {"n": 0, "both": 0, "internal_only": 0, "kis_only": 0, "neither": 0, "unknown": 0,
                              "_days": set(), "_by_day": {}, "_vals": []})  # fmt: skip
        if r["status"] == "unknown":
            c["unknown"] += 1
            continue
        if _판정밖(r):
            continue  # KIS 거부·가상의 판정 아닌 만료는 칸 표본이 아니다(25.1561)
        날 = str(r.get("trade_date") or "")[:10]
        c["n"] += 1
        c["_days"].add(날)
        내부 = r["i_status"] == "filled" and str(r.get("i_fill_date") or "")[:10] == 날
        kis = r["status"] in ("filled", "partial") and int(r.get("filled_qty") or 0) > 0
        c[("both" if kis else "internal_only") if 내부 else ("kis_only" if kis else "neither")] += 1
        if 내부 and kis and r.get("avg_price") and r.get("i_price"):
            k, i = float(r["avg_price"]), float(r["i_price"])
            틈 = ((k - i) if r["side"] == "buy" else (i - k)) / i * 100
            넘음 = 틈 - float(r.get("i_slip_pct") or 0.0)
            w = k * int(r.get("filled_qty") or 0)
            합 = c["_by_day"].setdefault(날, [0.0, 0.0])
            합[0] += w * 넘음
            합[1] += w
            c["_vals"].append(넘음)
            달.setdefault(날[:7], {}).setdefault(키, []).extend([w * 넘음, w])
    out: dict[str, Any] = {}
    for 키, c in 칸.items():
        일평균 = [a / b for a, b in c["_by_day"].values() if b > 0]
        총w = sum(b for _a, b in c["_by_day"].values())
        가상참, 가상안참 = c["both"] + c["internal_only"], c["kis_only"] + c["neither"]
        out[키] = {
            "n": c["n"], "days": len(c["_days"]), **{k: c[k] for k in ("both", "internal_only", "kis_only", "neither",
                                                                       "unknown")},
            "miss": c["internal_only"] / 가상참 * 100 if 가상참 else None,
            "extra": c["kis_only"] / 가상안참 * 100 if 가상안참 else None,
            "n_price": len(c["_vals"]),
            "excess": sum(a for a, _b in c["_by_day"].values()) / 총w if 총w > 0 else None,
            "median": statistics.median(c["_vals"]) if c["_vals"] else None,
            "se": statistics.stdev(일평균) / len(일평균) ** 0.5 if len(일평균) >= 2 else None,
        }  # fmt: skip
        # 보정에 쓰는 잣대 그대로 — 화면이 거래일만 보고 "잰 칸" 이라 하던 것을 맞춘다(25.1516, 검토 4)
        out[키]["measured"] = measured(out[키], CELL_MIN_DAYS)
    월 = {m: {k: (sum(v[0::2]) / sum(v[1::2]) if sum(v[1::2]) > 0 else None) for k, v in d.items()}
         for m, d in sorted(달.items())}  # fmt: skip
    return {"cells": out, "cells_by_month": 월}


#: 괴리 알림 (docs/paper.md 15.8, 25.1504·25.1507) — 그 칸의 표본이 이만큼 쌓여야 판단한다
DIVERGE_MIN_N = 20
#: 체결 일치율이 이 아래로 내려가면 켠다 — 첫 값(근거 없음 `[확인필요]`), 한 달 쌓이면 다시 본다
DIVERGE_MATCH_PCT = 80.0
#: 실현율(가상이 찼다고 한 주문 가운데 KIS 에서도 찬 비율 `kept_pct`)이 이 아래로 내려가면 켠다 — 첫 값 `[확인필요]`
DIVERGE_KEPT_PCT = 85.0
#: 매수·매도 가격 차이 평균(불리한 쪽 +, %)이 이 위로 올라가면 켠다 — 첫 값 `[확인필요]`
DIVERGE_SLIP_PCT = 0.5
#: 꺼지는 문턱 — 일치율·실현율은 켜는 문턱 + 이만큼(%p) 위로, 가격 차이는 아래 값만큼 밑으로 돌아와야 끈다.
#: 28일 창이 날마다 겹쳐 값이 문턱 둘레를 천천히 오르내리므로 같은 문턱으로 끄고 켜면 알림이 되풀이된다(25.1507, 검토 5)
DIVERGE_RECOVER_PP = 5.0
DIVERGE_SLIP_RECOVER_PP = 0.1


def diverge_active(s: dict[str, Any] | None, prev: set[str]) -> set[str]:
    """오늘 켜진 괴리 칸 — {match, kept, slip_buy, slip_sell} 의 부분집합. `prev` = 어제까지 켜져 있던 칸.

    켜져 있던 칸은 꺼지는 문턱까지 돌아와야 끄고, 꺼져 있던 칸은 켜는 문턱을 넘어야 켠다. **그 칸의 표본이
    `DIVERGE_MIN_N` 보다 적으면 어제 상태를 그대로** 둔다 — 미국처럼 주문이 적으면 표본이 20 둘레를 오가며 깜빡인다
    """
    s = s or {}
    켬: set[str] = set()

    def 판정(칸: str, 값: Any, 충분: bool, 넘음: Any, 돌아옴: Any) -> None:
        if not 충분 or 값 is None:
            if 칸 in prev:
                켬.add(칸)
            return
        v = float(값)
        if (칸 in prev and not 돌아옴(v)) or (칸 not in prev and 넘음(v)):
            켬.add(칸)

    가상참 = int(s.get("both") or 0) + int(s.get("internal_only") or 0)
    판정("match", s.get("match_pct"), int(s.get("n") or 0) >= DIVERGE_MIN_N, lambda v: v < DIVERGE_MATCH_PCT,
         lambda v: v >= DIVERGE_MATCH_PCT + DIVERGE_RECOVER_PP)  # fmt: skip
    판정("kept", s.get("kept_pct"), 가상참 >= DIVERGE_MIN_N, lambda v: v < DIVERGE_KEPT_PCT,
         lambda v: v >= DIVERGE_KEPT_PCT + DIVERGE_RECOVER_PP)  # fmt: skip
    for 쪽 in ("slip_buy", "slip_sell"):
        x = s.get(쪽) or {}
        판정(쪽, x.get("avg"), int(x.get("n") or 0) >= DIVERGE_MIN_N, lambda v: v > DIVERGE_SLIP_PCT,
             lambda v: v <= DIVERGE_SLIP_PCT - DIVERGE_SLIP_RECOVER_PP)  # fmt: skip
    return 켬


def _문턱값(값: float, 문턱: float, 아래: bool, 부호: bool = False) -> str:
    """"값% — 문턱 n% 아래/위" — 문턱 쪽으로 반올림돼 "85% — 문턱 85%" 처럼 같아 보이지 않게 자릿수를 늘린다.

    25.1631 — 25.653 `_넘은_비율` 과 같은 뜻. 값이 문턱을 넘지 않았으면(켜진 칸을 꺼짐 문턱까지 켜 두는 `diverge_active`
    — 전날 알림이 실패해 오늘 다시 글로 오는 칸) 방향을 말하지 않고 예전 자릿수로(25.1631 교차검증 [필수] — "82.000% —
    문턱 80% 아래" 같은 거짓 방향이 났다)
    """
    꼴 = "+" if 부호 else ""
    첫 = 2 if 부호 else 0  # 가격 차이는 예전처럼 소수 둘째 자리부터
    for 자리 in range(첫, 7):
        r = round(값, 자리)
        if (r < 문턱) if 아래 else (r > 문턱):
            return f"{값:{꼴}.{자리}f}% — 문턱 {'+' if 부호 else ''}{문턱:g}% {'아래' if 아래 else '위'}"
    return f"{값:{꼴}.{첫}f}% — 문턱 {'+' if 부호 else ''}{문턱:g}%"  # 넘지 않음 — 방향을 말하지 않는다


def _값글(값: Any, 문턱: float, 아래: bool, 부호: bool = False) -> str:
    """`_문턱값` 앞 — 값이 없으면 "오늘 값 없음(표본 부족 — 전에 켜진 그대로)" (25.1641). "전에" — `prev` 는 마지막
    실행의 상태라 휴장이 이어지면 며칠 전이다(교차검증 [권고])"""
    if 값 is None:
        return f"오늘 값 없음(표본 부족 — 전에 켜진 그대로, 문턱 {'+' if 부호 else ''}{문턱:g}%)"
    return _문턱값(float(값), 문턱, 아래, 부호)


def diverge(now: dict[str, Any] | None, keys: list[str], market: str) -> str | None:
    """새로 켜진 괴리 칸(`keys`)의 알림 글 — 어느 쪽으로 벌어졌는지까지(25.1507, 검토 2).

    가상만 참이 많으면 가상 체결 판정이 낙관(가상 성적을 덜 믿어야), KIS만 참이 많으면 비관(가상 성적이 낮게 나옴).
    오늘 그 칸 값이 없으면(표본 0 — `diverge_active` 가 전에 켜진 칸을 그대로 두고, 그때 알림이 실패해 오늘 다시 글로
    오는 경우) "오늘 값 없음" 으로 쓰고 결론 글(낙관·덜 믿어야)은 붙이지 않는다 — 견줄 값이 없다. 예전에는 `float(None)`
    으로 글 전체가 사라지고 다음 날도 되풀이됐다(25.1641)
    """
    if not keys or not now:
        return None
    이름 = "국내가" if market == "KR" else "미국이"
    내만, kis만 = int(now.get("internal_only") or 0), int(now.get("kis_only") or 0)
    글: list[str] = []
    if "match" in keys:
        방향 = ("가상만 참이 더 많음 — 가상 체결 판정이 낙관, 가상 성적을 그만큼 덜 믿어야 합니다" if 내만 > kis만
              else "KIS만 참이 더 많음 — 가상 체결 판정이 비관, 가상 성적이 실제보다 낮게 나옵니다" if kis만 > 내만
              else "가상만 참과 KIS만 참이 같음")  # fmt: skip
        일치 = _값글(now.get("match_pct"), DIVERGE_MATCH_PCT, True)
        글.append(f"체결 일치 {일치}." + (f" 가상만 참 {내만} · KIS만 참 {kis만}: {방향}"
                                          if now.get("match_pct") is not None else ""))  # fmt: skip
    if "kept" in keys:
        실현 = _값글(now.get("kept_pct"), DIVERGE_KEPT_PCT, True)
        글.append(f"실현율(가상이 찼다고 한 주문 가운데 KIS 에서도 찬 비율) {실현}"
                 + (": 가상 성적을 그만큼 덜 믿어야 합니다" if now.get("kept_pct") is not None else ""))  # fmt: skip
    for 쪽, 말 in (("slip_buy", "매수"), ("slip_sell", "매도")):
        if 쪽 in keys:
            x = now.get(쪽) or {}
            차이 = _값글(x.get("avg"), DIVERGE_SLIP_PCT, False, True)
            글.append(f"{말} 가격 차이 평균(n={x.get('n') or 0}) {차이}"
                     + (": 가상이 체결가를 낙관" if x.get("avg") is not None else ""))  # fmt: skip
    기간 = f"지난 {now.get('days', COMPARE_DAYS)}일, 주문 {now.get('n')}"
    return f"KIS모의투자 {이름} 가상모의투자와 벌어졌습니다({기간}): " + " · ".join(글)


def compare_lines(
    s: dict[str, Any] | None, diff_count: int | None, as_of: str | None, account: str = "H",
) -> list[str]:
    """주간 요약 몇 줄. 끝난 베낀 주문이 없으면 빈 목록. 거부·표본 밖은 체결 견줌(n)과 따로 끝 줄에 — 거부만 있는 주에
    "주문 없음" 으로 보이던 것(25.1564, 25.1561 반박 [권고] A)"""
    if not s or not (s.get("n") or s.get("unknown") or s.get("cleanup") or s.get("rejected") or s.get("excluded")):
        return []
    out = [f"[KIS모의투자 — {account} 주문 베끼기, 지난 {s['days']}일]"]
    if s.get("n"):
        out.append(f"  주문 {s['n']} · 체결 일치 {s['match_pct']:.0f}% (둘 다 참 {s['both']}"
                   f" · 가상만 참 {s['internal_only']} · KIS만 참 {s['kis_only']} · 둘 다 안 참 {s['neither']})"
                   f" · 부분 체결 {s['partial']}")  # fmt: skip

    def 칸(x: dict[str, Any], 이름: str) -> str:
        return f"{이름} {x['avg']:+.2f}% (n={x['n']})" if x.get("n") else f"{이름} 표본 없음"

    out.append("  가격(+ 면 가상모의투자가 낙관): " + 칸(s["slip_buy"], "매수") + " · " + 칸(s["slip_sell"], "매도"))
    if s.get("mismatch_causes"):
        # 가상과 KIS 가 어긋난 까닭(25.1736) — 가상 체결 가정이 낙관인지 보수인지
        out.append("  어긋난 까닭:")
        out += [f"    {MISMATCH_CAUSES.get(k, k)} {v}" for k, v in s["mismatch_causes"].items()]
    if s.get("reject_reasons"):
        out.append("  거부 까닭: " + " · ".join(f"{k} {v}" for k, v in s["reject_reasons"].items()))
    끝 = []
    if s.get("rejected"):
        끝.append(f"거부 {s['rejected']}")
    if s.get("excluded"):
        끝.append(f"표본 밖 {s['excluded']}(가상의 판정 아닌 만료·장 열린 뒤 시장가)")
    if s.get("unknown"):
        끝.append(f"모름 {s['unknown']}")
    if s.get("cleanup"):
        끝.append(f"정리 주문 {s['cleanup']}")
    if diff_count is not None:
        끝.append(f"잔고가 어긋난 종목 {diff_count}" + (f" ({as_of})" if as_of else ""))
    if 끝:
        out.append("  " + " · ".join(끝))
    return out


# ── 웹 성과 (docs/paper.md 15.7, 25.1496) ─────────────────────────────────────


def fx_neutral_total(
    total: float | None, cash_krw: float | None, fx_ref: float | None, fx: float | None,
) -> float | None:
    """쉬는 해외 현금의 환율 몫을 뺀 총액 = 총액 + 해외 현금(원화) × (기준 환율 ÷ 그날 환율 − 1) (25.1543).

    H 는 현금을 원화로 들고 미국 보유분만 환율을 탄다. KIS 모의 계좌는 따로 있는 해외 현금(약 2.3억원어치 달러,
    25.1527)이 늘 환율을 탄다 — 그 몫을 기준 환율(견주기 창의 시작 날)에 묶어야 H 와 같은 잣대가 된다.
    해외 현금은 그날 원화 환산(`frcr_evlu_tota`) ÷ KIS 고시환율로 달러로 되돌린다 — 구간 중 미국 매도로 생긴 현금도
    시작 환율로 묶는 근사다(그 몫은 매도 대금 × 그 뒤 환율 변화 — 쉬는 현금보다 작다 `[확인필요]`). 하나라도 모르면 None
    """
    if total is None or cash_krw is None or not fx_ref or not fx or fx <= 0 or fx_ref <= 0:
        return None
    return float(total) + float(cash_krw) * (float(fx_ref) / float(fx) - 1)


def h_compare(
    rows: dict[str, tuple[float | None, float | None, float | None]], start: str, end: str,
    h0: tuple[float | None, float | None] | None, h1: tuple[float | None, float | None] | None,
) -> dict[str, Any]:
    """KIS 대 가상모의투자 H 견주기 — **같은 창**(start ~ end)의 손익 (25.1543).

    `rows` = {날: (총액, 해외 현금 원화, KIS 고시환율)}. 끝은 `desk.h_window` 처럼 오늘보다 앞선, 미국 일일 배치가 끝난
    날이어야 H 평가 행이 KIS 총액과 같은 미국 장 종가다(아침 H 행은 미국이 한 장 늦다, 25.1516). KIS 쪽은 원래 손익
    (`kis_pnl_krw`)과 쉬는 해외 현금의 환율 몫을 뺀 손익(`kis_cmp_pnl_krw`)을 함께 — 견주는 것은 뒤의 것이다
    """
    t0, t1 = rows[start][0], rows[end][0]
    cmp1 = fx_neutral_total(t1, rows[end][1], rows[start][2], rows[end][2])
    a0, p0 = h0 or (None, None)
    a1, p1 = h1 or (None, None)
    원래 = float(t1) - float(t0) if t0 is not None and t1 is not None else None
    견줌 = cmp1 - float(t0) if cmp1 is not None and t0 is not None else None
    return {"h_from": start, "h_to": end, "kis_pnl_krw": 원래, "kis_cmp_pnl_krw": 견줌,
            "fx_cash_krw": 원래 - 견줌 if 원래 is not None and 견줌 is not None else None,
            "h_pnl_krw": p1 - p0 if p0 is not None and p1 is not None else None,
            "h_return_pct": (a1 / a0 - 1) * 100 if a0 and a1 else None}  # fmt: skip


def perf(
    series: list[tuple[str, float | None]], h0: tuple[float | None, float | None] | None = None,
    h1: tuple[float | None, float | None] | None = None,
) -> dict[str, Any] | None:
    """KIS모의투자 **계좌 전체 원화 총액** 계열(날짜순 (as_of, 총액)) → 성과. 총액이 없는 날(모름)은 뺀다.

    계좌 하나에 원·달러가 같이 있어 시장별 총액 비는 수익률이 아니다 — 국내 총평가 + KIS 해외 총자산 원화 한 계열로
    본다(25.1497·25.1527). 모의 계좌는 입출금이 없어 총액 비가
    곧 수익률: 수익률 = 마지막 ÷ 시작 − 1(%), 손익 = 마지막 − 시작(원), MDD = 고점 대비 최저(%, docs/metrics.md 2장).
    `h0`·`h1` = 같은 기간 가상모의투자 H 의 (시간가중 지수, 평가손익 원) — 시작일 이하 마지막·마지막. 모의 예수금이
    H 입금액보다 커 % 는 늘 KIS 쪽이 작게 나오므로 **견주는 것은 손익 금액**(H 손익 변화)이고 % 는 참고다.
    배치는 25.1543 부터 H 견주기를 `h_compare`(같은 창·환율 몫 뺌)로 덮어쓴다 — 이 함수의 h 칸은 그 전 꼴이다.
    총액이 하나도 없으면 None
    """
    줄 = [(d, float(v)) for d, v in sorted(series) if v is not None and float(v) > 0]
    if not 줄:
        return None
    고점, 낙폭 = 줄[0][1], 0.0
    for _d, v in 줄:
        고점 = max(고점, v)
        낙폭 = min(낙폭, v / 고점 - 1)
    t0, p0 = h0 or (None, None)
    t1, p1 = h1 or (None, None)
    return {
        "start": 줄[0][0], "start_total": 줄[0][1], "last": 줄[-1][0], "last_total": 줄[-1][1],
        "pnl_krw": 줄[-1][1] - 줄[0][1], "return_pct": (줄[-1][1] / 줄[0][1] - 1) * 100, "mdd_pct": 낙폭 * 100,
        "days": len(줄), "h_pnl_krw": p1 - p0 if p0 is not None and p1 is not None else None,
        "h_return_pct": (t1 / t0 - 1) * 100 if t0 and t1 else None,
    }  # fmt: skip


def realized(
    rows: list[dict[str, Any]], fx_on: Any, balance: dict[str, int] | None = None,
    snapshots: list[tuple[str, dict[str, int]]] | None = None,
) -> dict[int, tuple[float | None, float | None]]:
    """KIS모의투자 매도마다 실현손익(원)과 그 가운데 환차 몫 — **우리 주문 기록**(체결 수량·평균 체결가)의 평균 원가
    (docs/paper.md 15.7.1, 25.1694, 2026-10-22 사용자 지시 "모의투자에 실현손익이나 기간실현손익").

    `rows` = 체결이 있는 주문(id·trade_date·side·stock_id·code·filled_qty·avg_price — 미국은 달러).
    `fx_on(날짜)` = 그날 USDKRW(모르면 None — 국내는 부르지 않는다). `balance` = 같은 실행이 읽은 KIS 잔고 {코드: 수량}.
    `snapshots` = 지난 실행들이 **개장 전에** 읽은 KIS 잔고 [(그날, {코드: 수량})] — 그날 앞 체결까지 담긴다.
    돌려주는 것: {매도 주문 id: (실현손익, 환차 몫)}. **KIS 가 매긴 수수료·세금은 빠진다** — 기록에 없다
    `[확인필요: KIS 모의 비용]`.

    - **티커(`code`)로 묶는다**(25.1698) — KIS 잔고는 티커 하나에 평균 원가 하나다
    - 같은 날은 매도 먼저(가상모의투자 `paper.realized` 와 같은 순서 — 주문은 장 전에 한꺼번에), 다만 매도가 그때
      기록 보유보다 많으면 같은 날 매수를 먼저 넣는다(개장 뒤 다시 돌아 산 날 판 것)
    - 평균 원가는 **매수로만** 바뀐다(매도는 수량만 줄인다). 그래서 KIS 가 기록보다 **많이** 든 것(기록 밖 매수 —
      `unknown` 으로 닫은 매수가 실제로 참·손 매수·기록 전 보유)은 원가를 흐리고, **적게** 든 것(기록 밖 매도)은
      KIS 가 0 이 되어 원가가 새로 시작할 때까지 원가를 흐리지 않는다(25.1701)
    - **오염**(25.1701, 25.1698 교차검증 [권고] 1·2): 개장 전 잔고가 기록보다 많거나 기록보다 많이 판 매도가 나오면
      그 티커의 지금(또는 마지막) 보유 구간 매도를 모름으로 하고, 그 뒤 매도도 모름 — **개장 전 잔고가 0** 인 날 기록을
      0 으로 맞추고(원가 새로 시작) 오염을 푼다. 0 을 확인한 구간은 닫는다(뒤의 어긋남이 그 구간을 건드리지 않음).
      `balance` 는 이 실행의 잔고로 같은 잣대(기록보다 많으면 마지막 구간 모름)
    - 원가를 모르는 매수(환율 없는 미국)가 든 동안의 매도도 모름 — 다 판 뒤 새로 살 때까지
    """
    보유: dict[str, tuple[int, float, float, bool]] = {}  # 수량, 현지 원가, 원화 원가, 원가 모름
    구간: dict[str, list[int]] = {}  # 코드마다 지금(또는 마지막) 보유 구간의 매도 id
    오염: set[str] = set()
    산뒤: set[str] = set()  # 직전 기준점 뒤 매수가 있었던 티커
    out: dict[int, tuple[float | None, float | None]] = {}

    def 열쇠(r: dict[str, Any]) -> str:
        return str(r.get("code") or f"#{r['stock_id']}")

    def 모름표시(k: str) -> None:
        for x in 구간.get(k, []):
            out[x] = (None, None)

    def 사기(r: dict[str, Any]) -> None:
        k, q체결, 가 = 열쇠(r), int(r["filled_qty"]), r.get("avg_price")
        q, 현, 원, 모름 = 보유.get(k, (0, 0.0, 0.0, False))
        산뒤.add(k)
        if q <= 0:
            구간[k] = []
        환율 = fx_on(str(r["trade_date"])) if r.get("market") == "US" else 1.0
        if 가 is None or float(가) <= 0 or 환율 is None:
            보유[k] = (q + q체결, 현, 원, True)
        else:
            보유[k] = (q + q체결, 현 + q체결 * float(가), 원 + q체결 * float(가) * 환율, 모름)

    def 팔기(r: dict[str, Any]) -> None:
        k, q체결, 가, i = 열쇠(r), int(r["filled_qty"]), r.get("avg_price"), int(r["id"])
        q, 현, 원, 모름 = 보유.get(k, (0, 0.0, 0.0, False))
        미국 = r.get("market") == "US"
        환율 = fx_on(str(r["trade_date"])) if 미국 else 1.0
        구간.setdefault(k, []).append(i)
        남 = max(q - q체결, 0)
        if q체결 > q:
            # 기록보다 많이 팔렸다 = KIS 가 기록 밖 주식을 들고 있었다 — 이 구간 전부, 그 뒤도 원가를 모른다
            오염.add(k)
            모름표시(k)
            보유[k] = (0, 0.0, 0.0, False)
            return
        if 가 is None or float(가) <= 0 or 환율 is None or 모름 or k in 오염:
            out[i] = (None, None)
            보유[k] = (남, 현 * 남 / q, 원 * 남 / q, 모름 and 남 > 0)
            return
        현지원가, 원화원가 = 현 * q체결 / q, 원 * q체결 / q
        손익 = round(q체결 * float(가) * 환율 - 원화원가)
        out[i] = (float(손익), float(round(현지원가 * 환율 - 원화원가)) if 미국 else 0.0)
        보유[k] = (남, 현 - 현지원가, 원 - 원화원가, False)

    def 잔고와(잔: dict[str, int], 끝: bool) -> None:
        # 기록에 한 번이라도 나오는 티커 전부 — 첫 매수 전에 KIS 가 이미 든 것(기록 전 보유)도 원가를 흐린다
        for k in 기록코드:
            q, kis = 보유.get(k, (0,))[0], int(잔.get(k, 0))
            if kis > q:
                오염.add(k)
                모름표시(k)
            elif kis == 0 and not 끝:
                # KIS 가 0 — 원가가 새로 시작한다. 기록 밖 매도로 남은 기록 수량은 버리고 오염을 푼다
                보유[k] = (0, 0.0, 0.0, False)
                오염.discard(k)
                구간[k] = []
            elif kis < q:
                # KIS 가 적다(25.1704, 25.1701 교차검증 [필수] 1) — 기록 밖 매도면 평균 원가는 같지만, 남은 유령 수량이
                # 다음 매수와 가중 평균되어 원가가 어긋난다. 직전 기준점 뒤 이 티커 매수가 없었으면 어긋남은 매도뿐 —
                # 기록 수량을 KIS 로 줄이고 원가를 같은 비율로(평균 원가법에선 정확). 매수가 있었으면 그 매수가 유령
                # (기록은 찼는데 KIS 는 안 참)이거나 이미 섞였을 수 있어 오염
                if k in 산뒤:
                    오염.add(k)
                    모름표시(k)
                elif not 끝:
                    q0, 현, 원, 모름 = 보유[k]
                    보유[k] = (kis, 현 * kis / q0, 원 * kis / q0, 모름)
        if not 끝:
            산뒤.clear()

    찬 = [r for r in rows if int(r.get("filled_qty") or 0) > 0]
    기록코드 = sorted({열쇠(r) for r in 찬})
    날들: dict[str, list[dict[str, Any]]] = {}
    for r in 찬:
        날들.setdefault(str(r["trade_date"])[:10], []).append(r)
    스냅 = {str(d)[:10]: v for d, v in (snapshots or [])}
    for 날 in sorted(set(날들) | set(스냅)):
        if 날 in 스냅:
            잔고와(스냅[날], False)
        줄 = sorted(날들.get(날, []), key=lambda x: (0 if x["side"] == "sell" else 1, int(x["id"])))
        넣음: set[int] = set()
        for r in 줄:
            if r["side"] == "buy":
                if int(r["id"]) not in 넣음:
                    사기(r)
                continue
            if int(r["filled_qty"]) > 보유.get(열쇠(r), (0,))[0]:
                for b in 줄:
                    if b["side"] == "buy" and 열쇠(b) == 열쇠(r) and int(b["id"]) not in 넣음:
                        사기(b)
                        넣음.add(int(b["id"]))
            팔기(r)
    if balance is not None:
        # 이 실행의 잔고(개장 뒤 다시 돈 실행이면 오늘 체결이 섞일 수 있다 — 기록을 바꾸지 않고 표시만)
        잔고와(balance, True)
    return out
