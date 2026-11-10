"""KIS모의투자 체결 비교·실현손익 — 11-10 결함 회차 [권고] 셋 (25.2220).

(a) 개장 전에 만든 국내 시장가를 개장 뒤에 보낸 줄(이어 돌기·늦은 실행, 25.2144)은 비교·체결 칸 표본 밖
(b) 실현손익 같은 날 순서 — 시가 단일가·시장가 매수는 장중 매도 앞, 고점 대비 익절(trail)은 손절 갈래
(c) 그날 일봉 행이 없는데 KIS 만 찬 줄은 시세 결손 — 표본 밖("일봉 없음"), 시장가 까닭도 no_bar
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from batch.jobs import broker_mirror as job
from batch.jobs import intraday_guard as ig
from batch.services import broker_mirror as bm
from tests.test_broker_realized_1694 import _o
from tests.test_kis_realized_guard_1810 import _r
from tests.test_paper_job_1445 import Client

# ── (a) 개장 뒤 보낸 국내 시장가 ──────────────────────────────────────────────


def _kst(day: str, hhmm: str) -> str:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+09:00").astimezone(UTC).isoformat()


def _시장가매도(d: str, 보냄: str, kis: float, market: str = "KR") -> dict:
    return {"trade_date": d, "side": "sell", "status": "filled", "filled_qty": 10, "quantity": 10, "avg_price": kis,
            "message": None, "reason": bm.REASON_MIRROR, "i_status": "filled", "i_note": None,
            "i_order_type": "market", "i_fill_basis": "open", "i_price": 100.0, "i_fill_date": d, "i_slip_pct": 0.0,
            "b_market": market, "sent_at": 보냄, "p_close": 100.0, "p_low": 98.0, "p_high": 101.0}  # fmt: skip


def test_개장_뒤_보낸_국내_시장가는_비교와_칸_표본_밖() -> None:
    d = "2026-11-09"  # 월요일 거래일
    제때 = [_시장가매도(d, _kst(d, "08:40"), 100.0) for _ in range(20)]
    늦게 = [_시장가매도(d, _kst(d, "09:35"), 99.0) for _ in range(20)]  # 가상은 시가 100, KIS 는 09:35 에 99
    지정가 = {**_시장가매도(d, _kst(d, "09:35"), 100.0), "side": "buy", "i_order_type": "limit", "i_limit": 100.0}
    줄 = [*제때, *늦게, 지정가]
    assert job.mark_late_sends(줄) == 20  # 지정가 매수는 표시하지 않는다
    s = bm.compare(줄)
    assert (s["n"], s["excluded"], s["excluded_late_send"]) == (21, 20, 20)
    assert s["slip_sell"] == {"n": 20, "avg": 0.0}  # 예전엔 +0.5% — 괴리 문턱에 닿았다
    assert "slip_sell" not in bm.diverge_active(s, set())
    칸 = bm.cells(줄)["cells"]
    assert 칸["sell_market"]["n"] == 20 and 칸["sell_market"]["excess"] == 0.0
    assert 칸["buy_limit"]["n"] == 1


def test_개장_시각_경계_미국_보낸시각_없음() -> None:
    d = "2026-11-09"
    줄 = [_시장가매도(d, _kst(d, "08:59"), 100.0), _시장가매도(d, _kst(d, "09:00"), 100.0),
         _시장가매도(d, _kst(d, "09:35"), 100.0, market="US"), _시장가매도(d, None, 100.0)]  # type: ignore[arg-type]
    assert job.mark_late_sends(줄) == 1
    assert [bool(r.get("late_open_send")) for r in 줄] == [False, True, False, False]
    # 수능일은 10:00 개장 — 09:30 에 보낸 줄은 개장 앞(달력이 모르는 한 시간 늦춤, 25.1774)
    assert job.kr_open_utc(date(2026, 11, 19)) == datetime(2026, 11, 19, 1, 0, tzinfo=UTC)
    수능 = [_시장가매도("2026-11-19", _kst("2026-11-19", "09:30"), 100.0)]
    assert job.mark_late_sends(수능) == 0


def _db_줄(c: Client, 보냄: str, *, 일봉: bool = True) -> None:
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                   " decided_on, fill_session, reason, rule_version, status, fill_basis, created_at)"
                   " VALUES (1, 'H', 'KR', 1, 'sell', 'market', 10, 'KRW', '2026-11-06', '2026-11-09', 'r', 1,"
                   " 'filled', 'open', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                   " VALUES (1, 'H', 1, 'sell', '2026-11-09', 100, 10, 'KRW', 1, 0, 0, 0, 1000, 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, quantity, trade_date, reason, status, filled_qty, avg_price, sent_at, source,"
                   " created_at) VALUES ('kis_vts', 1, 'H', 'KR', 1, '005930', 'sell', 'market', 10, '2026-11-09',"
                   " '베낌', 'filled', 10, 99, ?, 'kis_vts', 't')", [보냄])  # fmt: skip
    if 일봉:
        c.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source,"
                       " fetched_at) VALUES (1, '2026-11-09', 100, 101, 98, 100, 1000, 'KRW', 't', 't')")  # fmt: skip


def test_비교와_칸_질의가_보낸_시각을_읽는다() -> None:
    """배선 — 질의에서 `sent_at`·`b_market` 이 빠지면 표시가 조용히 꺼진다"""
    for sql, 인자 in ((job.COMPARE_SQL, ["kis_vts", "2026-11-01", "2026-11-10", "H"]),
                     (job.CELLS_SQL, ["kis_vts", "2026-11-10", "H"])):  # fmt: skip
        c = Client(accounts=None)
        _db_줄(c, _kst("2026-11-09", "09:35"))
        줄 = c.execute(sql, 인자).dicts()
        assert job.mark_late_sends(줄) == 1, sql
        assert bm.compare(줄)["excluded_late_send"] == 1


def test_실행이_비교와_칸_모두에_표시한다() -> None:
    import inspect

    글 = inspect.getsource(job.run)
    assert 글.count("mark_late_sends(") == 2 and "mark_late_sends(비교줄)" in 글 and "mark_late_sends(칸줄)" in 글


# ── (b) 실현손익 같은 날 순서 ──────────────────────────────────────────────────


def test_고점_대비_익절은_시가_단일가_매수_뒤() -> None:
    """원가 100 10주, 회차 지정가 105 가 시가 103 에 참(시가 단일가), 개장 뒤 고점 대비 익절 10@104.
    평균 원가 (1,000 + 515) / 15 = 101 → 10 × (104 − 101) = +30. 예전(익절 먼저)은 +40"""
    줄 = [_r(_o(1, "2026-11-02", "buy", 10, 100), None), _r(_o(2, "2026-11-03", "buy", 5, 103), None, limit=105),
         _r(_o(3, "2026-11-03", "sell", 10, 104), bm.REASON_GUARD_TRAIL)]  # fmt: skip
    assert bm.realized(줄, lambda d: None) == {3: (30.0, 0.0)}


def test_고점_대비_익절은_손절처럼_값_높은_것부터() -> None:
    """지정가 105 에 그대로 찬 회차(값이 내려오며 105 를 지남) 뒤 104 에 고점 대비 익절 — 회차가 먼저.
    평균 (1,000 + 525) / 15 → 10 × 104 − 1,016.67 = +23. 목표가 익절은 선후를 정할 수 없어 예전처럼 매도 먼저(+40)"""
    줄 = [_r(_o(1, "2026-11-02", "buy", 10, 100), None), _r(_o(2, "2026-11-03", "buy", 5, 105), None),
         _r(_o(3, "2026-11-03", "sell", 10, 104), bm.REASON_GUARD_TRAIL)]  # fmt: skip
    assert bm.realized(줄, lambda d: None) == {3: (23.0, 0.0)}
    줄[2] = _r(_o(3, "2026-11-03", "sell", 10, 104), bm.REASON_GUARD_TAKE)
    assert bm.realized(줄, lambda d: None) == {3: (40.0, 0.0)}


def test_시가_단일가_매수와_시장가_매수는_목표가_익절보다_먼저() -> None:
    # 시가 단일가 103(지정가 105) — 평균 101, 목표가 익절 10@110 → +90 (예전 +100)
    줄 = [_r(_o(1, "2026-11-02", "buy", 10, 100), None), _r(_o(2, "2026-11-03", "buy", 5, 103), None, limit=105),
         _r(_o(3, "2026-11-03", "sell", 10, 110), bm.REASON_GUARD_TAKE)]  # fmt: skip
    assert bm.realized(줄, lambda d: None) == {3: (90.0, 0.0)}
    # 지정가 없는 시장가 매수(시가에 참)도
    줄[1] = {**_r(_o(2, "2026-11-03", "buy", 5, 103), None), "limit_price": None}
    assert bm.realized(줄, lambda d: None) == {3: (90.0, 0.0)}
    # 아침 베낀 매도는 장 전 주문이라 여전히 매수 앞(+100)
    줄[2] = _r(_o(3, "2026-11-03", "sell", 10, 110), None)
    assert bm.realized(줄, lambda d: None) == {3: (100.0, 0.0)}


def test_장중_감시가_고점_대비_익절을_따로_적는다() -> None:
    선 = {"B": (2, {"code": "B", "stop": 80.0, "trail": 120.0}), "C": (3, {"code": "C", "target": 110.0})}
    from tests.test_intraday_guard_1768 import _h

    hit = {x.code: x.reason for x in ig.hits([_h("B", 119.9), _h("C", 110.0)], 선)}
    assert hit == {"B": bm.REASON_GUARD_TRAIL, "C": bm.REASON_GUARD_TAKE}
    assert bm.REASON_GUARD_TRAIL in bm.GUARD_REASONS and len(bm.GUARD_REASON_LIST) == 3
    # 장중 줄을 고르는 질의의 자리 수가 사유 수와 같다(하나라도 빠지면 바인딩이 깨지거나 trail 줄을 놓친다)
    for sql in (ig.GUARDED_TODAY_SQL, ig.UNSENT_ORDER_SQL, job.GUARD_SELLS_SQL, job.SENT_TODAY_SQL):
        assert "IN (?, ?, ?)" in sql, sql


# ── (c) 일봉 없는 날 KIS 만 찬 줄 ─────────────────────────────────────────────


def _지정가매수(status: str, i_status: str, low: float | None, close: float | None, note: str | None = None) -> dict:
    kis = status == "filled"
    return {"trade_date": "2026-11-05", "side": "buy", "status": status, "filled_qty": 10 if kis else 0,
            "quantity": 10, "avg_price": 10_000.0 if kis else None, "message": None, "reason": bm.REASON_MIRROR,
            "i_status": i_status, "i_note": note, "i_order_type": "limit", "i_fill_basis": "open",
            "i_price": 10_000.0 if i_status == "filled" else None,
            "i_fill_date": "2026-11-05" if i_status == "filled" else None, "i_limit": 10_000.0,
            "p_low": low, "p_close": close, "b_market": "KR"}  # fmt: skip


def _21줄() -> list[dict]:
    정지 = "정지(그날 시세 없음·거래량 0)"
    return ([_지정가매수("filled", "filled", 9_900, 9_950) for _ in range(12)]
            + [_지정가매수("unfilled", "expired", 10_100, 10_200) for _ in range(2)]
            + [_지정가매수("unfilled", "filled", 9_900, 9_950) for _ in range(3)]  # through_unfilled
            + [_지정가매수("filled", "expired", 9_950, 10_100)]  # intraday_touch
            + [_지정가매수("filled", "expired", None, None, 정지) for _ in range(3)])  # 일봉 결손  # fmt: skip


def test_일봉_없는_날_KIS만_참은_표본_밖이고_알림_방향이_바로_선다() -> None:
    s = bm.compare(_21줄())
    assert (s["n"], s["internal_only"], s["kis_only"], s["excluded_no_bar"]) == (18, 3, 1, 3)
    assert round(s["match_pct"]) == 78  # 예전 n=21·67%
    assert "no_bar" not in s["mismatch_causes"]
    글 = bm.diverge(s, ["match"], "KR") or ""
    assert "가상만 참이 더 많음" in 글  # 예전엔 "KIS만 참이 더 많음 — 비관" 으로 거꾸로
    칸 = bm.cells(_21줄())["cells"]["buy_limit"]
    assert (칸["n"], 칸["kis_only"]) == (18, 1)


def test_진짜_정지는_남고_일봉을_붙이지_않은_줄은_가르지_않는다() -> None:
    # 둘 다 안 참(진짜 정지 — KIS 도 안 참)은 일봉이 없어도 표본에 남는다
    정지 = _지정가매수("unfilled", "expired", None, None, "정지(그날 시세 없음·거래량 0)")
    assert bm.compare([정지])["n"] == 1
    # `p_close` 열쇠가 없는 줄(일봉을 붙이지 않은 호출)은 모름 — 예전처럼 표본
    줄 = {k: v for k, v in _지정가매수("filled", "expired", None, None).items() if k not in ("p_low", "p_close")}
    assert bm.compare([줄])["kis_only"] == 1


def test_시장가_일봉_없음은_no_bar() -> None:
    시장가 = {"side": "sell", "i_order_type": "market", "i_limit": None, "b_market": "KR", "p_close": None,
             "p_low": None, "p_high": None, "p_change": None}  # fmt: skip
    assert bm.mismatch_cause(시장가, False) == "no_bar"
    assert bm.mismatch_cause(시장가, True) == "no_bar"


def test_칸_질의가_일봉을_붙인다() -> None:
    """배선 — 칸 질의가 일봉(`p_close`)을 읽지 않으면 일봉 없음 줄을 가르지 못한다"""
    c = Client(accounts=None)
    _db_줄(c, _kst("2026-11-09", "08:40"), 일봉=False)
    c.conn.execute("UPDATE paper_orders SET status = 'expired'")
    c.conn.execute("DELETE FROM paper_fills")
    for sql, 인자 in ((job.COMPARE_SQL, ["kis_vts", "2026-11-01", "2026-11-10", "H"]),
                     (job.CELLS_SQL, ["kis_vts", "2026-11-10", "H"])):  # fmt: skip
        줄 = c.execute(sql, 인자).dicts()
        assert bm.compare(줄)["excluded_no_bar"] == 1, sql
