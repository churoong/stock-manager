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
    assert "1년" in desk._손절_해당없음("Z3", 2)[0] and "−8%" in desk._손절_해당없음("Z4", 2)[0]


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
