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


def _일정(c: Client, fetched_at: str = "2026-10-23T09:20:00+00:00") -> None:
    """기업행위 일정 수집이 살아 있다는 표시 한 줄(배당 — 권리락 거르기와 무관). 없으면 감시선을 모두 뺀다(25.1772)"""
    c.conn.execute("INSERT OR REPLACE INTO kr_corp_events (code, kind, record_date, source, fetched_at)"
                   " VALUES ('999999', 'dividend', '2026-12-31', 'kis', ?)", [fetched_at])


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    c = Client(accounts=None)
    _일정(c)
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
    assert 비교["guard_rejected"] == 1 and "guard_rejected" not in bm.compare([])  # 데스크 근거가 뺀다(25.1779)
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
    _일정(c, "2026-10-15T09:20:00+00:00")
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
    # 8,000 × 5 가 차면 함께 차는 9,200 × 5 까지 넣어 평균 9,300 · 새 손절선 8,649 — 그 아래에서 사니 H 도 다음 아침
    # 손절로 나간다 → 베끼지 않음(25.1773 주석 고침 — 회차 하나만 보면 평균 9,333 · 8,680).
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
    # 예외는 세어 두고, 선이 하나도 안 섰으면 장중 감시가 "감시선 없음" 으로 알린다(25.1773)
    assert 기록["accounts"]["B"]["guard_lines"] == {"n": 0, "stop": 0, "trail": 0, "target": 0,
                                                 "line_errors": {"RuntimeError": 1}}  # fmt: skip
    assert db.get_setting(c, ig.GUARD_KEY, None)["KR"]["dropped"] == "감시선 계산 오류(RuntimeError)"
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
    assert ig.action_today(None, [1, 3], date(2026, 10, 26)) == ({1, 3}, "기업행위 일정 읽기 실패(AttributeError)")  # type: ignore[arg-type]


def test_가격제한폭_밖_현재가는_기업행위_의심으로_팔지_않는다(c: Client) -> None:
    """액면분할 재상장 첫날처럼 기준가가 바뀐 날 — ±31%(웹 장중 감시 25.597 과 같은 문턱) 밖이면 알림만(25.1771)"""
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend",
                  {1: {"code": "005930", "stop": 9_300.0, "qty": 10, "ref": 10_000.0}})  # fmt: skip
    s = _Sess([_h("005930", 2_000.0, sellable=50)])  # 1:5 분할 재상장
    r = ig.run(c, 월, session=s, clock=lambda: 월)
    assert s.orders == [] and r["suspect"] == 1 and "기업행위 의심" in r["alert"]
    assert "alert" in ig.run(c, 월, session=s, clock=lambda: 월)  # 보내기 전엔 표시 없음 — 전송 실패면 다음 회차(25.1772)
    ig.mark_ids(c, 월, r["marks"])
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


def test_배치가_실패했거나_건너뛰어도_11시_전엔_기다림(c: Client) -> None:
    """25.1772(25.1771 교차검증 [필수]) — 08:27 이 한도·KrxNotYet 로 건너뛰거나 실패해도 09:35 예비·dispatch 가 다시
    돈다(`has_successful_run` 은 성공·부분만 센다). 그 사이 "꺼짐" 을 알리면 거짓이다. 11:00 이 지나면 멈춤"""
    s = _Sess([_h("005930", 9_250.0)])
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-26T23:27:00+00:00', 'skipped')")  # 08:27
    assert ig.run(c, datetime(2026, 10, 27, 0, 13, tzinfo=UTC), session=s) == {"off": ig.WAITING}  # 09:13
    c.conn.execute("UPDATE batch_runs SET status = 'failed'")
    화 = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)  # 10:30 KST
    assert ig.run(c, 화, session=s) == {"off": ig.WAITING}
    assert str(ig.run(c, datetime(2026, 10, 27, 2, 10, tzinfo=UTC), session=s)["off"]).startswith(ig.NO_LINES)  # 11:10
    c.conn.execute("UPDATE batch_runs SET status = 'partial'")
    assert str(ig.run(c, 화, session=s)["off"]).startswith(ig.NO_LINES)  # 부분 성공으로 끝났는데 선이 없다


def _정리줄(c: Client, sid: int = 1, code: str = "005930") -> None:
    """같은 날 아침 베끼기가 낸 정리 매도(증권사 주문 id 없음) — 유일 색인 `idx_paper_broker_cleanup` 을 차지한다"""
    c.conn.execute(
        "INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
        " quantity, trade_date, reason, status, source, created_at) VALUES ('kis_vts', NULL, 'H', 'KR', ?, ?, 'sell',"
        " 'market', 2, '2026-10-26', ?, 'accepted', 'kis_vts', '2026-10-25T23:40:00+00:00')",
        [sid, code, bm.REASON_CLEANUP])  # fmt: skip


def test_정리_줄과_겹쳐_못_내면_멈춤으로_남기고_알린다(c: Client) -> None:
    """25.1771 교차검증 [필수] — 예전엔 `skipped_other_row` 로 세기만 하고 success 로 닫혀 그날 내내 조용히 안 나갔다"""
    _정리줄(c)
    s = _Sess([_h("005930", 9_250.0)])
    r = ig.run(c, 월, session=s, clock=lambda: 월)
    assert s.orders == [] and r["sent"] == {"skipped_other_row": 1}
    assert ig.run_status(r) == "skipped" and "정리 매도 줄과 겹쳐" in r["alert"] and "종목1" in r["alert"]
    assert r["marks"] == {ig.OTHER_ROW_KEY: [1]}
    ig.mark_ids(c, 월, r["marks"])
    다음 = ig.run(c, 월, session=s, clock=lambda: 월)
    assert "alert" not in 다음 and ig.run_status(다음) == "skipped"  # 알림은 하루 한 번, 멈춤은 그대로 센다


