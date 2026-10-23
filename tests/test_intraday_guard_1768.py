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
    # 선 바로 위·현재가 없음·팔 수량 0 은 보지 않는다
    assert ig.hits([_h("A", 93.01), _h("A", None), _h("A", 50.0, sellable=0), _h("C", 109.99)], 선) == []


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
    assert 선["trail"] == pytest.approx(115.0 * 0.93) and "target" not in 선
    assert "trail" not in (jp.guard_line(_C(109.0), h, 행, "trend", None, 오늘, 종목) or {})  # 목표 전
    # 목표에서 파는 규칙(B core)은 목표가 익절선, 손절을 안 따르는 D(time)는 선이 없다
    assert jp.guard_line(_C(None), h, 행, "core", None, 오늘, 종목)["target"] == pytest.approx(110.0)
    assert jp.guard_line(_C(None), h, 행, "all", None, 오늘, 종목)["target"] == pytest.approx(110.0)
    assert jp.guard_line(_C(None), h, 행, "time", None, 오늘, 종목) is None
    # 권리락 계수는 원가에 곱한다(평가 수익률 식과 같다), 설정 기간 손절폭을 쓴다, 기업행위 직후는 모름
    assert jp.guard_line(_C(None), *_보유(scale=0.9), "core", None, 오늘, 종목)["stop"] == pytest.approx(83.7)
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


def test_안_도는_날(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _Sess([_h("005930", 9_250.0)])
    # 감시선이 오늘 것이 아니면(아침 단계 실패) 꺼진다
    assert "off" in ig.run(c, datetime(2026, 10, 27, 1, 30, tzinfo=UTC), session=s)
    # 모의 매매 멈춤이면 내지 않는다
    db.set_setting(c, "paper_halt", {"on": True})
    assert ig.run(c, 월, session=s) == {"off": "모의 매매 멈춤(/paper)"}
    db.set_setting(c, "paper_halt", {"on": False})
    # 일일 배치가 돌고 있으면 쉰다(KIS 초당 한도)
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-26', 'cron', '2026-10-26T01:10:00+00:00', 'running')")
    assert "skipped" in ig.run(c, 월, session=s)
    # 확인 실행은 세기만
    c.conn.execute("UPDATE batch_runs SET status = 'success'")
    assert ig.run(c, 월, session=s, dry_run=True)["hits"] == {"stop": 1} and s.orders == []
    monkeypatch.delenv("KIS_VTS_APP_KEY")
    assert ig.run(c, 월, session=s) == {"off": "KIS모의투자 키 없음"}


def test_비교와_잔고_대보기에서_장중_매도는_따로() -> None:
    줄 = [{"reason": bm.REASON_GUARD_STOP, "status": "filled", "side": "sell"}]
    비교 = bm.compare(줄)
    assert 비교["n"] == 0 and 비교["guard"] == 1
    assert any("장중 손절·익절 1" in x for x in bm.compare_lines(비교, None, None))


def test_장중에_판_종목은_H_가_다시_사기_전까지_어긋남이_아니다(c: Client) -> None:
    def 체결(day: str, oid: int) -> None:
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
            " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
            " VALUES (?, 'H', 'KR', 1, 'buy', 'limit', 100, 1, 'KRW', ?, ?, 'r', 1, 1, 't')", [oid, day, day])
        c.conn.execute(
            "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
            " fx_rate, fee, tax, slippage, cash_krw, created_at) VALUES (?, 'H', 1, 'buy', ?, 100, 1, 'KRW', 1, 0, 0,"
            " 0, -100, 't')", [oid, day])  # fmt: skip
    체결("2026-10-20", 900)
    ig.run(c, 월, session=_Sess([_h("005930", 9_250.0)]), clock=lambda: 월)
    인자 = [bmj.BROKER, bm.REASON_GUARD_STOP, bm.REASON_GUARD_TAKE, "H"]
    assert c.q(bmj.GUARD_SOLD_SQL, 인자) == [(1,)]
    체결("2026-10-27", 901)  # H 가 장중 매도 뒤 다시 샀다 — KIS 도 베껴 샀으니 다시 대 본다
    assert c.q(bmj.GUARD_SOLD_SQL, 인자) == []


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
