"""거장 단독 규칙 계좌 Z3(그린블랫 magic_formula_v1)·Z4(오닐 canslim_v1) (docs/company.md 12.5, docs/paper.md 18장, 25.2319)."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from typing import Any

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.jobs import paper_solo
from batch.services import company as co
from batch.services import desk, report_picks, solo, trend
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client, _신호, 설정

# ── 순수 판정 ──────────────────────────────────────────────────────────────────────────────


def _d(sid: int, *, oi: float = 100.0, cap: float = 1000.0, ni: tuple[float, ...] = (10, 14, 20, 30),
       eq: float = 100.0, high52: float = 0.9, mom: float = 0.5, cur: str = "KRW", fin: bool = False) -> co.Dossier:  # fmt: skip
    """거장 자료 한 벌 — 사업연도 2022~2025, 끝 해에 영업이익·자본 근사 칸"""
    연간 = [{"fy": 2022 + i, "ni": n, "eq": eq, "ta": 1000.0, "cl": 100.0, "ncl": 100.0, "oi": oi, "cur": cur}
          for i, n in enumerate(ni)]  # fmt: skip
    return co.Dossier(stock_id=sid, ticker=str(sid), as_of="2026-10-30", masters={
        "annual": 연간, "raw": {"cap": cap, "high52": high52, "mom12_1": mom}, "financial": fin, "utility": False,
        "price_currency": "KRW"})  # fmt: skip


def test_매월_첫_거래일() -> None:
    assert solo.is_month_first_session(date(2026, 11, 2), date(2026, 10, 30))
    assert not solo.is_month_first_session(date(2026, 11, 3), date(2026, 11, 2))
    assert solo.is_month_first_session(date(2027, 1, 4), date(2026, 12, 30))  # 해가 바뀌어도


def test_마법공식_순위는_EY_ROC_백분위_합_순이고_못_재는_종목은_빠진다() -> None:
    # 1: EY·ROC 모두 가장 높음 · 2: 가운데 · 3: 가장 낮음 · 4: 금융(빠짐) · 5: 통화 다름(빠짐) · 6: 영업이익 ≤ 0(빠짐)
    줄 = solo.magic_ranks([_d(2, oi=60), _d(1, oi=90), _d(3, oi=30), _d(4, fin=True), _d(5, cur="USD"), _d(6, oi=-5)])
    assert [r.stock_id for r in 줄] == [1, 2, 3]
    assert [r.pct for r in 줄] == [100.0, 50.0, 0.0]
    assert 줄[0].detail == {"ey": 90 / 1100, "roc": 90 / 900}


def test_오닐_후보는_두_부서_모두_찬성만_모멘텀_순() -> None:
    # 1·2 실적(3년 연속 증가·CAGR ≥ 25%·ROE ≥ 17%)·주가(52주 ≥ 0.85·12-1 백분위 ≥ 80) 모두 찬성 — 모집단이 커야 80 을 넘는다
    좋음 = [_d(1, mom=0.9), _d(2, mom=0.95)]
    나머지 = [_d(10 + i, mom=0.1 * i, ni=(10, 9, 8, 7)) for i in range(8)]  # 실적 반대
    낮은주가 = [_d(30, mom=0.99, high52=0.5)]  # 52주 고점 근접 미달 — 주가 부서 중립
    줄 = solo.canslim_ranks(좋음 + 나머지 + 낮은주가)
    assert [r.stock_id for r in 줄] == [2, 1]
    assert 줄[0].pct > 줄[1].pct >= 80


def test_국면은_강세만() -> None:
    assert solo.canslim_market_ok("bull")
    assert not solo.canslim_market_ok("bear")
    assert not solo.canslim_market_ok("unknown")
    assert not solo.canslim_market_ok(None)


def test_고르기는_든_것_뺄_것을_건너뛰고_자리만큼() -> None:
    줄 = [solo.Ranked(i, 100 - i, {}) for i in range(1, 8)]
    assert [r.stock_id for r in solo.picks(줄, held={1}, excluded={2}, max_hold=12, per_day=1)] == [3]
    assert [r.stock_id for r in solo.picks(줄, held={1, 3}, excluded=set(), max_hold=5, per_day=None)] == [2, 4, 5]
    assert solo.picks(줄, held={1, 2, 3, 4, 5}, excluded=set(), max_hold=5, per_day=None) == []


def test_1년_보유와_오닐_매도선() -> None:
    assert not solo.hold_1y_due("2025-11-02", date(2026, 11, 1))
    assert solo.hold_1y_due("2025-11-02", date(2026, 11, 2))
    assert solo.hold_1y_due("2025-11-02T00:00:00", date(2026, 12, 1))
    assert solo.canslim_exit(100.0, 92.0) == "stop"  # −8% 이하(같으면 넘음)
    assert solo.canslim_exit(100.0, 92.01) is None
    assert solo.canslim_exit(100.0, 125.0) == "target"  # +25% 이상
    assert solo.canslim_exit(100.0, 124.99) is None
    assert solo.canslim_exit(0.0, 10.0) is None


def test_사전_등록_값() -> None:
    """company.md 12.5 의 수 — 바꾸면 판을 올린다(12.8)"""
    assert (solo.Z3_SLICES, solo.Z3_MAX_HOLD, solo.HOLD_DAYS) == (24, 12, 365)
    assert (solo.Z4_MAX_HOLD, solo.Z4_WEIGHT_PCT, solo.CANSLIM_STOP, solo.CANSLIM_TARGET) == (5, 10.0, -0.08, 0.25)
    assert pp.SELL_RULES[solo.HOLD_1Y] == frozenset({solo.HOLD_CODE})
    assert pp.SELL_RULES[solo.CANSLIM_EXIT] == frozenset(solo.CANSLIM_CODES.values())
    assert {solo.MAGIC, solo.CANSLIM} <= pp.SELECTIONS
    # 까닭 코드가 '손절' 이면 손절 보유의 남은 회차 거둠 길을 탄다 — 이름을 가른다
    assert "손절" not in {solo.HOLD_CODE, *solo.CANSLIM_CODES.values()}
    assert paper_solo.LAST_CLOSE_SQL == job.LAST_CLOSE_SQL
    # 문서(사전 등록 12.5 — 고치지 않는 문장)가 같은 값을 적는다
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "docs" / "company.md").read_text(encoding="utf-8")
    절 = 글[글.index("### 12.5 "):글.index("### 12.6 ")]
    for 조각 in ("평가총액 1/24", "시장당 보유 12 상한", "365 달력일", "시장당 보유 5 상한·종목 10%", "−8% 이하 손절", "+25% 이상"):
        assert 조각 in 절.replace("\n  ", " "), 조각


def test_마이그레이션_행은_엔진_값과_같다() -> None:
    c = Client(accounts=None)
    행 = {r[0]: r[1:] for r in c.q("SELECT id, sell_rule, entry_rule, top_n, selection, rule_version, created_at,"
                                    " charter_json FROM paper_accounts WHERE id IN ('Z3', 'Z4')")}  # fmt: skip
    assert 행 == {"Z3": ("hold_1y", "open", solo.Z3_MAX_HOLD, "magic_formula", 1, "2026-11-26T00:00:00+00:00", None),
                 "Z4": ("canslim_exit", "open", solo.Z4_MAX_HOLD, "canslim", 1, "2026-11-26T00:00:00+00:00", None)}
    assert {"Z3", "Z4"} <= set(pp.SEPARATE_IDS) and pp.SOLO_IDS == ("Z3", "Z4")
    assert not {"Z3", "Z4"} & set(pp.MASTER_IDS)  # core 손절선을 재지 않는다
    assert {("Z3", 1), ("Z4", 1)} <= set(desk.PREREG)
    assert (pp.account_name("Z3"), pp.account_name("Z4")) == ("그린블랫", "오닐")
    assert "365일 뒤" in desk._손절_해당없음("Z3", 2)[0] and "−8% 손절·+25% 익절" in desk._손절_해당없음("Z4", 2)[0]


# ── 엔진 한 바퀴 ─────────────────────────────────────────────────────────────────────────────


def _시세(c: Client, sid: int, day: str, open_: float, close: float) -> None:
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')", [sid, day, open_, max(open_, close), min(open_, close), close],
    )  # fmt: skip


def _새(monkeypatch: pytest.MonkeyPatch, accounts: tuple[str, ...], days: tuple[str, ...]) -> Client:
    c = Client(accounts=accounts)
    c.conn.execute("UPDATE paper_accounts SET created_at = '2026-10-14T00:00:00+00:00' WHERE id IN ('Z3', 'Z4')")
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    for day in days:
        for sid in (1, 3):
            _시세(c, sid, day, 10_000, 10_000)
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    # 유니버스 자료 — 3 이 마법공식·오닐 모두 1 보다 앞(1 은 이익이 줄어 오닐 실적 반대)
    자료 = [_d(1, oi=40, mom=0.2, ni=(10, 9, 8, 7)), _d(3, oi=90, mom=0.9), *[_d(10 + i, oi=10, mom=0.01 * i, ni=(9, 8, 7, 6))
                                                       for i in range(8)]]  # fmt: skip
    monkeypatch.setattr(paper_solo, "universe_dossiers", lambda *_a, **_k: 자료)
    return c


def _국면(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    monkeypatch.setattr(trend, "regimes_for_country", lambda *_a, **_k: {"KOSPI": trend.Regime("KOSPI", state)})


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, *, signals: bool = True) -> tuple[list[str], dict]:
    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    return job.run_step(
        c, market="KR", trade_date=trade_date, late=False, settings=설정,
        signal_rows=[dataclasses.replace(_신호(), as_of_date=trade_date, close_date=trade_date)] if signals else [],
        price_series={}, series_unknown={}, allocate=signals, now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip


def _주문(c: Client, aid: str) -> list[tuple]:
    return c.q("SELECT stock_id, side, order_type, quantity, fill_session, fill_basis, reason FROM paper_orders"
               " WHERE account_id = ? ORDER BY id", [aid])  # fmt: skip


def test_Z3_는_그_달_첫_거래일_장에만_1종목을_평가_총액_24분의_1로_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _새(monkeypatch, ("B", "Z3"), ("2026-10-29", "2026-10-30"))
    _국면(monkeypatch, "bull")
    _줄, 기록 = _하루(c, monkeypatch, "2026-10-29")  # 다음 장 10-30 — 달 첫 장이 아님
    assert _주문(c, "Z3") == []
    assert 기록["accounts"]["Z3"]["buy"]["skipped"] == "그 달 첫 거래일 장이 아님"
    _줄, 기록 = _하루(c, monkeypatch, "2026-10-30")  # 다음 장 11-02 — 11월 첫 거래일
    # 100만 ÷ 24 = 41,666 → 기준가 10,000 → 4주, 다음 장 시가 시장가
    assert _주문(c, "Z3") == [(3, "buy", "market", 4, "2026-11-02", "open", "magic_formula_v1: 100 백분위 · 비중 4.2%")]
    b = 기록["accounts"]["Z3"]["buy"]
    assert b["picks"] == [3] and b["held"] == 0 and b["orders"] == 1
    근거 = json.loads(c.q("SELECT rationale_json FROM paper_orders WHERE account_id = 'Z3'")[0][0])
    assert 근거["rule"] == "magic_formula_v1" and 근거["signal"]["criteria"][0]["label"] == "마법공식 순위"
    결정 = c.q("SELECT stock_id, kind FROM paper_decisions WHERE account_id = 'Z3'")
    assert 결정 == [(3, "buy")]


def test_Z4_는_강세일_때만_두_부서_찬성_종목을_10퍼센트로_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    for 상태, 산다 in (("bear", False), ("unknown", False), ("bull", True)):
        c = _새(monkeypatch, ("B", "Z4"), ("2026-10-14",))
        _국면(monkeypatch, 상태)
        _줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
        if 산다:
            # 100만 × 10% = 10만 → 10주 · 후보는 3 하나(1 은 12-1 백분위 미달)
            assert _주문(c, "Z4") == [(3, "buy", "market", 10, "2026-10-15", "open",
                                     "canslim_v1: 100 백분위 · 비중 10.0%")]  # fmt: skip
        else:
            assert _주문(c, "Z4") == [], 상태
            assert "강세일 때만" in 기록["accounts"]["Z4"]["buy"]["skipped"], 상태


def test_신호가_묵은_날은_단독_계좌도_사지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _새(monkeypatch, ("Z4",), ("2026-10-14",))
    _국면(monkeypatch, "bull")
    _줄, 기록 = _하루(c, monkeypatch, "2026-10-14", signals=False)
    assert _주문(c, "Z4") == [] and "배분하지 않는 날" in 기록["accounts"]["Z4"]["buy"]["skipped"]


def test_Z4_는_첫_체결가_대비_종가_마이너스_8퍼센트면_다음_장_시가에_판다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _새(monkeypatch, ("Z4",), ("2026-10-14",))
    _국면(monkeypatch, "bull")
    본보유: list[set[int]] = []
    원래 = paper_solo.plan
    monkeypatch.setattr(paper_solo, "plan", lambda *a, **k: (본보유.append(set(k["held"])), 원래(*a, **k))[1])
    _하루(c, monkeypatch, "2026-10-14")  # 10-15 시가 10,000 에 3 을 10주
    _시세(c, 3, "2026-10-15", 10_000, 9_300)  # −7% — 아직
    _시세(c, 1, "2026-10-15", 10_000, 10_000)
    _하루(c, monkeypatch, "2026-10-15")
    assert [o for o in _주문(c, "Z4") if o[1] == "sell"] == []
    _시세(c, 3, "2026-10-16", 9_300, 9_200)  # −8% — 넘음(같으면 넘음)
    _시세(c, 1, "2026-10-16", 10_000, 10_000)
    _하루(c, monkeypatch, "2026-10-16")
    assert [o for o in _주문(c, "Z4") if o[1] == "sell"] == [(3, "sell", "market", 10, "2026-10-19", "open", "오닐손절")]
    # 든 종목은 다시 사지 않는다 — 날마다 고르지만 매수는 첫날 한 줄(보유 상한·든 종목 빼기)
    assert [o[0] for o in _주문(c, "Z4") if o[1] == "buy"] == [3]
    # 보유 상한에 세는 것 — 첫날 없음, 찬 뒤 {3}, 파는 날은 같은 장에 나가 뺀다
    assert 본보유 == [set(), {3}, set()]
    결정 = c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'Z4' AND stock_id = 3 ORDER BY id")
    assert 결정[-1] == ("sell", "오닐손절")


def test_단독넘음_1년_보유와_익절(monkeypatch: pytest.MonkeyPatch) -> None:
    class _C:
        def execute(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("미국 보유는 권리락을 읽지 않는다")

    행 = {"stock_id": 2, "country": "US", "currency": "USD", "lots": [("2025-11-02", 4.0, 100.0)],
         "market_value": 4 * 125.0, "quantity": 4.0, "price_date": "2026-11-01"}  # fmt: skip
    assert job._단독넘음(_C(), 행, date(2026, 11, 1), solo.HOLD_1Y) is None
    팖 = job._단독넘음(_C(), 행, date(2026, 11, 2), solo.HOLD_1Y)
    assert 팖["reason_code"] == "보유1년" and "365일" in 팖["rationale"]
    팖 = job._단독넘음(_C(), 행, date(2026, 11, 2), solo.CANSLIM_EXIT)
    assert 팖["reason_code"] == "오닐익절" and "+25%" in 팖["rationale"]
    assert [x["label"] for x in 팖["criteria"]] == ["첫 체결", "판정 종가"]
    assert job._단독넘음(_C(), {**행, "market_value": 4 * 110.0}, date(2026, 11, 2), solo.CANSLIM_EXIT) is None


def test_단독_계좌가_열려도_다른_계좌의_행은_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """Z3·Z4 는 id 순으로 Y 뒤·ZL 앞에 돈다 — 앞뒤 계좌의 주문·결정이 그대로(12.8 무변화)"""
    def _앞뒤(c: Client) -> tuple[list, list]:
        return (c.q("SELECT account_id, stock_id, side, order_type, limit_price, quantity, tranche, reason FROM paper_orders"
                    " WHERE account_id NOT IN ('Z3', 'Z4') ORDER BY account_id, tranche, stock_id"),
                c.q("SELECT account_id, stock_id, kind, reason FROM paper_decisions WHERE account_id NOT IN ('Z3', 'Z4')"
                    " ORDER BY account_id, stock_id, kind"))  # fmt: skip

    없음 = _새(monkeypatch, ("A", "B", "R1", "ZL"), ("2026-10-29", "2026-10-30"))
    있음 = _새(monkeypatch, ("A", "B", "R1", "Z3", "Z4", "ZL"), ("2026-10-29", "2026-10-30"))
    for c in (없음, 있음):  # ZL 도 그날 열린 것으로 — 위원회를 실제로 연다(뒤 계좌)
        c.conn.execute("UPDATE paper_accounts SET created_at = '2026-10-14T00:00:00+00:00' WHERE id = 'ZL'")
        c.conn.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                       " VALUES ('2026-10-10', 1, 1, 'KRW', 't'), ('2026-10-10', 3, 1, 'KRW', 't')")  # fmt: skip
    _국면(monkeypatch, "bull")
    _하루(없음, monkeypatch, "2026-10-30")
    _하루(있음, monkeypatch, "2026-10-30")
    assert _앞뒤(있음) == _앞뒤(없음)
    assert _앞뒤(없음)[0]  # 비교가 공짜가 아니다
    assert {r[0] for r in 있음.q("SELECT account_id FROM paper_orders")} >= {"Z3", "Z4"}  # 단독 계좌도 실제로 샀다


def test_유니버스_읽기는_배당_베타를_읽지_않는다() -> None:
    """`master_facts(light=True)` — 그린블랫·오닐은 배당·베타를 쓰지 않는다. 베타는 기준일까지의 모든 행이라 유니버스 단위로
    무겁다(25.2319). 위원회 길(기본)은 그대로 다 읽는다"""
    from batch.jobs import company as cjob

    class _R:
        def dicts(self) -> list:
            return []

    class _C:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql: str, *_a: Any) -> _R:
            self.sql.append(sql)
            return _R()

    가벼움, 전부 = _C(), _C()
    cjob.master_facts(가벼움, [1, 2], "US", "2026-10-30", light=True)
    cjob.master_facts(전부, [1, 2], "US", "2026-10-30")
    assert cjob.MASTER_DIV_SQL not in 가벼움.sql and cjob.MASTER_BETA_SQL not in 가벼움.sql
    assert cjob.MASTER_SNAP_SQL in 가벼움.sql and cjob.MASTER_FACTOR_SQL in 가벼움.sql
    assert {cjob.MASTER_DIV_SQL, cjob.MASTER_BETA_SQL} <= set(전부.sql)


# ── 25.2320 (25.2319 교차검증 [필수] 2·[권고]) ────────────────────────────────────────────────


def test_기업행위_경계_종목은_건너뛰고_다음_순위를_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    """[필수] 1 — 관문의 `frozen` 은 관심 집합(보유·열린 주문·신호)만 봐 유니버스에서 새로 고른 종목을 못 막았다. 그래서
    원장에 buy 를 남기고 다음 날 주문만 '멈춤' 으로 닫히길 되풀이했다(25.1480 과 같은 꼴). 후보마다 보고 다음 순위로"""
    c = _새(monkeypatch, ("Z4",), ("2026-10-14",))
    _국면(monkeypatch, "bull")
    # 1 도 오닐 두 부서 찬성이 되게 — 이익이 늘고 12-1 이 3 다음
    자료 = [_d(1, oi=40, mom=0.8), _d(3, oi=90, mom=0.9), *[_d(10 + i, oi=10, mom=0.01 * i, ni=(9, 8, 7, 6))
                                                       for i in range(8)]]  # fmt: skip
    monkeypatch.setattr(paper_solo, "universe_dossiers", lambda *_a, **_k: 자료)
    본: list[set[int]] = []
    monkeypatch.setattr(job, "frozen_ids", lambda _c, ids, _m: (본.append(set(ids)), {3} & set(ids))[1])
    _줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert [o[0] for o in _주문(c, "Z4")] == [1]  # 3 은 경계 — 다음 순위 1
    assert {3} in 본  # 후보마다 물었다(관심 집합에는 3 이 없다)
    assert 기록["accounts"]["Z4"]["buy"]["not_bought"] == {3: "기업행위 경계(분할·병합 의심) — 다음 순위로"}
    assert c.q("SELECT stock_id, kind FROM paper_decisions WHERE account_id = 'Z4'") == [(1, "buy")]


def _가치(equity: float, cash: float) -> pp.Valuation:
    return pp.Valuation(invested_krw=equity, cash_krw=cash, holdings_krw=equity - cash, equity_krw=equity, pnl_krw=0.0,
                        pnl_pct=None)


class _시세판:
    def __init__(self, close: float, day: str) -> None:
        self.close, self.day = close, day

    def execute(self, _sql: str, _args: list) -> Any:
        class _R:
            rows = [(self.close, self.day)]

        return _R()


def test_현금은_예약과_같은_잣대로_쓴다_두_시장이_겹쳐도_체결_점검을_넘는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """[필수] 2 — 쓸 현금을 기준가 × 수수료 여유로만 세면, 같은 계좌의 다른 열린 매수 예약(기준가 × 1.10 × 1.01)과 겹쳐
    체결 때 '현금 부족' 으로 취소됐다. 크기는 평가 총액 × 비중(기준가), 현금은 예약 단가로"""
    오늘 = date(2026, 11, 2)
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 오늘)
    후보 = [solo.Ranked(i, 100 - i, {"rs": 100 - i}) for i in range(1, 9)]
    # 국내 다섯 종목(1,000원) — 평가 총액 1억 × 10% = 1만 주씩, 현금은 예약 단가 1,111원으로 센다
    주문, _ = paper_solo.size_orders(
        _시세판(1_000.0, "2026-10-30"), solo.CANSLIM, 후보, 5, market="KR", valuation=_가치(1e8, 1e8), open_buys=[],
        fx_rate=1_400.0, as_of="2026-10-30", n_ranked=8, is_frozen=lambda _s: False,
    )  # fmt: skip
    assert [o["quantity"] for o in 주문] == [10_000] * 5
    열린 = [pp.Order(id=i, account_id="Z4", stock_id=o["stock_id"], side="buy", order_type="market", limit_price=None,
                     quantity=o["quantity"], currency="KRW", fill_session="2026-11-02", ref_price=o["ref_price"])
            for i, o in enumerate(주문, start=1)]  # fmt: skip
    for o, 열 in zip(주문, 열린, strict=True):
        assert abs(o["cash_krw"] - pp.reserved_krw([열], {"USD": 1_400.0})) < 1  # 예약과 같은 값(25.1458)
    # 같은 날 미국 — 국내 예약을 뺀 현금 안에서만
    미국, _ = paper_solo.size_orders(
        _시세판(10.0, "2026-10-30"), solo.CANSLIM, 후보, 5, market="US", valuation=_가치(1e8, 1e8), open_buys=열린,
        fx_rate=1_400.0, as_of="2026-10-30", n_ranked=8, is_frozen=lambda _s: False,
    )  # fmt: skip
    남은 = 1e8 - pp.reserved_krw(열린, {"USD": 1_400.0})
    assert sum(o["cash_krw"] for o in 미국) <= 남은 + 1
    # 다른 예약을 다 빼도 국내 첫 주문의 체결 점검(기준가 그대로 시가)이 음수로 가지 않는다
    미국열린 = [pp.Order(id=100 + i, account_id="Z4", stock_id=o["stock_id"], side="buy", order_type="market",
                       limit_price=None, quantity=o["quantity"], currency="USD", fill_session="2026-11-02",
                       ref_price=o["ref_price"]) for i, o in enumerate(미국)]  # fmt: skip
    다른 = pp.reserved_krw(열린[1:] + 미국열린, {"USD": 1_400.0})
    assert 1e8 - 1_000 * 10_000 * (1 + pp.FEE_BUFFER) - 다른 >= 0
    # 현금이 모자라 1주도 못 사면 멈춘다 — 더 싼 다음 순위로 바꿔 사지 않는다
    멈춤, 기록 = paper_solo.size_orders(
        _시세판(1_000.0, "2026-10-30"), solo.CANSLIM, 후보, 5, market="KR", valuation=_가치(1e8, 500.0), open_buys=[],
        fx_rate=1_400.0, as_of="2026-10-30", n_ranked=8, is_frozen=lambda _s: False,
    )  # fmt: skip
    assert 멈춤 == [] and 기록["not_bought"] == {1: "현금 부족 — 멈춤"}


def test_비중으로_1주에_못_미치거나_묵은_종가면_다음_순위로(monkeypatch: pytest.MonkeyPatch) -> None:
    오늘 = date(2026, 11, 2)
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 오늘)
    후보 = [solo.Ranked(i, 100 - i, {"ey": 0.1, "roc": 0.2}) for i in (1, 2, 3)]

    class _판:
        def execute(self, _sql: str, args: list) -> Any:
            class _R:
                rows = {1: [(5_000_000.0, "2026-10-30")], 2: [(1_000.0, "2026-09-01")],
                        3: [(1_000.0, "2026-10-30")]}[args[0]]  # fmt: skip

            return _R()

    # Z3 — 100만 ÷ 24 = 41,666원: 1 은 500만 원짜리(1주에 못 미침), 2 는 묵은 종가, 3 을 41주
    주문, 기록 = paper_solo.size_orders(
        _판(), solo.MAGIC, 후보, 1, market="KR", valuation=_가치(1e6, 1e6), open_buys=[], fx_rate=None,
        as_of="2026-10-30", n_ranked=3, is_frozen=lambda _s: False,
    )  # fmt: skip
    assert [(o["stock_id"], o["quantity"]) for o in 주문] == [(3, 41)]
    assert 기록["not_bought"] == {1: "비중으로 1주에 못 미침 — 다음 순위로", 2: "묵은 종가(2026-09-01) — 다음 순위로"}


def test_Z3_는_1년_된_보유를_달_첫_장에_팔고_같은_장에_새로_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    """[권고] — hold_1y 가 엔진까지 이어지는지(갈래를 빼면 Z3 은 영원히 팔지 않는다). 보유 날수를 31일로 줄여 한 달 안에
    돌린다 — 10-01 체결 + 31 = 11-01. 결정일 10-30 엔 팔지 않고, 결정일 11-02(달 첫 장 아침)엔 팔고 같은 장에 새로 산다"""
    c = _새(monkeypatch, ("Z3",), ("2026-09-30", "2026-10-01", "2026-10-29", "2026-10-30"))
    # 9월에 열고 입금한 계좌로(개설일 막이·현금)
    c.conn.execute("UPDATE paper_accounts SET created_at = '2026-09-01T00:00:00+00:00' WHERE id = 'Z3'")
    c.conn.execute("UPDATE paper_cash_flows SET flow_date = '2026-09-01', created_at = '2026-09-01T00:00:00+00:00'")
    monkeypatch.setattr(solo, "HOLD_DAYS", 31)
    _하루(c, monkeypatch, "2026-09-30")  # 다음 장 10-01 — 10월 첫 거래일: 3 을 산다
    _하루(c, monkeypatch, "2026-10-01")  # 10-01 시가 체결
    assert c.q("SELECT stock_id, fill_date FROM paper_fills WHERE account_id = 'Z3'") == [(3, "2026-10-01")]
    _하루(c, monkeypatch, "2026-10-29")  # 결정일 10-30 — 아직(10-30 < 11-01)
    assert [o for o in _주문(c, "Z3") if o[1] == "sell"] == []
    _하루(c, monkeypatch, "2026-10-30")  # 결정일 11-02 ≥ 11-01 — 팔고, 같은 장(11 월 첫 장)에 새로 산다
    assert [o[:2] + o[4:] for o in _주문(c, "Z3") if o[1] == "sell"] == [(3, "sell", "2026-11-02", "open", "보유1년")]
    assert [o[0] for o in _주문(c, "Z3") if o[1] == "buy"] == [3, 1]  # 3 은 그날 파는 종목이라 빼고 다음 순위 1


def test_국내_권리락은_되돌려_견준다(monkeypatch: pytest.MonkeyPatch) -> None:
    """[권고] — 원시 종가는 −8% 아래지만 권리락 계수(0.9)로 되돌리면 위 — 팔지 않는다(25.1999 와 같은 꼴)"""
    from batch.jobs import monitor_targets as mt

    monkeypatch.setattr(mt, "rights_factors_since", lambda *_a, **_k: [("2026-10-20", 0.9)])
    행 = {"stock_id": 1, "country": "KR", "currency": "KRW", "lots": [("2026-10-15", 10.0, 10_000.0)],
         "market_value": 10 * 9_000.0, "quantity": 10.0, "price_date": "2026-10-21"}  # fmt: skip
    assert job._단독넘음(object(), 행, date(2026, 10, 22), solo.CANSLIM_EXIT) is None  # 9,000 ÷ 0.9 = 10,000
    팖 = job._단독넘음(object(), {**행, "market_value": 10 * 8_200.0}, date(2026, 10, 22), solo.CANSLIM_EXIT)
    assert 팖["reason_code"] == "오닐손절" and "권리락 되돌림 ×0.9" in 팖["criteria"][1]["display"]


def test_국면_지수_열쇠는_추세_모듈과_같다() -> None:
    for 시장, 열쇠 in solo.M_INDEX.items():
        assert 열쇠 in trend.COUNTRY_INDEXES[시장], 시장


def test_보유_상한은_시장마다_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    """[권고] — 다른 시장 보유는 이 시장 상한에 들지 않는다(미국 2 를 들고도 국내 단계의 held 는 빈 집합)"""
    c = _새(monkeypatch, ("Z4",), ("2026-10-14",))
    _국면(monkeypatch, "bull")
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, fill_basis, tranche, reason, status, rule_version, created_at,"
                   " ref_price) VALUES (901, 'Z4', 'US', 2, 'buy', 'market', NULL, 1, 'USD', '2026-10-13',"
                   " '2026-10-16', 'open', 0, 'canslim_v1', 'open', 1, '2026-10-13T00:00:00+00:00', 10)")  # fmt: skip
    본: list[set[int]] = []
    원래 = paper_solo.plan
    monkeypatch.setattr(paper_solo, "plan", lambda *a, **k: (본.append(set(k["held"])), 원래(*a, **k))[1])
    _하루(c, monkeypatch, "2026-10-14")
    assert 본 and 본[0] == set()


def test_다시_사기_쉼_안에는_같은_종목을_다시_사지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """[권고] — 판 뒤 5거래일 안이면 다시 1순위여도 사지 않는다(엔진 연결)"""
    c = _새(monkeypatch, ("Z4",), ("2026-10-14",))
    _국면(monkeypatch, "bull")
    본: list[set[int]] = []
    원래 = paper_solo.plan
    monkeypatch.setattr(paper_solo, "plan", lambda *a, **k: (본.append(set(k["excluded"])), 원래(*a, **k))[1])
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, 3, "2026-10-15", 10_000, 9_000)  # 체결 뒤 −10% → 판다
    _시세(c, 1, "2026-10-15", 10_000, 10_000)
    _하루(c, monkeypatch, "2026-10-15")
    _시세(c, 3, "2026-10-16", 9_000, 9_000)
    _시세(c, 1, "2026-10-16", 10_000, 10_000)
    _하루(c, monkeypatch, "2026-10-16")  # 10-19 에 팔림(열린 매도) — 쉼·열린 매도로 3 을 빼야 한다
    _시세(c, 3, "2026-10-19", 9_000, 9_000)
    _시세(c, 1, "2026-10-19", 10_000, 10_000)
    _하루(c, monkeypatch, "2026-10-19")  # 10-19 체결 — 판 뒤 쉼 안
    assert 3 in 본[-1]
    assert [o[0] for o in _주문(c, "Z4") if o[1] == "buy"] == [3]


def test_월간_복기의_규칙_점검은_단독_계좌_규칙으로_적는다() -> None:
    """[권고] — core 잣대로 세면 Z3 의 손절 없는 보유를 '손절선 아래로 들고 간 날' 로, Z4 를 평균단가 −25% 로 적었다"""
    행 = [{"as_of": "2026-11-16", "invested_krw": 1_000, "equity_krw": 1_000, "twr_index": 1.0,
           "positions_json": '[{"stock_id": 10, "value_krw": 120, "horizon": "long", "cost": 200, "market_value": 120}]'}]
    줄 = pp.summary_lines({"Z3": 행, "Z4": 행}, {}, "2026-11-12", "2026-11-01", {}, {},
                          {"max_stock_pct": 10.0, "stop_pct": {"long": -25.0}}, {"Z3": "hold_1y", "Z4": "canslim_exit"})
    z3 = next(x for x in 줄 if x.startswith("  그린블랫 규칙 점검"))
    z4 = next(x for x in 줄 if x.startswith("  오닐 규칙 점검"))
    assert "손절선 아래로 들고 간 날" not in z3 and "손절·목표 없이 첫 체결 365일 뒤" in z3
    assert "손절선 아래로 들고 간 날" not in z4 and "−8% 이하·+25% 이상" in z4
    assert "설계상(종목 10% 로 산다)" in z4  # 12% — 종목 상한 넘음은 설계상


def test_웹_규칙_글은_규칙_값과_같다() -> None:
    """웹 `ruleText`·`SELL_RULE_TEXT` 는 값을 손으로 적는다 — 규칙 정의처(`services/solo`)와 어긋나면 깨진다(25.1874, 25.2320)"""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "paper.ts").read_text(encoding="utf-8")
    아래 = f"{solo.CANSLIM_STOP * 100:g}".replace("-", "−")
    for 조각 in (f"평가 총액 1/{solo.Z3_SLICES}", f"시장당 {solo.Z3_MAX_HOLD}종목", f"첫 체결 {solo.HOLD_DAYS}일 뒤",
                 f"종목 {solo.Z4_WEIGHT_PCT:g}%", f"시장당 {solo.Z4_MAX_HOLD}종목",
                 f"{아래}% 이하·+{solo.CANSLIM_TARGET * 100:g}% 이상"):  # fmt: skip
        assert 조각 in 글, 조각


# ── 25.2321 (25.2320 교차검증 [필수] 1·[권고]) ────────────────────────────────────────────────


def test_새로_사는_종목에도_업종_상한이_걸리고_막힌_자리는_다음_순위로(monkeypatch: pytest.MonkeyPatch) -> None:
    """[필수] — 관문의 업종 칸에 후보 종목이 없으면 업종 30% 상한이 꺼졌다. 상한에 막힌 자리는 다음 순위(다른 업종)로"""
    c = _새(monkeypatch, ("Z4",), ("2026-10-14",))
    _국면(monkeypatch, "bull")
    for sid, 업종 in ((4, "전자"), (5, "전자"), (6, "전자"), (7, "화학"), (8, "전자")):
        c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source,"
                       " fetched_at, asset_type) VALUES (?, ?, 'M', 'KR', ?, ?, 'KRW', 'active', 't', 't', 'stock')",
                       [sid, f"00{sid}", f"종목{sid}", 업종])  # fmt: skip
        _시세(c, sid, "2026-10-14", 10_000, 10_000)
    # 12-1 순: 3 > 4 > 5 > 6 > 8 > 7 — 위 다섯이 모두 '전자'(1·3 도 전자), 7 만 '화학'
    좋음 = [_d(3, mom=0.99), _d(4, mom=0.98), _d(5, mom=0.97), _d(6, mom=0.96), _d(8, mom=0.95), _d(7, mom=0.94)]
    나쁨 = [_d(1, mom=0.1, ni=(10, 9, 8, 7)), *[_d(20 + i, mom=0.01 * i, ni=(9, 8, 7, 6)) for i in range(30)]]
    monkeypatch.setattr(paper_solo, "universe_dossiers", lambda *_a, **_k: 좋음 + 나쁨)
    _줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    # 전자 셋(30.3% — 상한 30% + 여유 1.5%p 안)까지, 넷째 전자부터 막히고 남은 두 자리 가운데 하나를 화학 7 로
    assert sorted(o[0] for o in _주문(c, "Z4")) == [3, 4, 5, 7]
    막힘 = c.q("SELECT stock_id, reason FROM paper_decisions WHERE account_id = 'Z4' AND kind = 'blocked' ORDER BY 1")
    assert [m[0] for m in 막힘] == [6, 8] and all("전자" in m[1] for m in 막힘)
    assert 기록["accounts"]["Z4"]["buy"]["gate_rounds"] == 2


def test_상한_밖의_막힘은_다음_순위로_넘기지_않는다() -> None:
    """멈춤 스위치·하루 손실·현금 같은 막힘은 다음 순위로 넘겨도 같이 막힌다 — 한 번에 멈춘다"""
    후보 = [solo.Ranked(i, 100 - i, {"rs": 1}) for i in range(1, 6)]
    불림: list[int] = []

    def _관문(줄들: list[dict]) -> tuple[list, list]:
        불림.append(len(줄들))
        from batch.services import risk_gate as rg

        cs = [rg.Candidate(o["stock_id"], "buy", o["cost_krw"], o["ref_date"], i, o["cash_krw"]) for i, o in enumerate(줄들)]
        return [], [(c, [("halted", "멈춤 스위치")]) for c in cs]

    import batch.jobs.paper_solo as ps

    class _판:
        def execute(self, *_a: Any) -> Any:
            class _R:
                rows = [(1_000.0, "2026-10-30")]

            return _R()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cal, "local_today", lambda _m=None: date(2026, 11, 2))
        주문, 통과, 막힘, _기록 = ps.orders_through_gate(
            _판(), solo.CANSLIM, 후보, 2, market="KR", valuation=_가치(1e8, 1e8), open_buys=[], fx_rate=None,
            as_of="2026-10-30", n_ranked=5, is_frozen=lambda _s: False, gate=_관문, account_id="Z4",
            fill_session="2026-11-02",
        )  # fmt: skip
    assert 불림 == [2] and 통과 == [] and len(막힘) == 2


def test_시세_없음도_다음_순위로_미국은_권리락을_읽지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cal, "local_today", lambda _m=None: date(2026, 11, 2))
    후보 = [solo.Ranked(i, 100 - i, {"rs": 1}) for i in (1, 2)]

    class _판:
        def execute(self, _sql: str, args: list) -> Any:
            class _R:
                rows = {1: [], 2: [(1_000.0, "2026-10-30")]}[args[0]]

            return _R()

    주문, 기록 = paper_solo.size_orders(
        _판(), solo.CANSLIM, 후보, 1, market="KR", valuation=_가치(1e6, 1e6), open_buys=[], fx_rate=None,
        as_of="2026-10-30", n_ranked=2, is_frozen=lambda _s: False,
    )  # fmt: skip
    assert [o["stock_id"] for o in 주문] == [2] and 기록["not_bought"] == {1: "시세 없음 — 다음 순위로"}
    from batch.jobs import monitor_targets as mt

    monkeypatch.setattr(mt, "rights_factors_since", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("읽음")))
    행 = {"stock_id": 2, "country": "US", "currency": "USD", "lots": [("2026-10-15", 1.0, 100.0)],
         "market_value": 91.0, "quantity": 1.0, "price_date": "2026-10-21"}  # fmt: skip
    assert job._단독넘음(object(), 행, date(2026, 10, 22), solo.CANSLIM_EXIT)["reason_code"] == "오닐손절"


def test_단독_매도_규칙_이름은_정의처와_같다() -> None:
    assert pp.SOLO_SELL_RULES == (solo.HOLD_1Y, solo.CANSLIM_EXIT)


def test_다시_잴_때_앞_회차에_통과한_줄의_예약을_뺀다(monkeypatch: pytest.MonkeyPatch) -> None:
    """두 번째 회차의 쓸 현금은 첫 회차 통과 줄을 열린 매수로 쳐 뺀다 — 빼지 않으면 같은 현금을 두 번 쓴다"""
    from batch.services import risk_gate as rg

    monkeypatch.setattr(cal, "local_today", lambda _m=None: date(2026, 11, 2))
    후보 = [solo.Ranked(i, 100 - i, {"rs": 1}) for i in range(1, 6)]

    class _판:
        def execute(self, *_a: Any) -> Any:
            class _R:
                rows = [(1_000.0, "2026-10-30")]

            return _R()

    def _관문(줄들: list[dict]) -> tuple[list, list]:  # 2 만 업종 상한
        cs = [rg.Candidate(o["stock_id"], "buy", o["cost_krw"], o["ref_date"], i, o["cash_krw"]) for i, o in enumerate(줄들)]
        return [c for c in cs if c.stock_id != 2], [(c, [("sector_cap", "업종")]) for c in cs if c.stock_id == 2]

    # 평가 총액 100만 × 10% = 100주(예약 1주 1,111원) · 쓸 현금 20만
    주문, 통과, _막힘, _기록 = paper_solo.orders_through_gate(
        _판(), solo.CANSLIM, 후보, 3, market="KR", valuation=_가치(1e6, 200_000), open_buys=[], fx_rate=None,
        as_of="2026-10-30", n_ranked=5, is_frozen=lambda _s: False, gate=_관문, account_id="Z4",
        fill_session="2026-11-02",
    )  # fmt: skip
    # 1회: 1 100주(11.11만) · 2 80주 → 2 막힘. 2회: 남은 현금 20만 − 11.11만 = 8.89만 → 3 80주, 4 는 현금 부족
    assert [(주문[c.key]["stock_id"], 주문[c.key]["quantity"]) for c in 통과] == [(1, 100), (3, 80)]