def test_다시_보내기는_줄을_되찾았을_때만(c: Client) -> None:
    """RETRY 의 `status = 'rejected'` 조건이 막은 줄(아침 맞추기 등이 이미 바꿈)은 보내지 않는다(25.1772)"""
    from batch.sources.kis import KisFailed

    ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)], fail={"005930": KisFailed("EGW00201 초당")}),
           clock=lambda: 월)  # fmt: skip
    진짜 = c.execute

    def execute(sql: str, args: list[Any] | None = None) -> Any:
        if sql == ig.RETRY_SQL:  # 그 사이 다른 쪽이 줄을 바꿔 UPDATE 가 아무 행도 못 바꿈
            c.conn.execute("UPDATE paper_broker_orders SET status = 'unknown'")
        return 진짜(sql, args)

    c.execute = execute  # type: ignore[method-assign]
    s = _Sess([_h("005930", 9_250.0)])
    r = ig.run(c, 월, session=s, clock=lambda: 월)
    assert s.orders == [] and r["sent"] == {"skipped_other_run": 1}


def test_순간_거부_알림은_종목마다_하루_한_번(c: Client) -> None:
    from batch.sources.kis import KisFailed

    def 한번() -> dict:
        return ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)], fail={"005930": KisFailed("EGW00201 초당")}),
                      clock=lambda: 월)  # fmt: skip

    r = 한번()
    assert ig.TRANSIENT_TEXT in r["alert"] and "선에 아직 닿아 있으면" in r["alert"] and r["marks"] == {ig.TRANSIENT_KEY: [1]}
    ig.mark_ids(c, 월, r["marks"])
    assert "alert" not in 한번()  # 다시 보낸 것도 또 순간 거부 — 같은 알림은 하지 않는다
    r = ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)]), clock=lambda: 월)
    assert "접수" in r["alert"]  # 끝내 나간 것은 알린다


def test_잘린_잔고면_팔지_않고_멈춤(c: Client) -> None:
    s = _Sess([_h("005930", 9_250.0)])
    s.balance_truncated = True  # type: ignore[attr-defined]
    r = ig.run(c, 월, session=s, clock=lambda: 월)
    assert s.orders == [] and ig.run_status(r) == "skipped"


def test_확인_실행은_알림도_표시도_없다(c: Client) -> None:
    _정리줄(c)
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend",
                  {1: {"code": "005930", "stop": 9_300.0, "qty": 10, "ref": 10_000.0}})  # fmt: skip
    r = ig.run(c, 월, session=_Sess([_h("005930", 2_000.0)]), dry_run=True)
    assert "alert" not in r and "marks" not in r and r["suspect"] == 1


def _main_준비(c: Client, monkeypatch: pytest.MonkeyPatch, 보낸: list[str], 실패: bool = False) -> None:
    from batch.core import client as client_mod
    from batch.notify import telegram

    def send(t: str, *_a: Any, **_k: Any) -> list[int]:
        if 실패:
            raise RuntimeError("텔레그램 끊김")
        보낸.append(t)
        return [1]

    monkeypatch.setattr(telegram, "send", send)
    c.close = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr(client_mod, "TursoClient", lambda: c)
    monkeypatch.setattr(ig, "datetime", type("고정", (datetime,), {"now": classmethod(lambda cls, tz=None: 월)}))
    monkeypatch.setattr("sys.argv", ["intraday_guard"])


def test_알림이_실패하면_표시하지_않고_판_날은_부분으로(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1770 규칙(표시는 보낸 뒤) — 표시를 send 앞으로 옮겨도 예전 시험은 통과했다(25.1771 교차검증 M1·M2)"""
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    db.set_setting(c, ig.GUARD_KEY, {})  # 감시선 없음
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-25T23:27:00+00:00', 'success')")
    assert ig.main() == 0
    assert db.get_setting(c, ig.NO_LINES_ALERT_KEY, None) is None  # 못 보냈으니 다음 회차가 다시 알린다

    def 터짐(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError("토큰")

    with monkeypatch.context() as m:
        m.setattr(ig, "run", 터짐)
        with pytest.raises(RuntimeError):
            ig.main()
    assert db.get_setting(c, ig.FAIL_ALERT_KEY, None) is None
    # 판 뒤 알림이 실패하면 실행 기록은 부분 — 주문은 나갔는데 사람은 모른다. 하루 한 번 표시도 서지 않는다
    assert ig.run_status({"sent": {"accepted": 1}, "alert_error": "RuntimeError"}) == "partial"
    assert ig.run_status({"sent": {"skipped_other_run": 1}, "alert_error": "RuntimeError"}) == "success"
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}})
    _정리줄(c)
    from tests.test_kis_vts_1475 import _Sess as S

    monkeypatch.setattr(kv, "Session", lambda *a, **k: S([_h("005930", 9_250.0)]))
    assert ig.main() == 0
    assert db.get_setting(c, ig.OTHER_ROW_KEY, None) is None


def test_확인_실행_main_은_감시선_없음도_알리지_않는다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸)
    monkeypatch.setattr("sys.argv", ["intraday_guard", "--dry-run"])
    db.set_setting(c, ig.GUARD_KEY, {})
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-25T23:27:00+00:00', 'success')")
    assert ig.main() == 0 and 보낸 == [] and db.get_setting(c, ig.NO_LINES_ALERT_KEY, None) is None


    def 터짐(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError("토큰")

    monkeypatch.setattr(ig, "run", 터짐)
    with pytest.raises(RuntimeError):
        ig.main()
    assert 보낸 == [] and db.get_setting(c, ig.FAIL_ALERT_KEY, None) is None  # 확인 실행이 그날 실패 알림을 막지 않게


def test_기업행위_일정이_묵거나_비면_선을_모두_빼고_알린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1771 교차검증 [권고] — 일정 표는 실전 시세 키로 채운다(모의 키와 다르다). 비거나 묵으면 SQL 은 빈 결과를 줘
    권리락일 종목에도 선이 섰다"""
    c = Client(accounts=None)
    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    r = ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}})
    assert r["n"] == 0 and "비어 있음" in r["action_day_error"]
    _일정(c, "2026-10-18T09:20:00+00:00")  # 8일 묵음
    r = ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}})
    assert r["n"] == 0 and "묵음(2026-10-18)" in r["action_day_error"]
    off = ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)]))["off"]
    assert off.startswith(ig.NO_LINES) and ig.run_status({"off": off}) == "skipped"
    _일정(c, "2026-10-19T09:20:00+00:00")  # 7일 — 아직 씀
    assert ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}})["n"] == 1


