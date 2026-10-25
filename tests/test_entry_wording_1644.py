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


def test_산_쪽도_새_후보만_추가_매수는_따로() -> None:
    """25.1689 (결정 원장 감사 [권고]) — 안 산 쪽은 이미 든 종목을 빼는데 산 쪽엔 추가 매수가 남았다. 재현: 새 매수 +2,
    추가 매수 −12, 안 산 새 후보 +2 → 선정이 −5 대 +2 로 나왔다."""
    def 결(kind: str, ret: float, sid: int, held: bool = False) -> dict:
        return {"kind": kind, "reason": "x", "ret_20d": ret, "bench_20d": 0.0, "stock_id": sid,
                "decided_on": "2026-10-01", "detail": {"held": held}}  # fmt: skip

    a = pp.attribution([결("buy", 2.0, 1), 결("buy", -12.0, 2, held=True), 결("skip", 2.0, 3)], [])
    assert a["selection"] == {"n": 1, "avg": 2.0, "hit": 100.0} and a["add_on"]["avg"] == -12.0
    assert a["skipped"]["avg"] == 2.0
    assert "이미 든 종목 추가 매수 -12.0%p" in pp.attribution_lines("A", a)[0]


def test_막은_매도_뒤_막은_매수와_추가_매수_뒤_새_매수가_묻히지_않는다() -> None:
    """25.1696 (25.1688·1689 교차검증 [권고]) — 에피소드 열쇠가 (종목, kind) 라 28일 안의 다른 갈래가 먼저 온 쪽에 묻혔다."""
    def 줄(kind: str, d: str, ret: float, **kw: object) -> dict:
        return {"kind": kind, "decided_on": d, "stock_id": 1, "ret_20d": ret, "bench_20d": 0.0, **kw}

    a = pp.attribution([줄("blocked", "2026-09-01", -6.0, reason=f"{pp.BLOCKED_SELL_PREFIX}: 멈춤"),
                        줄("blocked", "2026-09-15", 9.0, reason="위험 점검: 하루 주문 수"),
                        줄("buy", "2026-09-01", -5.0, reason="x", detail={"held": True}),
                        줄("buy", "2026-09-20", 7.0, reason="x", detail={"held": False})], [])  # fmt: skip
    assert (a["blocked"]["n"], a["blocked_sell"]["n"]) == (1, 1)
    assert a["selection"]["avg"] == 7.0 and a["add_on"]["avg"] == -5.0


def test_종가로_판정된_시장가_매수는_진입에서_재지_않는다() -> None:
    """25.1783 (결함 루프 [권고]) — 늦은 실행·시가 없음 날의 시장가 매수는 체결가 = 그날 종가라 늘 0 이 섞여 평균이 묽어졌다
    (시가 100·종가 103 체결 10건이면 +3.0 인데, 같은 날 종가 체결 10건을 더하면 +1.5)"""
    from batch.jobs import paper as jp

    행 = [{"price": 100.0, "day_close": 103.0, "order_type": "market", "fill_basis": "open", "close_note": "시가"}] * 10
    행 += [{"price": 103.0, "day_close": 103.0, "order_type": "market", "fill_basis": "close", "close_note": "종가"}] * 5
    행 += [{"price": 103.0, "day_close": 103.0, "order_type": "market", "fill_basis": "open",
           "close_note": "시가 없음 — 종가"}] * 5  # fmt: skip

    class C:
        def execute(self, sql: str, args: list) -> object:  # noqa: ANN001
            from types import SimpleNamespace

            return SimpleNamespace(dicts=lambda: 행 if sql == jp.ATTR_FILLS_SQL else [])

    import batch.jobs.paper as 모듈

    원래 = 모듈._쓰기
    모듈._쓰기 = lambda *a, **k: None  # type: ignore[assignment]
    try:
        a = jp.save_attribution(C(), "C", "2026-10-26")
    finally:
        모듈._쓰기 = 원래  # type: ignore[assignment]
    assert a["entry"] == {"n": 10, "avg": 3.0, "close_n": 10}
    줄 = next(x for x in pp.attribution_lines("C", a) if " 진입: " in x)
    assert "평균 3.00% 쌌다 (n=10), 종가로 판정된 시장가 체결 10건은 재지 않음" in 줄
    assert jp.is_close_judged({"fill_basis": "close"}) and not jp.is_close_judged({"fill_basis": "open", "close_note": "시가"})
