"""KIS모의투자 장중 손절·익절 (docs/paper.md 15.11, docs/infra.md 25.1768 — 2026-10-23 사용자 지시)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from batch.core import db
from batch.jobs import broker_mirror as bmj
from batch.jobs import intraday_guard as ig
from batch.jobs import paper as jp
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from tests.test_kis_vts_1475 import _Sess
from tests.test_paper_job_1445 import Client

키 = {"KIS_VTS_APP_KEY": "k", "KIS_VTS_APP_SECRET": "s", "KIS_VTS_ACCOUNT": "12345678-01"}
월 = datetime(2026, 10, 26, 1, 30, tzinfo=UTC)  # 10-26(월) 10:30 KST


def _h(code: str, price: float | None, sellable: int = 10) -> kv.Holding:
    return kv.Holding(code, sellable, sellable, 100.0, price)


def test_감시_계좌는_베끼는_계좌() -> None:
    assert ig.GUARD_ACCOUNT == bmj.MIRROR_ACCOUNT == "H"


def test_닿은_보유_손절이_먼저() -> None:
    선 = {"A": (1, {"code": "A", "stop": 93.0}), "B": (2, {"code": "B", "stop": 80.0, "trail": 120.0}),
          "C": (3, {"code": "C", "target": 110.0}), "D": (4, {"code": "D", "stop": 93.0, "trail": 95.0})}  # fmt: skip
    hit = {x.code: (x.kind, x.reason) for x in ig.hits(
        [_h("A", 93.0), _h("B", 119.9), _h("C", 110.0), _h("D", 92.0), _h("E", 1.0)], 선)}  # fmt: skip
    assert hit == {"A": ("stop", bm.REASON_GUARD_STOP), "B": ("trail", bm.REASON_GUARD_TAKE),
                   "C": ("target", bm.REASON_GUARD_TAKE), "D": ("stop", bm.REASON_GUARD_STOP)}  # fmt: skip
    # 선 바로 위(판정식처럼 소수 여섯째 자리 — 1e-6 위는 안 닿음)·현재가 없음·팔 수량 0 은 보지 않는다(25.1769)
    assert ig.hits([_h("A", 93.0 * 1.000002), _h("A", None), _h("A", 50.0, sellable=0), _h("C", 110.0 * 0.999998)],
                   선) == []  # fmt: skip


def test_시간과_휴장일() -> None:
    assert ig.due(월) is None
    assert ig.due(datetime(2026, 10, 25, 1, 30, tzinfo=UTC)) == "휴장일"  # 일요일
    assert ig.due(datetime(2026, 10, 26, 0, 2, tzinfo=UTC)) == "장중 감시 시간 밖"  # 09:02 — 시가 단일가 직후
    assert ig.due(datetime(2026, 10, 26, 6, 20, tzinfo=UTC)) == "장중 감시 시간 밖"  # 15:20 — 마감 동시호가


class _C:
    """`guard_line` 이 읽는 종가 고점 한 줄"""

    def __init__(self, peak: float | None) -> None:
        self.peak = peak

    def execute(self, sql: str, args: list[Any]) -> Any:
        assert sql == jp.PEAK_SQL
        return SimpleNamespace(rows=[(self.peak,)])


def _보유(horizon: str = "short", scale: float = 1.0, ca: bool = False) -> tuple[Any, dict[str, Any]]:
    h = SimpleNamespace(stock_id=7, horizon=horizon, rights_scale=scale, corporate_action_recent=ca)
    return h, {"quantity": 10, "cost": 1_000.0, "market_value": 1_050.0, "first_buy_date": "2026-10-01"}


def test_감시선은_아침_판정식과_같다() -> None:
    h, 행 = _보유()
    오늘, 종목 = date(2026, 10, 23), {"ticker": "005930"}
    # 추세(H): 손절 = 원가 × 0.93, 고점이 목표(+10%)에 닿았으면 익절선 = 고점 × 0.93
    선 = jp.guard_line(_C(115.0), h, 행, "trend", None, 오늘, 종목)
    assert 선 is not None and 선["code"] == "005930" and 선["stop"] == pytest.approx(93.0)
    assert 선["qty"] == 10 and 선["ref"] == pytest.approx(105.0)  # H 수량·아침 판정 종가(25.1770·25.1771)
    assert 선["trail"] == pytest.approx(115.0 * 0.93) and "target" not in 선
    assert "trail" not in (jp.guard_line(_C(109.0), h, 행, "trend", None, 오늘, 종목) or {})  # 목표 전
    # 목표에서 파는 규칙(B core)은 목표가 익절선, 손절을 안 따르는 D(time)는 선이 없다
    assert jp.guard_line(_C(None), h, 행, "core", None, 오늘, 종목)["target"] == pytest.approx(110.0)
    assert jp.guard_line(_C(None), h, 행, "all", None, 오늘, 종목)["target"] == pytest.approx(110.0)
    assert jp.guard_line(_C(None), h, 행, "time", None, 오늘, 종목) is None
    # 권리락 계수는 원가에 곱한다(평가 수익률 식과 같다), 설정 기간 손절폭을 쓴다, 기업행위 직후는 모름
    assert jp.guard_line(_C(None), *_보유(scale=0.9), "core", None, 오늘, 종목)["stop"] == pytest.approx(83.7)
    # 익절선도 고점에 같은 계수(`pp.trend_exit` — 권리락 전 고점이 거짓 하락을 만들지 않게, 25.1769)
    assert jp.guard_line(_C(115.0), *_보유(scale=0.9), "trend", None, 오늘, 종목)["trail"] == pytest.approx(
        115.0 * 0.9 * 0.93)
    설정 = {"short": {"stop_pct": -5, "target_pct": 20}}
    assert jp.guard_line(_C(None), h, 행, "core", 설정, 오늘, 종목)["stop"] == pytest.approx(95.0)
    assert jp.guard_line(_C(None), *_보유(ca=True), "core", None, 오늘, 종목) is None


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    c = Client(accounts=None)
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend",
                  {1: {"code": "005930", "stop": 9_300.0}, 3: {"code": "000660", "stop": 50_000.0, "trail": 90_000.0}})
    return c


def test_장중_손절_한_번만_낸다(c: Client) -> None:
    s = _Sess([_h("005930", 9_250.0), _h("000660", 95_000.0)])
    r = ig.run(c, 월, session=s, clock=lambda: 월)
    assert r["hits"] == {"stop": 1} and r["sent"] == {"accepted": 1}
    assert s.orders == [("sell", "005930", 10, None)]  # 시장가 전량
    줄 = c.q("SELECT account_id, side, order_type, quantity, reason, status, message, paper_order_id"
             " FROM paper_broker_orders")  # fmt: skip
    assert 줄 == [("H", "sell", "market", 10, "장중손절", "accepted", "장중 현재가 9,250 ≤ 손절선 9,300", None)]
    assert "⚡ KIS모의투자 장중 매도" in r["alert"] and "종목1 10주" in r["alert"]
    # 다음 10분 — 같은 종목은 다시 내지 않는다
    assert "sent" not in ig.run(c, 월, session=_Sess([_h("005930", 9_100.0)]), clock=lambda: 월)
    assert ig.run_status(r) == "success"


def test_장중_익절도_낸다(c: Client) -> None:
    """사용자 "손절만이 아니라 익절도" — H 의 추적손절 선(25.1769 교차검증: run 수준에서 익절이 한 번도 안 돌았다)"""
    s = _Sess([_h("005930", 9_500.0), _h("000660", 89_000.0)])
    r = ig.run(c, 월, session=s, clock=lambda: 월)
    assert r["hits"] == {"trail": 1} and s.orders == [("sell", "000660", 10, None)]
    assert c.q("SELECT reason, message FROM paper_broker_orders") == [
        ("장중익절", "장중 현재가 89,000 ≤ 익절선(고점 대비) 90,000")]
    assert "익절선" in r["alert"]


def test_응답_없음은_다시_보내지_않고_순간_거부는_다시_보낸다(c: Client) -> None:
    from batch.sources.kis import KisFailed

    s = _Sess([_h("005930", 9_250.0)], fail={"005930": kv.NoResponse("모의 응답 없음 (Timeout)")})
    ig.run(c, 월, session=s, clock=lambda: 월)
    assert c.q("SELECT status FROM paper_broker_orders") == [("sending",)]  # 다음 아침 맞추기가 본다(PENDING_SQL)
    assert "sent" not in ig.run(c, 월, session=_Sess([_h("005930", 9_100.0)]), clock=lambda: 월)
    # 순간 거부(초당 한도)는 같은 줄로 다시 — 진짜 거부(잔고 부족)는 그대로
    c.conn.execute("DELETE FROM paper_broker_orders")
    ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)], fail={"005930": KisFailed("EGW00201 초당 거래건수 초과")}),
           clock=lambda: 월)  # fmt: skip
    assert c.q("SELECT status FROM paper_broker_orders") == [("rejected",)]
    다시 = ig.run(c, 월, session=_Sess([_h("005930", 9_200.0)]), clock=lambda: 월)
    assert 다시["sent"] == {"retried": 1, "accepted": 1}
    assert c.q("SELECT COUNT(*), MAX(status) FROM paper_broker_orders") == [(1, "accepted")]
    c.conn.execute("UPDATE paper_broker_orders SET status = 'rejected', message = 'APBK0400 주문가능수량 부족'")
    assert "sent" not in ig.run(c, 월, session=_Sess([_h("005930", 9_100.0)]), clock=lambda: 월)


def test_주문_뒤_기록이_깨져도_알림은_간다(c: Client) -> None:
    진짜 = c.execute

    def execute(sql: str, args: list[Any] | None = None) -> Any:
        if sql == bmj.SENT_UPDATE_SQL:
            raise RuntimeError("DB 끊김")
        return 진짜(sql, args)

    c.execute = execute  # type: ignore[method-assign]
    with pytest.raises(ig.GuardRecordError) as e:
        ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)]), clock=lambda: 월)
    assert "⚡ KIS모의투자 장중 매도" in e.value.record["alert"]
    assert c.q("SELECT status FROM paper_broker_orders") == [("sending",)]  # 다음 아침 맞추기가 본다


def test_안_도는_날(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _Sess([_h("005930", 9_250.0)])
    # 감시선이 오늘 것이 아니면(아침 단계 실패) 꺼진다
    화 = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)
    # 오늘 국내 일일 배치가 아직 안 끝났으면 기다림 — 알림·멈춤 아님(25.1770, 배치가 늦는 날 거짓 "꺼짐" 이었다)
    assert ig.run(c, 화, session=s) == {"off": ig.WAITING} and ig.run_status({"off": ig.WAITING}) == "success"
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-27T01:10:00+00:00', 'running')")  # 09:35 예비
    assert "skipped" in ig.run(c, 화, session=s)  # 실행 중이면 감시선을 보기 전에 쉰다
    c.conn.execute("UPDATE batch_runs SET status = 'success'")
    없음 = ig.run(c, 화, session=s)  # 배치는 끝났는데 감시선이 없다 — 진짜 멈춤
    assert ig.run_status(없음) == "skipped"  # 주간 요약이 센다
    assert ig.no_lines_alert(c, datetime(2026, 10, 27, 1, 30, tzinfo=UTC)).startswith("⚠️ KIS모의투자 장중 손절·익절 꺼짐")
    assert ig.no_lines_alert(c, datetime(2026, 10, 27, 1, 40, tzinfo=UTC)) is not None  # 보내기 전엔 표시 없음
    ig.mark_alerted(c, 화)
    assert ig.no_lines_alert(c, datetime(2026, 10, 27, 1, 40, tzinfo=UTC)) is None  # 보낸 뒤 하루 한 번
    c.conn.execute("DELETE FROM batch_runs")
    # 모의 매매 멈춤이면 내지 않는다
    db.set_setting(c, "paper_halt", {"on": True})
    assert ig.run(c, 월, session=s) == {"off": "모의 매매 멈춤(/paper)"}
    db.set_setting(c, "paper_halt", {"on": False})
    # 일일 배치가 돌고 있으면 쉰다(KIS 초당 한도)
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-26T01:10:00+00:00', 'running')")
    assert "skipped" in ig.run(c, 월, session=s)
    assert ig.run_status(ig.run(c, 월, session=s)) == "success"  # 정상 쉼은 주간 요약의 문제로 세지 않는다
    # 확인 실행은 세기만
    c.conn.execute("UPDATE batch_runs SET status = 'success'")
    assert ig.run(c, 월, session=s, dry_run=True)["hits"] == {"stop": 1} and s.orders == []
    monkeypatch.delenv("KIS_VTS_APP_KEY")
    assert ig.run(c, 월, session=s) == {"off": "KIS모의투자 키 없음"}


def test_비교와_잔고_대보기에서_장중_매도는_따로() -> None:
    줄 = [{"reason": bm.REASON_GUARD_STOP, "status": "filled", "side": "sell"},
          {"reason": bm.REASON_GUARD_TAKE, "status": "rejected", "side": "sell", "message": "APBK0400 거부"}]
    비교 = bm.compare(줄)
    assert 비교["n"] == 0 and 비교["guard"] == 2
    assert 비교["rejected"] == 1 and 비교["reject_reasons"] == {"APBK0400": 1}  # 장중 매도 거부도 까닭을 보인다
    assert any("장중 손절·익절 2" in x for x in bm.compare_lines(비교, None, None))


def test_장중에_판_종목은_H_가_같은_보유를_드는_동안_어긋남도_매수_베끼기도_없다(c: Client) -> None:
    """25.1768 교차검증 [필수] 1 — 예전엔 H 가 추가로 사면 KIS 도 새 몫만 사 잔고가 H 가 팔 때까지 어긋났다"""

    def 체결(day: str, oid: int, side: str = "buy", q: int = 10) -> None:
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
            " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
            " VALUES (?, 'H', 'KR', 1, ?, 'limit', 100, ?, 'KRW', ?, ?, 'r', 1, 1, 't')", [oid, side, q, day, day])
        c.conn.execute(
            "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
            " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (?, 'H', 1, ?, ?, 100, ?, 'KRW', 1, 0, 0,"
            " 0, -100, 't')", [oid, side, day, q])  # fmt: skip
    체결("2026-10-20", 900)
    ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)]), clock=lambda: 월)
    assert bmj.guard_held(c, bmj.BROKER, "H") == {1}
    체결("2026-10-27", 901, q=5)  # H 가 같은 보유에 더 샀다 — 여전히 장중 매도 뒤의 같은 보유
    assert bmj.guard_held(c, bmj.BROKER, "H") == {1}
    # 그 종목의 H 매수는 베끼지 않는다, 잔고 대보기는 KIS 0 · H 15 를 어긋남으로 보지 않는다(jobs 가 둘 다에서 뺀다)
    낼것, 안냄 = bm.plan([{"id": 7, "stock_id": 1, "side": "buy", "order_type": "limit", "limit_price": 100,
                          "quantity": 5}], {1: "005930"}, {}, {}, set(), already=set(), cleaned_today=set(),
                         halted=False, guard_held={1})  # fmt: skip
    assert 낼것 == [] and 안냄 == [(7, 1, bm.GUARD_HELD_SKIP)]
    체결("2026-10-28", 902, side="sell", q=15)  # H 가 그 보유를 다 팔았다
    assert bmj.guard_held(c, bmj.BROKER, "H") == set()
    체결("2026-10-29", 903)  # 새로 산 보유는 다시 베낀다
    assert bmj.guard_held(c, bmj.BROKER, "H") == set()
    # 안 찬(하한가)·거부 장중 매도는 KIS 가 아직 들고 있다 — 빼지 않는다
    c.conn.execute("UPDATE paper_broker_orders SET status = 'unfilled', filled_qty = 0")
    c.conn.execute("DELETE FROM paper_fills WHERE order_id >= 902")
    assert bmj.guard_held(c, bmj.BROKER, "H") == set()


def test_잔고_대보기_코드가_장중판_종목을_뺀다() -> None:
    """jobs 의 어긋남 계산이 `guard_held` 를 양쪽(증권사·내부)에서 빼는지 — 25.1768 교차검증 [권고](SQL 만 시험)"""
    import inspect

    원문 = inspect.getsource(bmj.run)
    assert "q > 0 and k not in 장중0" in 원문 and "k not in 연결전 and k not in 장중0" in 원문
    assert "장중0 = {k for k in 장중판 if 증권수.get(k, 0) <= 0}" in 원문  # KIS 가 0 주일 때만 뺀다(25.1770)
    assert "guard_held=장중판" in 원문 and "stop_floor=_오늘_손절선(client, 오늘, 베낌) if slot == MAIN" in 원문


def test_아침_가상모의투자_단계가_베끼는_계좌의_감시선을_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`jobs/paper` 의 매도 판정 자리에서 같은 입력으로 — 이 자리가 빠지면 장중 감시는 늘 "오늘 감시선 없음" 으로 꺼진다"""
    from tests.test_paper_job_1445 import _시세, _하루
    from tests.test_paper_job_1445 import client as 픽스처

    c = 픽스처.__wrapped__(monkeypatch)  # type: ignore[attr-defined]
    monkeypatch.setattr(ig, "GUARD_ACCOUNT", "B")  # 이 시험 장부는 A·B 두 계좌(B = core: 손절·목표도달)
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    _, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    값 = db.get_setting(c, ig.GUARD_KEY, None)
    칸 = 값["KR"]
    assert 칸["account"] == "B" and 칸["rule"] == "core" and 칸["as_of"] == "2026-10-16"
    선 = 칸["lines"]["1"]
    원가 = 선["cost"]
    assert 선["code"] == "005930" and 10_000 < 원가 < 10_010  # 수수료 포함 원가
    assert 선["stop"] == pytest.approx(원가 * 0.93) and 선["target"] == pytest.approx(원가 * 1.10)
    assert 기록["accounts"]["B"]["guard_lines"] == {"n": 1, "stop": 1, "trail": 0, "target": 1}
    assert "guard_lines" not in 기록["accounts"]["A"]