def test_묵은_종가_보유는_감시선이_없다() -> None:
    """거래정지 뒤 재개 첫날은 기준가가 ±31% 안에서 바뀌어도 아침 배치가 모른다(25.1772, 25.1771 교차검증 [권고])"""
    h, 행 = _보유()
    오늘, 종목 = date(2026, 10, 26), {"ticker": "005930"}  # 직전 거래일 10-23(금)
    assert jp.guard_line(_C(None), h, 행 | {"price_date": "2026-10-23", "country": "KR"}, "core", None, 오늘, 종목)
    assert jp.guard_line(_C(None), h, 행 | {"price_date": "2026-10-22", "country": "KR"}, "core", None, 오늘,
                         종목) is None  # fmt: skip


def test_함께_차는_회차를_평균에_넣는다() -> None:
    """25.1771 교차검증 [권고] — 회차3 하나로만 보면 새 손절선 8,649 라 걸렀지만, 회차2·3 이 함께 차면 평균 9,200 ·
    손절선 8,556 이라 H 는 8,600 에 사고도 손절하지 않는다"""
    선 = {"stop": 9_300.0, "cost": 10_000.0, "qty": 10, "stop_pct": -7.0}
    주문 = [{"id": 7, "stock_id": 1, "side": "buy", "order_type": "limit", "limit_price": 9_000, "quantity": 10},
            {"id": 8, "stock_id": 1, "side": "buy", "order_type": "limit", "limit_price": 8_600, "quantity": 10}]
    assert bm.buys_below_stop(선, 8_600, 10)  # 하나만 보면 거른다
    낼것, 안냄 = bm.plan(주문, {1: "005930"}, {}, {}, set(), already=set(), cleaned_today=set(), halted=False,
                         stop_floor={1: 선})  # fmt: skip
    assert [x.paper_order_id for x in 낼것] == [7, 8] and 안냄 == []
    # 경계 — 새 손절선과 정확히 같은 지정가는 거른다(10,000×10 + 2,500×20 → 평균 5,000 · 손절 −50% 선 2,500)
    assert bm.buys_below_stop({"stop": 1.0, "cost": 10_000.0, "qty": 10, "stop_pct": -50.0}, 2_500, 20)
    # 시장가 매수에는 걸지 않는다(가격이 없다 — 걸면 float(None))
    낼것, 안냄 = bm.plan([{"id": 9, "stock_id": 1, "side": "buy", "order_type": "market", "limit_price": None,
                          "quantity": 5}], {1: "005930"}, {}, {}, set(), already=set(), cleaned_today=set(),
                         halted=False, stop_floor={1: 선})  # fmt: skip
    assert [x.paper_order_id for x in 낼것] == [9]


def test_장중판_보유_같은_날_매도_먼저와_모름_줄(c: Client) -> None:
    """같은 날 다 팔고 다시 산 보유는 새 보유다(`pp.positions` 처럼 매도 먼저), 국내 모름 장중 매도도 판 것으로 본다"""

    def 체결(day: str, oid: int, side: str, q: int) -> None:
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
            " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
            " VALUES (?, 'H', 'KR', 1, ?, 'limit', 100, ?, 'KRW', ?, ?, 'r', 1, 1, 't')", [oid, side, q, day, day])
        c.conn.execute(
            "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
            " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (?, 'H', 1, ?, ?, 100, ?, 'KRW', 1, 0, 0,"
            " 0, -100, 't')", [oid, side, day, q])  # fmt: skip

    체결("2026-10-20", 900, "buy", 10)
    ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)]), clock=lambda: 월)
    c.conn.execute("UPDATE paper_broker_orders SET status = 'unknown', filled_qty = NULL")
    assert bmj.guard_held(c, bmj.BROKER, "H") == {1}  # 모름 — 실제로 찼을 수 있다
    체결("2026-10-27", 901, "buy", 5)  # 다음 날 H 가 다 팔고 같은 날 새로 샀다
    체결("2026-10-27", 902, "sell", 10)
    # 매도 먼저(10 → 0 → 5): 10-27 에 연 새 보유라 다시 베낀다. 매수 먼저로 세면 10 → 15 → 5 로 10-20 보유가 이어져
    # KIS 는 새 보유를 영영 못 샀다
    assert bmj.guard_held(c, bmj.BROKER, "H") == set()


def test_요약_끝_줄에_장중_매도_뒤_H_만_든_종목() -> None:
    줄 = bm.compare_lines(bm.compare([]) | {"guard_held": 2}, None, None)
    assert 줄 and "장중 매도 뒤 H 만 든 종목 2" in 줄[-1]


def test_감시선은_국내만_계산한다() -> None:
    import inspect

    assert 'aid == intraday_guard.GUARD_ACCOUNT and market == "KR"' in inspect.getsource(jp)


