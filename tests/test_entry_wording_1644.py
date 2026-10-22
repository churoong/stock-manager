"""25.1644 — 성과 분해 진입 줄: 체결가가 종가보다 비쌌으면 "비쌌다" 로(예전 "평균 -0.85% 쌌다" 는 뜻이 뒤집혀 읽혔다)."""

from batch.services import paper as pp


def _진입줄(avg: float | None, n: int) -> str:
    a = {"selection": {}, "skipped": {}, "missed": {}, "sold": {}, "held_flag": {}, "entry": {"n": n, "avg": avg}}
    return next(x for x in pp.attribution_lines("A", a) if " 진입: " in x)


def test_싸면_쌌다_비싸면_비쌌다_같으면_같았다() -> None:
    assert "평균 0.50% 쌌다 (n=2)" in _진입줄(0.5, 2)
    assert "평균 0.85% 비쌌다 (n=3)" in _진입줄(-0.85, 3)
    assert "-0.85" not in _진입줄(-0.85, 3)
    assert "종가와 평균 같았다 (n=4)" in _진입줄(-0.001, 4)
    assert "체결 표본 없음" in _진입줄(None, 0)


def test_지정가_체결은_진입에서_재지_않고_수만_적는다() -> None:
    """25.1687 (결정 원장 감사 [필수] 2) — 지정가는 "종가 ≤ 회차 가격이면 회차 가격" 이라 체결가가 늘 종가 이상이어서
    B·H 의 진입이 실력과 상관없이 늘 "비쌌다" 였다. 시장가 체결만 재고 지정가는 수만."""
    a = pp.attribution([], [], limit_fills=3)
    assert a["entry"] == {"n": 0, "avg": None, "limit_n": 3}
    줄 = next(x for x in pp.attribution_lines("B", a) if " 진입: " in x)
    assert "지정가 체결 3건은 재지 않음(모형상 늘 종가 이상)" in 줄 and "비쌌다" not in 줄
    섞임 = pp.attribution([], [(10_000, 10_100)], limit_fills=2)
    줄 = next(x for x in pp.attribution_lines("C", 섞임) if " 진입: " in x)
    assert "평균 1.00% 쌌다 (n=1), 지정가 체결 2건은 재지 않음" in 줄
    assert pp.attribution([], [(10_000, 10_100)])["entry"] == {"n": 1, "avg": 1.0}  # 지정가 없으면 예전 꼴


def test_막은_매수와_막은_매도는_따로_센다() -> None:
    """25.1688 (결정 원장 감사 [권고]) — 막은 매수는 오르면, 막은 매도는 내리면 점검이 비용이다. 한 평균이면 +8 과 −8 이
    0 으로 지워져 두 번 다 손해인 것이 "중립" 으로 읽혔다."""
    def 막(reason: str, ret: float) -> dict:
        return {"kind": "blocked", "reason": reason, "ret_20d": ret, "bench_20d": 0.0, "stock_id": len(reason),
                "decided_on": "2026-10-01"}  # fmt: skip

    a = pp.attribution([막("위험 점검: 하루 주문 수", 8.0), 막(f"{pp.BLOCKED_SELL_PREFIX}: 하루 손실", -8.0)], [])
    assert a["blocked"]["avg"] == 8.0 and a["blocked_sell"]["avg"] == -8.0
    줄 = pp.attribution_lines("A", a)
    assert any("막은 매수의 그 뒤: +8.0%p" in x and "오르면 막은 것이 비용" in x for x in 줄)
    assert any("막은 매도의 그 뒤: -8.0%p" in x and "내리면 막은 것이 비용" in x for x in 줄)