def test_장중_매도는_H_수량까지만() -> None:
    """사람이 모의계좌에서 손으로 산 몫까지 팔지 않는다(25.1770, 정리 주문 원칙)"""
    선 = {"A": (1, {"code": "A", "stop": 93.0, "qty": 4})}
    assert [x.quantity for x in ig.hits([_h("A", 90.0, sellable=10)], 선)] == [4]
    assert [x.quantity for x in ig.hits([_h("A", 90.0, sellable=3)], 선)] == [3]


def test_장중_손절선_이하_회차는_베끼지_않는다() -> None:
    """같은 날 장중 손절 뒤 더 빠져 하루짜리 회차가 KIS 에서만 차는 길(25.1770)"""
    선 = {"stop": 9_300.0, "cost": 10_000.0, "qty": 10, "stop_pct": -7.0}
    # 8,000 × 5 가 차면 평균 9,333 · 새 손절선 8,680 — 그 아래에서 사니 H 도 다음 아침 손절로 나간다 → 베끼지 않음.
    # 9,200 × 5 는 평균 9,733 · 새 손절선 9,052 위 — H 는 손절이 안 나 계속 든다 → 베낀다(옛 손절선 9,300 과 견주면
    # 걸러 KIS 만 수량이 모자랐다, 25.1771 교차검증)
    주문 = [{"id": 7, "stock_id": 1, "side": "buy", "order_type": "limit", "limit_price": 8_000, "quantity": 5},
            {"id": 8, "stock_id": 1, "side": "buy", "order_type": "limit", "limit_price": 9_200, "quantity": 5}]
    낼것, 안냄 = bm.plan(주문, {1: "005930"}, {}, {}, set(), already=set(), cleaned_today=set(), halted=False,
                         stop_floor={1: 선})  # fmt: skip
    assert [x.paper_order_id for x in 낼것] == [8] and 안냄 == [(7, 1, bm.STOP_FLOOR_SKIP)]
    assert not bm.buys_below_stop({"cost": 10_000.0, "qty": 10, "stop_pct": -7.0}, 8_000, 5)  # 손절이 없는 선