def test_상태_목록은_장중_감시의_마지막_한_줄만() -> None:
    """25.1771 5번 — 10분마다 한 줄이 최근 실행 목록을 밀어내지 않게(웹 `health.ts` 와 같은 조건)"""
    import re
    import sqlite3
    from pathlib import Path

    import scripts.db_status as ds

    원문 = Path(ds.__file__).read_text(encoding="utf-8")
    조건 = re.search(r'" FROM batch_runs WHERE job_name <> \'intraday_guard\'"\n\s*" OR id = \(SELECT MAX\(id\) FROM batch_runs'
                     r" WHERE job_name = 'intraday_guard'\) ORDER BY id DESC LIMIT 30\"", 원문)  # fmt: skip
    assert 조건, "db_status 의 최근 실행 질의가 바뀌었다"
    db_ = sqlite3.connect(":memory:")
    db_.execute("CREATE TABLE batch_runs (id INTEGER PRIMARY KEY, job_name TEXT)")
    db_.executemany("INSERT INTO batch_runs (job_name) VALUES (?)", [("daily_kr",), *[("intraday_guard",)] * 3])
    줄 = db_.execute("SELECT id, job_name FROM batch_runs WHERE job_name <> 'intraday_guard'"
                    " OR id = (SELECT MAX(id) FROM batch_runs WHERE job_name = 'intraday_guard') ORDER BY id DESC").fetchall()
    assert 줄 == [(4, "intraday_guard"), (1, "daily_kr")]
    웹 = (Path(ds.__file__).parents[1] / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
    assert "FROM batch_runs WHERE job_name <> 'intraday_guard'\n  OR id = (SELECT MAX(id) FROM batch_runs WHERE" in 웹



def test_주식배당_락일도_감시선을_만들지_않는다(c: Client) -> None:
    """25.1772 교차검증 [필수] — 주식배당은 배당 일정(`dividend`)에 `stk_divi_rate` 로 온다. 5% 하락은 ±31% 가드에
    안 걸려 시장가 매도가 나갔고, 다음 아침 H 는 기업행위로 그 종목을 멈춰 KIS 만 판 채로 갈렸다"""
    c.conn.execute("INSERT INTO kr_corp_events (code, kind, record_date, detail, source, fetched_at) VALUES ('005930',"
                   " 'dividend', '2026-10-27', '{\"stk_divi_rate\":\"5\"}', 'kis', '2026-10-20T00:00:00+00:00')")  # 락일 10-26
    c.conn.execute("INSERT INTO kr_corp_events (code, kind, record_date, detail, source, fetched_at) VALUES ('000660',"
                   " 'dividend', '2026-10-27', '{\"stk_divi_rate\":\"0\",\"per_sto_divi_amt\":\"500\"}', 'kis',"
                   " '2026-10-20T00:00:00+00:00')")  # 현금배당 — 거르지 않는다(수 % 기계적 하락은 판정식도 그대로 본다)
    r = ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend",
                      {1: {"code": "005930", "stop": 9_300.0, "qty": 10, "ref": 9_600.0},
                       3: {"code": "000660", "stop": 50_000.0}})  # fmt: skip
    assert r["action_day"] == 1 and set(db.get_setting(c, ig.GUARD_KEY, None)["KR"]["lines"]) == {"3"}
    s = _Sess([_h("005930", 9_143.0)])
    assert "sent" not in ig.run(c, 월, session=s, clock=lambda: 월) and s.orders == []
    assert ig.stock_dividend('{"stk_divi_rate": "0.05"}') and not ig.stock_dividend("깨진 글")


def test_잘린_일정은_베끼는_계좌의_국내_보유도_다시_받는다() -> None:
    from batch.jobs import kis_flows as kf

    assert f"f.account_id = '{bmj.MIRROR_ACCOUNT}'" in kf.HELD_KR_TICKERS
    c = Client(accounts=None)
    c.conn.execute("INSERT INTO paper_accounts (id, name, created_at) VALUES ('H', 'H', 't')") if not c.q(
        "SELECT 1 FROM paper_accounts WHERE id = 'H'") else None
    for oid, side, q in ((900, "buy", 10), (901, "sell", 4)):
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
            " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
            " VALUES (?, 'H', 'KR', 1, ?, 'limit', 100, ?, 'KRW', '2026-10-20', '2026-10-20', 'r', 1, 1, 't')",
            [oid, side, q])  # fmt: skip
        c.conn.execute(
            "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
            " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (?, 'H', 1, ?, '2026-10-20', 100, ?, 'KRW', 1,"
            " 0, 0, 0, -100, 't')", [oid, side, q])  # fmt: skip
    assert ("005930",) in c.q(kf.HELD_KR_TICKERS)


def test_감시선_계산이_모두_깨지면_감시선_없음으로_알린다(c: Client) -> None:
    """25.1772 교차검증 [권고] — 예외를 삼키고 세지 않아 lines 0·success 로 매일 조용히 꺼졌다"""
    r = ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {}, {"KeyError": 3})
    assert r["n"] == 0 and r["line_errors"] == {"KeyError": 3}
    off = ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)]))["off"]
    assert off.startswith(ig.NO_LINES) and "감시선 계산 오류(KeyError)" in off
    assert "guard_lines 확인" in ig.no_lines_alert(c, 월, off)
    # 일정 묵음이면 일일 배치가 아니라 kis-flows·실전 키를 보라고 적는다
    assert "kis-flows" in ig.no_lines_alert(c, 월, f"{ig.NO_LINES} — 기업행위 일정 묵음(2026-10-14)")
    assert "일일 배치 확인" in ig.no_lines_alert(c, 월, f"{ig.NO_LINES} — 오늘 국내 일일 배치가 끝났거나")


