"""핵심 H 백테스트 — 지주사가 고르지 않은 추천 종목도 결정(skip)으로 (docs/infra.md 25.1634).

운영 H 는 `holding_step` 이 그 줄을 결정 원장에 적어 관문 잣대(`selection_evidence`)의 "안 산 새 후보" 풀로 센다. 백테스트는
`buy_orders` 결정만 넣어 그 풀이 거의 비었다 — 문서(backtest.md "실전 전환 관문과 같은 숫자")와 어긋났다.
"""

from __future__ import annotations

from tests import test_core_backtest_job_1534 as T


def test_core_h_결정에_지주사_skip_줄이_원장과_같게_든다() -> None:
    shared, 나눔 = T._나눠()
    s, _r = 나눔["core_h"]
    원장 = {}
    for row in shared.market.ledger.rows:
        if row.kind == "skip" and str(row.reason).startswith("지주사:"):
            원장.setdefault(row.decided_on, set()).add(row.stock_id)
    매수날 = [lg for lg in s.log if any(d["kind"] == "buy" for d in lg.decisions)]
    assert 매수날
    for lg in 매수날:
        지주 = {d["stock_id"] for d in lg.decisions if str(d.get("reason", "")).startswith("지주사:")}
        assert 지주 == 원장.get(lg.day, set()), lg.day
        assert all("held" in d["detail"] for d in lg.decisions if str(d.get("reason", "")).startswith("지주사:"))
    assert any(원장.get(lg.day) for lg in 매수날)  # 픽스처가 실제로 그 경우를 만든다