def test_장중_매도만_있는_주도_주간_요약에_나온다() -> None:
    비교 = bm.compare([{"reason": bm.REASON_GUARD_STOP, "status": "filled", "side": "sell"}] * 2)
    줄 = bm.compare_lines(비교, None, None)
    assert 줄 and "장중 손절·익절 2" in 줄[-1]


def test_정상_쉼과_진짜_멈춤() -> None:
    assert ig.run_status({"skipped": "잔고 조회가 쪽 상한에 닿아 잘림"}) == "skipped"
    assert ig.run_status({"skipped": "일일 배치 실행 중 — KIS 초당 한도를 나눠 쓰지 않게"}) == "success"
    assert ig.run_status({"off": "모의 매매 멈춤(/paper)"}) == "success"


def test_감시선_계산이_깨져도_매도_판정은_그대로(monkeypatch: pytest.MonkeyPatch) -> None:
    """`guard_line` 예외를 삼킨다 — H 매도 판정·가상모의투자 단계를 깨지 않게(25.1769·25.1770)"""
    from tests.test_paper_job_1445 import _시세, _하루
    from tests.test_paper_job_1445 import client as 픽스처

    def 깨짐(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("고점 읽기 실패")

    c = 픽스처.__wrapped__(monkeypatch)  # type: ignore[attr-defined]
    monkeypatch.setattr(ig, "GUARD_ACCOUNT", "B")
    monkeypatch.setattr(jp, "guard_line", 깨짐)
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    _, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert 기록["accounts"]["B"]["guard_lines"] == {"n": 0, "stop": 0, "trail": 0, "target": 0}
    assert "sells" in 기록["accounts"]["B"]  # 매도 판정은 그대로 돌았다


def test_main_은_보낸_뒤에_표시하고_실패도_알린다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """감시선이 없는 날 알림 한 번(보낸 뒤 표시), 실행 예외는 하루 한 번 알림 뒤 실패(25.1770)"""
    from batch.core import client as client_mod
    from batch.notify import telegram

    보낸: list[str] = []
    monkeypatch.setattr(telegram, "send", lambda t, *a, **k: 보낸.append(t) or [1])
    c.close = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr(client_mod, "TursoClient", lambda: c)
    monkeypatch.setattr(ig, "datetime", type("고정", (datetime,), {"now": classmethod(lambda cls, tz=None: 월)}))
    monkeypatch.setattr("sys.argv", ["intraday_guard"])
    db.set_setting(c, ig.GUARD_KEY, {})  # 감시선 없음
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-25T23:27:00+00:00', 'success')")
    assert ig.main() == 0 and len(보낸) == 1 and "꺼짐" in 보낸[0]
    assert ig.main() == 0 and len(보낸) == 1  # 하루 한 번
    assert c.q("SELECT status FROM batch_runs WHERE job_name = 'intraday_guard'") == [("skipped",), ("skipped",)]

    def 터짐(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError("토큰")

    monkeypatch.setattr(ig, "run", 터짐)
    with pytest.raises(RuntimeError):
        ig.main()
    assert len(보낸) == 2 and "실행 실패 (RuntimeError)" in 보낸[1]
    with pytest.raises(RuntimeError):
        ig.main()
    assert len(보낸) == 2  # 같은 날 같은 실패는 한 번


def test_권리락일은_감시선을_만들지_않는다(c: Client) -> None:
    """25.1770 교차검증 [필수] — 기계적 하락이 나는 바로 그날 권리락 전 원가의 손절선에 닿아 거짓 장중 매도가 나갔다"""
    c.conn.execute("INSERT INTO kr_corp_events (code, kind, record_date, source, fetched_at)"
                   " VALUES ('005930', 'bonus', '2026-10-27', 'kis', '2026-10-20T00:00:00+00:00')")  # 권리락일 10-26
    r = ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0},
                                                                   3: {"code": "000660", "stop": 50_000.0}})
    assert r["action_day"] == 1 and set(db.get_setting(c, ig.GUARD_KEY, None)["KR"]["lines"]) == {"3"}
    s = _Sess([_h("005930", 5_000.0)])
    assert "sent" not in ig.run(c, 월, session=s, clock=lambda: 월) and s.orders == []
    # 일정을 못 읽으면 그날 선을 모두 뺀다(보수)
    assert ig.action_today(None, [1, 3], date(2026, 10, 26)) == ({1, 3}, "AttributeError")  # type: ignore[arg-type]