def test_감시선_저장이_깨져도_가상모의투자_단계는_그대로(monkeypatch: pytest.MonkeyPatch) -> None:
    """`save_lines` 예외를 삼킨다 — 설정 쓰기·일정 읽기 한 번 실패로 H 매도 판정·주문 단계가 깨지지 않게(25.1768)"""
    from tests.test_paper_job_1445 import _시세, _하루
    from tests.test_paper_job_1445 import client as 픽스처

    def 깨짐(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("설정 쓰기 실패")

    c = 픽스처.__wrapped__(monkeypatch)  # type: ignore[attr-defined]
    monkeypatch.setattr(ig, "GUARD_ACCOUNT", "B")
    monkeypatch.setattr(ig, "save_lines", 깨짐)
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    _, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert 기록["accounts"]["B"]["guard_lines"] == {"error": "RuntimeError"} and "sells" in 기록["accounts"]["B"]


def test_기업행위_의심_문턱은_하한가_바로_밖() -> None:
    """±31% — 하한가(−30%)는 장중 움직임, −31.5% 는 의심(여유 1%p 를 빼면 부동소수로 −30% 가 의심이 됐다)"""
    선 = {"005930": (1, {"stop": 9_300.0, "ref": 10_000.0})}
    assert [x.kind for x in ig.hits([_h("005930", 7_000.0)], 선)] == ["stop"]
    assert [x.kind for x in ig.hits([_h("005930", 6_850.0)], 선)] == ["suspect"]


def test_선_비교는_반올림한다() -> None:
    """원가에 소수가 있는 감시선 — 9,299.9999996 에 현재가 9,300 은 닿음(판정식처럼 소수 여섯째 자리 반올림, 25.1769)"""
    assert [x.kind for x in ig.hits([_h("A", 9_300.0)], {"A": (1, {"stop": 9_299.9999996})})] == ["stop"]


def test_그_아래_회차는_평균에_넣지_않는다() -> None:
    """함께 차는 것은 그 **이상** 지정가 회차만 — 아래 회차를 섞으면 평균이 내려가 걸러야 할 회차를 베꼈다(25.1773)"""
    선 = {"stop": 9_300.0, "cost": 10_000.0, "qty": 10, "stop_pct": -7.0}
    주문 = [{"id": 7, "stock_id": 1, "side": "buy", "order_type": "limit", "limit_price": 8_000, "quantity": 5},
            {"id": 8, "stock_id": 1, "side": "buy", "order_type": "limit", "limit_price": 3_000, "quantity": 100}]
    _낼것, 안냄 = bm.plan(주문, {1: "005930"}, {}, {}, set(), already=set(), cleaned_today=set(), halted=False,
                          stop_floor={1: 선})  # fmt: skip
    assert (7, 1, bm.STOP_FLOOR_SKIP) in 안냄


def test_장중_매도_거부_까닭은_KIS_문구로_묶는다() -> None:
    줄 = [{"reason": bm.REASON_GUARD_STOP, "status": "rejected", "side": "sell",
           "message": f"장중 현재가 {p:,} ≤ 손절선 9,300 · 하한가 주문 불가"} for p in (9_100, 9_050)]
    assert bm.compare(줄)["reject_reasons"] == {"하한가 주문 불가": 2}


def test_장중_감시_워크플로는_공개_로그에_찍지_않는다() -> None:
    from pathlib import Path

    글 = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "kis-guard.yml").read_text(encoding="utf-8")
    assert "python -m batch.jobs.intraday_guard $ARGS 2>&1 | python scripts/ops_tee.py run.log" in 글
    assert "set -o pipefail" in 글 and "if: always() && (failure()" in 글 and "scripts/publish_output.py" in 글


def test_감시_창은_그날_개장과_마감을_따른다() -> None:
    """새해 첫 거래일(10:00 개장)·수능일(한 시간 늦춤) — 고정 창이면 장 열리기 전 가격으로 팔았다(25.1774)"""
    def at(d: str, hm: str) -> datetime:
        return datetime.fromisoformat(f"{d}T{hm}:00+00:00")

    assert ig.due(at("2027-01-04", "00:13")) == "장중 감시 시간 밖"  # 09:13 KST — 아직 장전
    assert ig.due(at("2027-01-04", "01:06")) is None  # 10:06
    assert ig.due(at("2026-11-19", "00:53")) == "장중 감시 시간 밖"  # 수능일 09:53
    assert ig.due(at("2026-11-19", "01:06")) is None and ig.due(at("2026-11-19", "07:13")) is None  # 16:13
    assert ig.due(at("2026-11-19", "07:20")) == "장중 감시 시간 밖"  # 16:20 — 마감 동시호가
    assert ig.due(at("2026-10-26", "06:15")) is None and ig.due(at("2026-10-26", "06:16")) == "장중 감시 시간 밖"
    assert ig.due(at("2026-10-26", "00:05")) is None and ig.due(at("2026-10-26", "00:04")) == "장중 감시 시간 밖"


def test_어제_배치_성공은_오늘_끝남이_아니다(c: Client) -> None:
    """'오늘' 경계 — 어제 성공 기록으로 끝남을 세면 배치가 늦는 날 거짓 꺼짐이 되살아난다(25.1770 [필수])"""
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-23', 'cron', '2026-10-25T23:27:00+00:00', 'success')")  # 10-26 08:27
    assert ig.run(c, datetime(2026, 10, 27, 1, 30, tzinfo=UTC), session=_Sess([])) == {"off": ig.WAITING}


def test_오늘이_아닌_락일은_선을_남긴다(c: Client) -> None:
    """권리락·주식배당 거르기는 **오늘** 락일만 — 창(14일) 안 다른 날까지 빼면 락일 전 2주 동안 감시가 조용히 빠졌다"""
    c.conn.execute("INSERT INTO kr_corp_events (code, kind, record_date, source, fetched_at) VALUES ('005930', 'bonus',"
                   " '2026-10-29', 'kis', '2026-10-20T00:00:00+00:00')")  # 권리락일 10-28
    c.conn.execute("INSERT INTO kr_corp_events (code, kind, record_date, detail, source, fetched_at) VALUES ('000660',"
                   " 'dividend', '2026-10-29', '{\"stk_divi_rate\":\"5\"}', 'kis', '2026-10-20T00:00:00+00:00')")
    r = ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0},
                                                                   3: {"code": "000660", "stop": 50_000.0}})
    assert r["n"] == 2 and "action_day" not in r


