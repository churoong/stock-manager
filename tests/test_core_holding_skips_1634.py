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


def test_holding_skips_는_운영과_같은_꼴() -> None:
    """교차검증 [필수] — kind·held 값·사유 두 갈래·기준일을 모두 본다(픽스처는 "점수 순위 밖" 갈래를 돌지 않는다)."""
    from types import SimpleNamespace

    from batch.services import core_strategy as cs

    def 행(sid: int) -> SimpleNamespace:
        return SimpleNamespace(stock_id=sid, close=100.0 + sid, close_date="2026-10-20", horizon="long",
                               total_score=50.0 + sid)  # fmt: skip

    dv = SimpleNamespace(
        ranked=[{"stock_id": 1, "eligible": True}, {"stock_id": 2, "eligible": True},
                {"stock_id": 3, "eligible": False}, {"stock_id": 4, "eligible": True},
                {"stock_id": 5, "eligible": True}],  # 5 는 신호 행이 없다 — 운영처럼 건너뜀
        picks=[1], rows=[행(1), 행(2), 행(3), 행(4), 행(2)],
    )  # fmt: skip
    줄 = cs.holding_skips(dv, 5, held={4})  # type: ignore[arg-type]
    assert [(d["stock_id"], d["kind"], d["reason"], d["detail"]["held"]) for d in 줄] == [
        (2, "skip", "지주사: 점수 순위 밖(상위 5)", False),
        (3, "skip", "지주사: 추천 회사 1곳", False),
        (4, "skip", "지주사: 점수 순위 밖(상위 5)", True),
    ]
    assert all(d["ref_date"] == "2026-10-20" and d["ref_close"] == 100.0 + d["stock_id"] for d in 줄)


def test_core_h_지주사_skip_의_held_는_그날_보유() -> None:
    shared, 나눔 = T._나눠()
    s, _r = 나눔["core_h"]
    본 = 0
    for lg in s.log:
        for d in lg.decisions:
            if str(d.get("reason", "")).startswith("지주사:"):
                assert d["kind"] == "skip" and d["detail"]["held"] == (d["stock_id"] in lg.held)
                본 += 1
    assert 본