def test_가격제한폭_밖_현재가는_기업행위_의심으로_팔지_않는다(c: Client) -> None:
    """액면분할 재상장 첫날처럼 기준가가 바뀐 날 — ±31%(웹 장중 감시 25.597 과 같은 문턱) 밖이면 알림만(25.1771)"""
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend",
                  {1: {"code": "005930", "stop": 9_300.0, "qty": 10, "ref": 10_000.0}})  # fmt: skip
    s = _Sess([_h("005930", 2_000.0, sellable=50)])  # 1:5 분할 재상장
    r = ig.run(c, 월, session=s, clock=lambda: 월)
    assert s.orders == [] and r["suspect"] == 1 and "기업행위 의심" in r["alert"]
    assert "alert" not in ig.run(c, 월, session=s, clock=lambda: 월)  # 종목마다 하루 한 번
    assert [x.kind for x in ig.hits([_h("005930", 9_250.0)], {"005930": (1, {"stop": 9_300.0, "ref": 10_000.0})})] == [
        "stop"]  # -7.5% 는 장중 움직임


def test_오늘_감시선을_아침_베끼기가_읽는다(c: Client) -> None:
    """`_오늘_손절선` — 오늘·베끼는 계좌의 선만(25.1771 교차검증: 늘 빈 것을 돌려줘도 시험이 통과했다)"""
    assert set(bmj._오늘_손절선(c, date(2026, 10, 26), "H")) == {1, 3}
    assert bmj._오늘_손절선(c, date(2026, 10, 27), "H") == {}  # 어제 선
    assert bmj._오늘_손절선(c, date(2026, 10, 26), "C") == {}  # 다른 계좌(도전자)


def test_다시_보낼_때_수량과_사유도_지금_값으로(c: Client) -> None:
    from batch.sources.kis import KisFailed

    ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)], fail={"005930": KisFailed("EGW00201 초당")}),
           clock=lambda: 월)  # fmt: skip
    ig.run(c, 월, session=_Sess([_h("005930", 9_250.0, sellable=7)]), clock=lambda: 월)
    assert c.q("SELECT quantity, reason, status FROM paper_broker_orders") == [(7, "장중손절", "accepted")]


def test_배치가_실패했거나_늦어도_감시선이_없으면_멈춤(c: Client) -> None:
    s = _Sess([_h("005930", 9_250.0)])
    화 = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)  # 10:30 KST
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-26T23:27:00+00:00', 'failed')")
    assert str(ig.run(c, 화, session=s)["off"]).startswith(ig.NO_LINES)  # 실패로 끝났다 — 감시선은 영영 안 생긴다
    c.conn.execute("DELETE FROM batch_runs")
    assert ig.run(c, 화, session=s) == {"off": ig.WAITING}
    assert str(ig.run(c, datetime(2026, 10, 27, 2, 10, tzinfo=UTC), session=s)["off"]).startswith(ig.NO_LINES)  # 11:10