def test_판_뒤_알림이_실패하면_부분으로_닫고_다음_회차가_다시_보낸다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1773 교차검증 [권고] — 판 종목은 다음 회차부터 '이미' 로 빠져 알림 글이 다시 안 만들어졌다"""
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0)]))
    assert ig.main() == 0
    assert c.q("SELECT status FROM batch_runs WHERE job_name = 'intraday_guard'") == [("partial",)]
    assert c.q("SELECT status FROM paper_broker_orders") == [("accepted",)]
    _main_준비(c, monkeypatch, 보낸)
    assert ig.main() == 0 and len(보낸) == 1
    assert 보낸[0].startswith("(앞 회차에 보내지 못했거나 전송 여부를 모르는") and "⚡ KIS모의투자 장중 매도" in 보낸[0]
    assert ig.main() == 0 and len(보낸) == 1  # 보낸 뒤 지웠다


def test_일부_보유의_감시선_계산이_깨지면_멈춤으로_남기고_하루_한_번_알린다(c: Client) -> None:
    """25.1773 교차검증 [권고] — 모두 깨질 때만 막아 일부만 깨진 종목은 매일 조용히 감시 밖이었다"""
    r = ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}},
                      {"KeyError": 2})  # fmt: skip
    assert r["n"] == 1 and db.get_setting(c, ig.GUARD_KEY, None)["KR"]["line_errors"] == 2
    a = ig.run(c, 월, session=_Sess([_h("005930", 9_500.0)]), clock=lambda: 월)
    assert ig.run_status(a) == "skipped" and "감시선 계산 오류 2건" in a["alert"] and a["date_marks"] == [ig.LINE_ERR_KEY]
    ig.mark_alerted(c, 월, ig.LINE_ERR_KEY)
    b = ig.run(c, 월, session=_Sess([_h("005930", 9_500.0)]), clock=lambda: 월)
    assert "alert" not in b and ig.run_status(b) == "skipped"


def test_익절선은_고점이_목표에_정확히_닿으면_선다() -> None:
    h, 행 = _보유()
    assert "trail" in jp.guard_line(_C(110.0), h, 행, "trend", None, date(2026, 10, 23), {"ticker": "005930"})


def test_다_판_종목은_다시_받기_대상이_아니다() -> None:
    from batch.jobs import kis_flows as kf

    c = Client(accounts=None)
    for oid, side in ((900, "buy"), (901, "sell")):
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
            " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
            " VALUES (?, 'H', 'KR', 1, ?, 'limit', 100, 10, 'KRW', '2026-10-20', '2026-10-20', 'r', 1, 1, 't')",
            [oid, side])  # fmt: skip
        c.conn.execute(
            "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
            " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (?, 'H', 1, ?, '2026-10-20', 100, 10, 'KRW', 1,"
            " 0, 0, 0, -100, 't')", [oid, side])  # fmt: skip
    assert ("005930",) not in c.q(kf.HELD_KR_TICKERS)


def test_묵은_실행_중_기록은_45분_뒤엔_막지_않는다(c: Client) -> None:
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-26T00:44:00+00:00', 'running')")  # 46분 전
    assert "skipped" not in ig.run(c, 월, session=_Sess([_h("005930", 9_500.0)]), clock=lambda: 월)


def test_main_은_계산_오류_알림을_하루_한_번(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸)
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}}, {"KeyError": 1})
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_500.0)]))
    assert ig.main() == 0 and ig.main() == 0
    assert len(보낸) == 1 and "감시선 계산 오류 1건" in 보낸[0]



def test_계산_오류가_있는_날도_판_뒤_알림_실패는_부분(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1775 — 멈춤(skipped) 검사가 앞에 있어 '주문은 나갔는데 사람은 모름' 이 묻혔다"""
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}}, {"KeyError": 1})
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0)]))
    assert ig.main() == 0
    assert c.q("SELECT status FROM batch_runs WHERE job_name = 'intraday_guard'") == [("partial",)]
    # 남긴 글은 판 알림 원문뿐(머리·계산 오류 문단 없음) — 다음 회차가 오류 문단을 다시 만든다
    남김 = db.get_setting(c, ig.UNSENT_KEY, None)["text"]
    assert 남김.startswith("[2026-10-26] ⚡ KIS모의투자 장중 매도") and "계산 오류" not in 남김
    assert ig.main() == 0  # 또 실패 — 머리 없이 그대로(판 종목은 '이미')
    assert db.get_setting(c, ig.UNSENT_KEY, None)["text"] == 남김


def test_판_회차에도_계산_오류를_알린다(c: Client) -> None:
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}}, {"KeyError": 1})
    r = ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)]), clock=lambda: 월)
    assert "⚡ KIS모의투자 장중 매도" in r["alert"] and "감시선 계산 오류 1건" in r["alert"]
    assert r["date_marks"] == [ig.LINE_ERR_KEY] and r["sent"] == {"accepted": 1}


def test_못_보낸_글은_보낸_뒤_지우고_지난_장_글도_보낸다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """마지막 회차에서 실패하면 다음 날 날짜가 달라 버려졌다. 지울 때 null 로 덮으면 매 회차 경고가 났다(25.1775)"""
    import logging

    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸)
    db.set_setting(c, ig.UNSENT_KEY, {"date": "2026-10-23", "text": "⚡ 지난 장 매도"})
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_500.0)]))
    assert ig.main() == 0 and len(보낸) == 1 and 보낸[0].startswith("(지난 장(2026-10-23)부터 보내지 못했거나")
    assert db.get_setting(c, ig.UNSENT_KEY, None) == {}  # 빈 칸 — 배치는 설정 행을 지우지 않는다
    with monkeypatch.context():
        로그: list[str] = []
        h = logging.Handler()
        h.emit = lambda rec: 로그.append(rec.getMessage())  # type: ignore[method-assign]
        logging.getLogger().addHandler(h)
        try:
            assert ig.main() == 0 and len(보낸) == 1
        finally:
            logging.getLogger().removeHandler(h)
        assert not [x for x in 로그 if ig.UNSENT_KEY in x]


