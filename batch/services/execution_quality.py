"""실제 매매의 실행 품질 — 주문표를 얼마나 따랐고 실제로 얼마에 샀나 (docs/paper.md 13장, infra 25.1452).

2026-10-15 사용자 목표("노하우를 … 실제 매매프로그램으로")의 다리. 모의 계좌는 "종가 ≤ 회차 가격이면
회차 가격에 찬다" 고 본다(docs/paper.md 2.3). 그 가정이 실제와 얼마나 다른지는 **사용자가 리포트 주문표대로
손으로 산 기록**이 있어야 잰다 — 그 차이가 모의 성적을 실제로 옮길 때의 할인율이고, 실매매 관문
(손 매매 준수율·슬리피지)의 수치다.

순수 함수다 — 사용자 매매·리포트 주문표·매도 플래그를 받아 센다. 금액은 쓰지 않고 비율·건수만 낸다
(비공개 리포트에 싣는다).
"""

from __future__ import annotations

from datetime import date
from typing import Any

#: 리포트 거래일 뒤 이 달력일 안의 매수만 그 리포트 주문표를 따른 것으로 본다 — 리포트는 다음 장 아침에
#: 나오고, 분할 매수는 며칠에 걸친다. 주말·연휴를 넘겨도 한 주 안
PLAN_LOOKBACK_DAYS = 7
#: 매도 전 이 달력일 안에 처음 선 플래그만 "플래그 뒤 판 것" 으로 본다 — 두 달 전에 섰다 풀린 플래그를
#: 오늘 매도의 까닭으로 잇지 않게
FLAG_LOOKBACK_DAYS = 60


def _날(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def measure(
    trades: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    picks: list[dict[str, Any]],
    flags: list[dict[str, Any]],
    start: str,
    end: str,
) -> dict[str, Any]:
    """[start, end] 매매의 실행 품질.

    `trades` [{stock_id, side, trade_date, price}],
    `plans` 2부 배분 [{stock_id, report_date, prices:[1차, 2차, 3차]}], `picks` 1부 [{stock_id, report_date}],
    `flags` [{stock_id, reason_code, level, first_seen}].
    1차 가격이 가장 높다(매수 구간 상단) — 1차 이하로 샀으면 주문표 안이다
    """
    def 가까운(rows: list[dict[str, Any]], sid: int, 날: date) -> dict[str, Any] | None:
        후보 = [r for r in rows
              if int(r["stock_id"]) == sid and 0 <= (날 - _날(r["report_date"])).days <= PLAN_LOOKBACK_DAYS]
        return max(후보, key=lambda r: str(r["report_date"])) if 후보 else None

    안, 차이, 계획밖, 일부만 = 0, [], 0, 0
    지연: list[int] = []
    플래그없음 = 0
    by_level: dict[str, int] = {}
    for t in trades:
        if not (start <= str(t["trade_date"])[:10] <= end):
            continue
        sid, 날, 가격 = int(t["stock_id"]), _날(t["trade_date"]), float(t["price"])
        if t["side"] == "buy":
            계획 = 가까운(plans, sid, 날)
            if 계획 and 계획.get("prices") and 계획["prices"][0]:
                첫 = float(계획["prices"][0])
                차이.append((가격 / 첫 - 1) * 100)
                안 += 가격 <= 첫
            elif 가까운(picks, sid, 날):
                일부만 += 1
            else:
                계획밖 += 1
            continue
        선 = [f for f in flags if int(f["stock_id"]) == sid
             and 0 <= (날 - _날(f["first_seen"])).days <= FLAG_LOOKBACK_DAYS]  # fmt: skip
        if 선:
            첫플래그 = min(선, key=lambda f: str(f["first_seen"]))
            지연.append((날 - _날(첫플래그["first_seen"])).days)
            by_level[str(첫플래그["level"])] = by_level.get(str(첫플래그["level"]), 0) + 1
        else:
            플래그없음 += 1
    return {
        "start": start, "end": end,
        "buys_on_plan": len(차이), "buys_within_limit": 안, "buys_pick_only": 일부만, "buys_off_plan": 계획밖,
        "vs_first_avg": sum(차이) / len(차이) if 차이 else None,
        "sells_after_flag": len(지연), "sell_delay_avg": sum(지연) / len(지연) if 지연 else None,
        "sells_by_level": by_level, "sells_no_flag": 플래그없음,
    }  # fmt: skip


def lines(m: dict[str, Any] | None) -> list[str]:
    """월간 복기에 싣는 줄. 매매가 없으면 빈 목록."""
    if not m:
        return []
    매수 = m["buys_on_plan"] + m["buys_pick_only"] + m["buys_off_plan"]
    매도 = m["sells_after_flag"] + m["sells_no_flag"]
    if not 매수 and not 매도:
        return []
    out = [f"[실행 품질 — 실제 매매 {m['start']}~{m['end']}]"]
    if 매수:
        out.append(f"  매수 {매수}건: 주문표(2부) 따른 것 {m['buys_on_plan']} · 1부에만 있던 것 {m['buys_pick_only']}"
                   f" · 리포트 밖 {m['buys_off_plan']}")  # fmt: skip
    if m["buys_on_plan"]:
        out.append(f"  주문표 매수 가운데 1차 가격 이하 {m['buys_within_limit']}/{m['buys_on_plan']}건,"
                   f" 체결가는 1차 가격보다 평균 {m['vs_first_avg']:+.2f}%"
                   " — 모의는 회차 가격 그대로 찬다고 본다(양수면 모의가 낙관)")  # fmt: skip
    if 매도:
        글 = (f"  매도 {매도}건: 플래그가 선 뒤 판 것 {m['sells_after_flag']}"
              + (f"(처음 선 날부터 평균 {m['sell_delay_avg']:.1f}일)" if m["sell_delay_avg"] is not None else "")
              + f" · 플래그 없이 판 것 {m['sells_no_flag']}")
        out.append(글)
    return out