def test_확인_실행은_못_보낸_글을_보내지도_지우지도_않는다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸)
    monkeypatch.setattr("sys.argv", ["intraday_guard", "--dry-run"])
    db.set_setting(c, ig.UNSENT_KEY, {"date": "2026-10-26", "text": "⚡ 매도"})
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_500.0)]))
    assert ig.main() == 0 and 보낸 == [] and db.get_setting(c, ig.UNSENT_KEY, None)["text"] == "⚡ 매도"


def test_판_게_없는_알림이_실패하면_남기지_않는다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """의심·오류·감시선 없음 알림은 표시가 안 적혀 다음 회차가 다시 만든다 — 남기면 두 번 간다"""
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend", {1: {"code": "005930", "stop": 9_300.0}}, {"KeyError": 1})
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_500.0)]))
    assert ig.main() == 0 and db.get_setting(c, ig.UNSENT_KEY, None) is None


def test_45분_안의_실행_중_기록은_막는다(c: Client) -> None:
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-26T00:46:00+00:00', 'running')")  # 44분 전
    assert "skipped" in ig.run(c, 월, session=_Sess([_h("005930", 9_500.0)]), clock=lambda: 월)



def test_못_보낸_판_알림은_회차를_넘어_쌓이고_처음_날짜를_지킨다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """두 회차에 판 알림이 모두 실패해도 둘 다 간다(덮어쓰면 앞 매도 알림이 사라졌다). 지난 장 글이 남은 채 오늘도
    실패하면 날짜는 처음 못 보낸 날 그대로(25.1775 교차검증)"""
    import json

    ig.save_lines(c, "KR", date(2026, 10, 26), "H", "trend",
                  {1: {"code": "005930", "stop": 9_300.0}, 3: {"code": "000660", "stop": 50_000.0}})  # fmt: skip
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    db.set_setting(c, ig.UNSENT_KEY, {"date": "2026-10-23", "text": "⚡ 지난 장 매도"})
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0), _h("000660", 60_000.0)]))
    assert ig.main() == 0
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0), _h("000660", 49_000.0)]))
    assert ig.main() == 0
    남김 = db.get_setting(c, ig.UNSENT_KEY, None)
    assert 남김["date"] == "2026-10-23" and 남김["text"].count("⚡ KIS모의투자 장중 매도") == 2
    # 실행 기록에 종목·수량 글은 넣지 않는다, 앞 글이 남은 채 실패한 회차도 부분
    로그 = [json.loads(x[0]) for x in c.q("SELECT step_log FROM batch_runs WHERE job_name = 'intraday_guard'")]
    assert all("sale_alert" not in x["guard"] for x in 로그)
    assert c.q("SELECT status FROM batch_runs WHERE job_name = 'intraday_guard'") == [("partial",), ("partial",)]
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0), _h("000660", 49_000.0)]))
    assert ig.main() == 0  # 새로 판 게 없고 남은 글 다시 보내기만 실패 — 그래도 부분
    assert c.q("SELECT status FROM batch_runs WHERE job_name = 'intraday_guard' ORDER BY id DESC LIMIT 1") == [
        ("partial",)]
    _main_준비(c, monkeypatch, 보낸)
    assert ig.main() == 0 and len(보낸) == 1 and 보낸[0].count("종목") >= 2 and "⚡ 지난 장 매도" in 보낸[0]


@pytest.mark.parametrize("실패", ["응답 없음", "거부"])
def test_응답_없음과_거부도_못_보내면_남긴다(c: Client, monkeypatch: pytest.MonkeyPatch, 실패: str) -> None:
    from batch.sources.kis import KisFailed

    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    예외 = kv.NoResponse("모의 응답 없음") if 실패 == "응답 없음" else KisFailed("APBK0400 주문가능수량 부족")
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0)], fail={"005930": 예외}))
    assert ig.main() == 0 and "⚡ KIS모의투자 장중 매도" in db.get_setting(c, ig.UNSENT_KEY, None)["text"]


def test_기록이_깨지고_알림도_실패하면_글을_남긴다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    진짜 = c.execute

    def execute(sql: str, args: list[Any] | None = None) -> Any:
        if sql == bmj.SENT_UPDATE_SQL:
            raise RuntimeError("DB 끊김")
        return 진짜(sql, args)

    c.execute = execute  # type: ignore[method-assign]
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0)]))
    with pytest.raises(RuntimeError):
        ig.main()
    c.execute = 진짜  # type: ignore[method-assign]
    assert "⚡ KIS모의투자 장중 매도" in db.get_setting(c, ig.UNSENT_KEY, None)["text"]


def test_예전_null_행은_고친다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1774 가 다시 보낸 뒤 null 로 덮은 행 — 매 회차 '설정을 읽지 못함' 경고가 났다"""
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸)
    db.set_setting(c, ig.UNSENT_KEY, None)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_500.0)]))
    assert ig.main() == 0 and c.q("SELECT value FROM settings WHERE key = ?", [ig.UNSENT_KEY]) == [("{}",)]



def test_남은_글은_문단마다_날짜를_달고_쉬는_회차도_보낸다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """지난 장 글 + 오늘 실패 — 한 머리 아래 서로의 날짜로 읽혔다. 다음 장 첫 회차가 감시선 대기여도 남은 글은 간다"""
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    db.set_setting(c, ig.UNSENT_KEY, {"date": "2026-10-23", "text": "[2026-10-23] ⚡ 지난 장 매도"})
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0)]))
    assert ig.main() == 0
    assert db.get_setting(c, ig.UNSENT_KEY, None)["text"].split("\n")[1].startswith("[2026-10-26] ⚡")
    db.set_setting(c, ig.GUARD_KEY, {})  # 감시선 없음(배치는 끝남) 회차
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-25T23:27:00+00:00', 'success')")
    assert ig.main() == 0  # 또 실패 — 감시선이 없는(멈춤) 회차라도 부분
    assert c.q("SELECT status FROM batch_runs WHERE job_name = 'intraday_guard' ORDER BY id DESC LIMIT 1") == [
        ("partial",)]
    _main_준비(c, monkeypatch, 보낸)
    assert ig.main() == 0 and 보낸[0].startswith("(지난 장(2026-10-23)부터 보내지 못했거나")
    assert "[2026-10-23] ⚡ 지난 장 매도" in 보낸[0] and "[2026-10-26] ⚡ KIS모의투자 장중 매도" in 보낸[0]


def test_알림이_끊긴_동안_순간_거부_문단은_한_번만_쌓인다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.sources.kis import KisFailed

    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0)],
                                                            fail={"005930": KisFailed("EGW00201 초당")}))  # fmt: skip
    for _ in range(3):
        assert ig.main() == 0
    assert db.get_setting(c, ig.UNSENT_KEY, None)["text"].count("거부(초당") == 1
    # 텔레그램이 돌아오면 그 한 문단이 가고, 표시는 다시 보내기(RETRY)를 막지 않는다 — 거부 줄은 같은 줄로 다시 접수
    _main_준비(c, monkeypatch, 보낸)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([_h("005930", 9_250.0)]))
    assert ig.main() == 0 and 보낸[0].count("거부(초당") == 1 and "→ 접수" in 보낸[0]
    assert c.q("SELECT COUNT(*), MAX(status) FROM paper_broker_orders") == [(1, "accepted")]


def test_대기_잘림_회차도_남은_글이_또_못_가면_부분() -> None:
    assert ig.run_status({"skipped": "잔고 조회가 쪽 상한에 닿아 잘림", "alert_error": "X", "unsent_pending": True}) == "partial"
    assert ig.run_status({"off": ig.WAITING, "alert_error": "X", "unsent_pending": True}) == "partial"


def test_같은_날_다시_돈_단계가_못_적은_감시선을_다시_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """첫 실행의 감시선 저장이 깨졌으면 같은 날 다시 돌린 단계("이미 냄" 가지)가 다시 적는다(25.1780)"""
    from tests.test_paper_job_1445 import _시세, _하루
    from tests.test_paper_job_1445 import client as 픽스처

    c = 픽스처.__wrapped__(monkeypatch)  # type: ignore[attr-defined]
    monkeypatch.setattr(ig, "GUARD_ACCOUNT", "B")
    _일정(c, "2026-10-15T09:20:00+00:00")
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    진짜 = ig.save_lines

    def 깨짐(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("설정 쓰기 실패")

    with monkeypatch.context() as m:
        m.setattr(ig, "save_lines", 깨짐)
        _, 첫 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert 첫["accounts"]["B"]["guard_lines"] == {"error": "RuntimeError"} and not ig.lines_saved(c, date(2026, 10, 16), "B")
    assert ig.save_lines is 진짜
    if 첫["accounts"]["B"].get("orders") == 0:  # 이 장부는 그날 B 결정이 없다 — "이미 냄" 가지를 타게 낸 날을 만든다
        c.conn.execute(
            "INSERT INTO paper_orders (account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
            " decided_on, fill_session, reason, rule_version, tranche, created_at)"
            " VALUES ('B', 'KR', 1, 'buy', 'limit', 100, 1, 'KRW', '2026-10-15', '2099-01-01', 'r', 1, 1, 't')")
    _, 다시 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert 다시["accounts"]["B"]["orders"] == "이미 냄" and 다시["accounts"]["B"]["guard_lines"]["n"] == 1
    assert ig.lines_saved(c, date(2026, 10, 16), "B")
    _, 셋째 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert "guard_lines" not in 셋째["accounts"]["B"]  # 이미 적혔으면 다시 계산하지 않는다
    assert not ig.lines_saved(c, date(2026, 10, 16), "H")  # 다른 계좌가 적은 칸은 이 계좌 것이 아니다
    # 계산 오류로 빠진 선이 있는 칸은 "안 적힘" — 다시 돌리기로 되살린다(25.1781). 일정 묵음으로 뺀 칸은 적힌 것
    값 = db.get_setting(c, ig.GUARD_KEY, None)
    for 칸, 적힘 in (({"line_errors": 1}, False), ({"dropped": "감시선 계산 오류(KeyError)"}, False),
                    ({"dropped": "기업행위 일정 묵음(2026-10-01)"}, True),
                    ({"dropped": "기업행위 일정 읽기 실패(RuntimeError)"}, False)):  # fmt: skip
        db.set_setting(c, ig.GUARD_KEY, {"KR": 값["KR"] | 칸})
        assert ig.lines_saved(c, date(2026, 10, 16), "B") is 적힘
    # 다시 적는 길에서 매도 판정이 깨지면 첫 실행이 적은 칸(일부 선·line_errors)을 덮지 않는다 — 빈 칸으로 덮으면 일부만
    # 돌던 장중 감시가 통째로 꺼졌다(25.1781·25.1785)
    부분 = 값["KR"] | {"line_errors": 1}
    db.set_setting(c, ig.GUARD_KEY, {"KR": 부분})

    원래판정 = jp.sell_decisions

    def 판정_깨짐(*a: Any, **k: Any) -> Any:
        if len(a) > 10 and a[9] is None and a[10] is not None:  # 다시 적는 길(결정 메모 없이 감시선만)
            raise RuntimeError("시세 읽기 실패")
        return 원래판정(*a, **k)

    with monkeypatch.context() as m:
        m.setattr(jp, "sell_decisions", 판정_깨짐)
        _, 넷째 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert 넷째["accounts"]["B"]["guard_lines"] == {"error": "RuntimeError"}
    assert db.get_setting(c, ig.GUARD_KEY, None)["KR"] == 부분
