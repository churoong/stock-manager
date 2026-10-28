"""모의 계좌 7개 (docs/paper.md 1장·2.1.1·2.1.2·2.2, docs/infra.md 25.1458).

C 시가 일괄 매수 · D 기간초과만 · E 상위 10 · R1·R2 무작위 대조 — 엔진 단위와, 실제 마이그레이션을 올린 메모리 SQLite 에서의 하루 단계.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import sell_flags as sf
from tests.test_paper_job_1445 import Client, _신호, 설정


def _o(**kw: Any) -> pp.Order:
    base = {"id": 1, "account_id": "C", "stock_id": 1, "side": "buy", "order_type": "market", "limit_price": None,
            "quantity": 10, "currency": "KRW", "fill_session": "2026-10-15", "ref_price": 10_000.0}  # fmt: skip
    return pp.Order(**{**base, **kw})


COSTS = pp.Costs(buy_fee_pct=0.015, sell_fee_pct=0.015, sell_tax_pct=0.15, slippage_pct=0.1)


# ── 엔진 ──────────────────────────────────────────────────────────────────────


def test_market_buy_fills_at_open_with_adverse_slippage() -> None:
    bar = pp.Bar("2026-10-15", 10_300, 9_900, 1_000)
    assert pp.judge(_o(), bar) == (10_300, "시가")
    # 장 열린 뒤 낸 주문은 종가 — 시가는 주문보다 앞선 값이다(2.3)
    assert pp.judge(_o(fill_basis="close"), bar)[0] == 9_900
    # 정지한 날은 안 찬다
    assert pp.judge(_o(), pp.Bar("2026-10-15", 0, 9_900, 0))[0] is None
    f = pp.make_fill(_o(), 10_300, "2026-10-15", 1.0, COSTS)
    # 시장가 매수는 슬리피지를 비싸게 쪽으로 — 원가에도 들어간다
    assert f.slippage == pytest.approx(103_000 * 0.001)
    assert f.cash_krw == -round(103_000 + 103 + 103_000 * 0.00015)
    assert pp.positions([f])[1].cost == pytest.approx(103_000 + 103 + 103_000 * 0.00015)
    # 지정가 매수는 그대로 슬리피지 없음
    assert pp.make_fill(_o(order_type="limit", limit_price=10_000.0), 10_000, "d", 1.0, COSTS).slippage == 0


def test_reserved_counts_market_buy_with_buffer() -> None:
    시장가 = _o(quantity=10)
    assert pp.reserved_krw([시장가], {}) == round(10_000 * 1.10 * 10 * (1 + pp.FEE_BUFFER))
    달러 = _o(currency="USD", ref_price=100.0, quantity=2)
    assert pp.reserved_krw([달러], {"USD": 1_400.0}) == round(
        100 * 1.10 * 2 * (1 + pp.FEE_BUFFER) * 1_400 * (1 + pp.FX_BUFFER))
    assert pp.reserved_krw([달러], {"USD": None}) is None  # 모르는 예약을 0 으로 치지 않는다


def test_size_buys_open_one_order_same_yardstick_as_reservation() -> None:
    배분 = [{"stock_id": 1, "amount": 100_000, "tranches": [{"price_at_or_below": 9_500, "amount": 40_000}],
             "ref_price": 10_000},
            {"stock_id": 2, "amount": 100_000, "tranches": [], "ref_price": 10_000}]  # fmt: skip
    주문, 빠짐 = pp.size_buys(배분, 1_000_000, 1.0, set(), "KRW", "open")
    # 회차 가격(9,500)이 아니라 기준가로 — 100,000 ÷ 10,000 = 10주, 한 주문
    assert 주문 == [{"stock_id": 1, "tranche": 1, "limit_price": None, "ref_price": 10_000, "quantity": 10},
                  {"stock_id": 2, "tranche": 1, "limit_price": None, "ref_price": 10_000, "quantity": 10}]
    assert 빠짐 == []
    # 현금은 예약과 같은 잣대(기준가 × 1.1 × 수수료 여유) — 첫 주문을 내고 나면 둘째는 그만큼 줄어든다
    현금 = 10_000 * 1.1 * (1 + pp.FEE_BUFFER) * 13
    주문, 빠짐 = pp.size_buys(배분, 현금, 1.0, set(), "KRW", "open")
    assert [o["quantity"] for o in 주문] == [10, 3]
    assert pp.reserved_krw([_o(quantity=o["quantity"]) for o in 주문], {}) <= 현금 + 1
    # 기준가가 없으면 내지 않는다 — 예약에서 빠져 같은 현금을 두 번 쓰게 된다
    주문, 빠짐 = pp.size_buys([{**배분[0], "ref_price": None}], 1_000_000, 1.0, set(), "KRW", "open")
    assert 주문 == [] and 빠짐[0]["why"] == "기준가 없음"


def test_time_rule_follows_only_period_flag() -> None:
    걸림 = {1: {"손절", "감성급락"}, 2: {"기간초과", "목표도달"}}
    assert pp.sells_for("time", 걸림) == {2: ["기간초과"]}
    assert pp.sells_for("core", 걸림) == {1: ["손절"], 2: ["기간초과", "목표도달"]}


def test_random_order_is_fixed_by_seed() -> None:
    모집단 = list(range(1, 200))
    한번 = pp.random_order(모집단, "R1|KR|1")
    assert 한번 == pp.random_order(list(reversed(모집단)), "R1|KR|1")  # 읽은 순서와 무관
    assert sorted(한번) == 모집단
    assert 한번 != pp.random_order(모집단, "R2|KR|1")  # R1·R2 는 다른 종목
    assert 한번 != pp.random_order(모집단, "R1|KR|2")  # 신호 종목마다 다른 자리


def test_summary_pairs_and_design_note() -> None:
    def 행(v: float) -> list[dict[str, Any]]:
        return [{"as_of": "2026-10-16", "invested_krw": 1_000, "equity_krw": v, "twr_index": v / 1_000,
                 "positions_json": "[]"}]  # fmt: skip

    daily = {k: 행(v) for k, v in {"A": 1_000, "B": 1_010, "C": 990, "D": 1_000, "E": 1_020, "R1": 980,
                                    "R2": 1_000}.items()}  # fmt: skip
    daily["E"][0]["positions_json"] = '[{"stock_id": 1, "value_krw": 816}]'
    daily["B"][0]["positions_json"] = '[{"stock_id": 1, "value_krw": 404}]'
    줄 = pp.summary_lines(daily, {}, "2026-10-12", "2026-10-01", {}, {}, {}, {"D": "time"})
    # 판 2(투자사, 25.1468)부터 짝은 회사 대 기준(B)과 기준 대 무작위
    assert "  A−B: -10원 (가치투자사 대 기준; 투자 비중 A 0% · B 40%)" in 줄
    assert "  C−B: -20원 (모멘텀투자사 대 기준; 투자 비중 C 0% · B 40%)" in 줄
    # 투자 비중을 함께 — E 가 더 들어가 있으면 차이의 큰 몫이 베타일 수 있다(검증 [필수] 7)
    assert "  E−B: +10원 (안정투자사 대 기준; 투자 비중 E 80% · B 40%)" in 줄
    assert any(x.startswith("  B−R1: +30원 (기준 대 무작위") for x in 줄)
    assert any(x.startswith("  B−R2: +10원 (기준 대 무작위") for x in 줄)
    assert any(x.startswith("  D 규칙 점검") and "손절을 따르지 않는 계좌" in x for x in 줄)


# ── 하루 단계 ────────────────────────────────────────────────────────────────


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    c = Client(accounts=None)
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')"
    )
    # 국내 유니버스: 1(신호 종목)·3 — 무작위 대조가 고르는 모집단
    for sid in (1, 3):
        c.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES ('2026-10-10', ?, 1, 'KRW', 't')", [sid])  # fmt: skip
        _시세(c, sid, "2026-10-14", 10_000, 10_000)
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    return c


def _시세(c: Client, sid: int, day: str, open_: float, close: float) -> None:
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')",
        [sid, day, open_, max(open_, close), min(open_, close), close],
    )


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, signals: bool = True) -> dict:
    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    return job.run_step(
        c, market="KR", trade_date=trade_date, late=False, signal_rows=[_신호()] if signals else [], settings=설정,
        price_series={}, series_unknown={}, allocate=signals, now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )[1]  # fmt: skip


def _대체(aid: str) -> int:
    return next(x for x in pp.random_order([1, 3], f"{aid}|KR|1"))


def test_seven_accounts_order_by_their_own_rules(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    n들: list[int] = []
    원래 = report_picks.select_top
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: (n들.append(n), 원래(rows, n))[1])
    기록 = _하루(c, monkeypatch, "2026-10-14")
    # G·H 는 25.1481·25.1482, S·T 는 25.1890(0092)
    assert sorted(기록["accounts"]) == ["A", "B", "C", "D", "E", "G", "H", "R1", "R2", "S", "T"]
    # E 만 상위 10 — 나머지는 5 (같은 select_top 에 계좌의 수)
    assert sorted(n들) == [5, 5, 5, 5, 5, 5, 5, 10, 10, 10]  # E·S·T 는 상위 10
    주문 = c.q("SELECT account_id, stock_id, order_type, limit_price, ref_price, quantity, tranche, reason"
               " FROM paper_orders ORDER BY account_id, tranche")  # fmt: skip
    by: dict[str, list[tuple]] = {}
    for r in 주문:
        by.setdefault(r[0], []).append(r[1:])
    분할 = [(1, "limit", 10_000, None, 4, 1, "2부 배분"), (1, "limit", 9_800, None, 3, 2, "2부 배분"),
            (1, "limit", 9_600, None, 3, 3, "2부 배분")]  # fmt: skip
    for aid in ("A", "B", "D", "E"):
        assert by[aid] == 분할, aid
    # C: 평가 100만 × 10% = 10만 → 기준가 10,000 으로 10주, 시장가 한 주문
    assert by["C"] == [(1, "market", None, 10_000, 10, 1, "2부 배분")]
    # R: 같은 비중·같은 시가 일괄 — 종목만 씨앗이 고른 것
    for aid in ("R1", "R2"):
        assert by[aid] == [(_대체(aid), "market", None, 10_000, 10, 1, "무작위 대조")], aid
        assert 기록["accounts"][aid]["random"] == {"1": _대체(aid)}
    근거 = c.q("SELECT rationale_json FROM paper_orders WHERE account_id = 'R1'")[0][0]
    assert "무작위 대조" in 근거 and "원래 자리 종목1(005930)" in 근거
    # 결정 원장에도 무작위 대조 산 것이 남는다(표본)
    assert c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'R1'") == [("buy", "무작위 대조")]


def test_open_entry_fills_at_open_and_random_mapping_is_stable(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    _하루(c, monkeypatch, "2026-10-14")
    for sid in (1, 3):
        _시세(c, sid, "2026-10-15", 10_200, 9_900)
    기록 = _하루(c, monkeypatch, "2026-10-15")
    체결 = dict(c.q("SELECT account_id, price FROM paper_fills WHERE account_id IN ('B', 'C')"))
    # B 는 지정가 10,000 (종가 9,900 ≤ 10,000), C 는 시가 10,200 — 같은 배분, 진입만 다르다
    assert 체결 == {"B": 10_000, "C": 10_200}
    C현금 = c.q("SELECT cash_krw, slippage FROM paper_fills WHERE account_id = 'C'")[0]
    assert C현금[1] == pytest.approx(102_000 * 0.001)
    # 둘째 날도 같은 신호 종목 → 같은 대체 종목 (날짜가 아니라 신호 종목에 묶인 씨앗)
    assert 기록["accounts"]["R1"]["random"] == {"1": _대체("R1")}
    assert {r[0] for r in c.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'R1'")} == {_대체("R1")}
    # 장부 검산은 모든 계좌(S·T 포함, 25.1890)
    assert c.q("SELECT COUNT(*), SUM(recon_ok) FROM paper_daily WHERE as_of = '2026-10-16'") == [(11, 11)]


def test_d_holds_through_stop_loss(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    _하루(c, monkeypatch, "2026-10-14")
    for sid in (1, 3):
        _시세(c, sid, "2026-10-15", 10_000, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    monkeypatch.setattr(sf, "evaluate", lambda h, _t, _r=None: [sf.Flag(h.stock_id, "red", "손절", "x", [])])
    for sid in (1, 3):
        _시세(c, sid, "2026-10-16", 8_000, 8_000)
    _하루(c, monkeypatch, "2026-10-16")
    판 = {r[0] for r in c.q("SELECT account_id FROM paper_orders WHERE side = 'sell'")}
    assert {"A", "B", "C", "E"} <= 판 and "D" not in 판
    # D 는 손절선 아래 보유에 더 사지 않는다 — 판정은 계좌 규칙 전의 것으로 (검증 [필수] 4: 물타기가 B−D 에 섞였다)
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'D' AND decided_on = '2026-10-16'") == [(0,)]
    # D 는 따르지 않은 손절을 결정 원장에 들고 간 것으로 남긴다 — "손절을 무시한 결과" 의 표본
    assert sorted(c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'D' AND decided_on = '2026-10-16'")) == [
        ("hold_flag", "손절"), ("skip", "보유 중 손절선 이탈")]


def test_random_skips_stale_but_cooldown_leaves_slot_empty(c: Client) -> None:
    """자료 문제(묵은 종가)는 씨앗 순서의 다음으로, 계좌 상태(판 뒤 쉼)는 자리를 비운다 — C 가 X 를 쉬는 날과 같은 뜻.

    쉼에서 다시 뽑으면 R 만 쉬는 동안 다른 종목을 사고 쉼이 끝나면 또 사서 C 와 투자 속도가 갈렸다 (검증 [필수] 3)
    """
    오늘 = date(2026, 10, 15)
    첫째, 둘째 = pp.random_order([1, 3], "R1|KR|1")
    c.conn.execute("DELETE FROM prices WHERE stock_id = ?", [첫째])
    _시세(c, 첫째, "2026-10-01", 10_000, 10_000)  # 14일 묵음
    기록: dict[str, Any] = {}
    줄 = job.random_substitute(c, [_신호()], account="R1", market="KR", pool=[1, 3], cooldown=set(),
                               stock_cache={}, today=오늘, 기록=기록)  # fmt: skip
    assert [r.stock_id for r in 줄] == [둘째] and 줄[0].total_score is None and 줄[0].close == 10_000
    # 회차 가격 키(`price`)도 대체 종목의 종가로 — build_portfolio 의 1주 검사가 읽는다 (검증 [필수] 2)
    assert {t["price"] for t in 줄[0].tranche_plan} == {10_000} and 줄[0].buy_zone_low == 10_000
    assert 줄[0].criteria[0]["display"].startswith("원래 자리 종목1(005930)")
    줄 = job.random_substitute(c, [_신호()], account="R1", market="KR", pool=[1, 3], cooldown={둘째},
                               stock_cache={}, today=오늘, 기록=기록)  # fmt: skip
    assert 줄 == [] and "판 뒤" in 기록["random_empty"]["1"]


def test_random_order_survives_universe_changes() -> None:
    """랑데부 해싱 — 다른 종목이 빠지거나 더해져도 남은 종목끼리의 순서는 그대로 (검증 [필수] 3).

    목록 전체를 섞던 판은 한 종목만 더해도 순열이 통째로 바뀌어, 주간 유니버스 갱신마다 대체 종목이 바뀌었다
    """
    모집단 = list(range(1, 2_000))
    처음 = pp.random_order(모집단, "R1|KR|5930")
    늘림 = pp.random_order([*모집단, *range(5_000, 5_020)], "R1|KR|5930")
    assert [x for x in 늘림 if x < 5_000] == 처음
    줄임 = pp.random_order([x for x in 모집단 if x != 처음[5]], "R1|KR|5930")
    assert 줄임[:5] == 처음[:5]


def test_accounts_rules_come_from_columns_not_names(c: Client) -> None:
    행 = c.q("SELECT id, sell_rule, entry_rule, top_n, selection FROM paper_accounts ORDER BY id")
    assert 행 == [("A", "all", "tranche", 5, "signal"), ("B", "core", "tranche", 5, "signal"),
                 ("C", "core", "open", 5, "signal"), ("D", "time", "tranche", 5, "signal"),
                 ("E", "core", "tranche", 10, "signal"), ("G", "trend", "tranche", 5, "signal"),
                 ("H", "trend", "tranche", 5, "weighted"), ("R1", "core", "open", 5, "random"),
                 ("R2", "core", "open", 5, "random"), ("S", "swing_close", "open", 10, "short"),
                 ("T", "day_close", "open", 10, "short")]  # fmt: skip
    assert set(r[1] for r in 행) <= set(pp.SELL_RULES)


def test_web_buffers_match_engine() -> None:
    """웹 예약(입금 취소 막기·화면)은 배치와 같은 여유로 센다 — 어긋나면 취소가 묶인 돈을 덜 본다 (검증 [필수] 5)."""
    import re
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "paper.ts").read_text(encoding="utf-8")
    for 이름 in ("FEE_BUFFER", "FX_BUFFER", "OPEN_BUY_BUFFER"):
        값 = re.search(rf"export const {이름} = ([0-9.]+);", 글)
        assert 값 and float(값.group(1)) == getattr(pp, 이름), 이름
    # 짝 목록도 같다 — 웹 주석이 "같은 목록" 이라 하면서 문구가 달랐다 (25.1458 재검증)
    웹짝 = re.findall(r'\["(\w+)", "(\w+)", "([^"]+)"\]', 글[글.index("export const PAIRS"):])
    assert [tuple(x) for x in 웹짝] == [tuple(x) for x in pp.PAIRS]


def test_news_targets_only_for_accounts_that_sell_on_sentiment(c: Client) -> None:
    """뉴스 수집은 감성급락을 따르는 계좌(A)의 보유만 — 일곱 계좌를 다 넣으면 무작위 종목만큼 수집이 는다 (25.1458)."""
    from batch.jobs import monitor_targets

    def 삼(order_id: int, aid: str, sid: int) -> None:
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
            " decided_on, fill_session, tranche, reason, rule_version, status, created_at) VALUES (?, ?, 'KR', ?, 'buy',"
            " 'limit', 10000, 1, 'KRW', '2026-10-14', '2026-10-15', 1, 't', 1, 'filled', 't')", [order_id, aid, sid])  # fmt: skip
        c.conn.execute(
            "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
            " fee, tax, slippage, cash_krw, created_at) VALUES (?, ?, ?, 'buy', '2026-10-15', 10000, 1, 'KRW', 1, 0, 0,"
            " 0, -10000, 't')", [order_id, aid, sid])  # fmt: skip

    삼(1, "R1", 3)
    삼(2, "B", 3)
    assert [r for r in monitor_targets.news_target_rows(c, "KR", [], "t") if r[3] == "paper"] == []  # type: ignore[arg-type]
    삼(3, "A", 1)
    assert [r for r in monitor_targets.news_target_rows(c, "KR", [], "t") if r[3] == "paper"] == [  # type: ignore[arg-type]
        ("KR", 1, None, "paper", "t")]


def test_random_keeps_held_substitute_and_empties_slot_on_data_issue(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """이미 든 대체 종목은 씨앗을 다시 보지 않고 잇는다. 그 종목이 멈추면 자리를 비운다 — 다음 후보를 새로 사면
    든 것은 원가로 남은 채 그 자리만 돈이 두 배가 됐다 (25.1458 재검증 [필수] 1)."""
    오늘 = date(2026, 10, 15)
    첫째, 둘째 = pp.random_order([1, 3], "R1|KR|1")
    기록: dict[str, Any] = {}
    # 씨앗으로는 첫째가 오지만 둘째를 그 자리로 들고 있다 → 둘째를 잇는다(모집단에 없어도)
    줄 = job.random_substitute(c, [_신호()], account="R1", market="KR", pool=[첫째], cooldown=set(),
                               stock_cache={}, today=오늘, 기록=기록, held={1: 둘째})  # fmt: skip
    assert [r.stock_id for r in 줄] == [둘째]
    # 든 종목이 멈췄다 → 다음 후보(첫째)를 사지 않고 자리를 비운다
    monkeypatch.setattr(job, "frozen_ids", lambda _c, ids, _m: set(ids) & {둘째})
    줄 = job.random_substitute(c, [_신호()], account="R1", market="KR", pool=[1, 3], cooldown=set(),
                               stock_cache={}, today=오늘, 기록=기록, held={1: 둘째})  # fmt: skip
    assert 줄 == [] and "자료 문제" in 기록["random_empty"]["1"]


def test_held_substitutes_read_from_order_rationale(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    _하루(c, monkeypatch, "2026-10-14")
    대체 = _대체("R1")
    c.conn.execute("UPDATE paper_orders SET status = 'filled' WHERE account_id = 'R1'")
    assert job.held_substitutes(c, "R1", {대체}) == {1: 대체}
    assert job.held_substitutes(c, "R1", set()) == {}  # 다 판 뒤에는 잇지 않는다 — 씨앗으로 다시


def test_gate_caps_use_expected_cost_cash_uses_reservation() -> None:
    """상한은 낼 것으로 보는 값, 현금은 예약과 같은 잣대(시장가 틈 여유 포함) — 한 값이면 둘 중 하나가 틀린다 (25.1458 재검증)."""
    from batch.services import risk_gate as rg

    문맥 = rg.Context(equity_krw=1_000_000, day_return_pct=None, cash_available_krw=105_000, holding_krw={},
                      sector_of={}, max_stock_pct=10.0, max_sector_pct=30.0, today="2026-10-15")  # fmt: skip
    후보 = rg.Candidate(1, "buy", 101_000, "2026-10-14", 0, 111_100)
    통과, 막힘 = rg.gate([후보], 문맥)
    assert 통과 == [] and [k for k, _t in 막힘[0][1]] == ["cash"]  # 상한(10.1%)은 통과, 현금(111,100 > 105,000)에서 막힘
    통과, 막힘 = rg.gate([rg.Candidate(1, "buy", 101_000, "2026-10-14", 0, 104_000)], 문맥)
    assert len(통과) == 1


def test_universe_sql_matches_latest_snapshot_helper() -> None:
    """무작위 대조 모집단의 스냅샷 서브쿼리는 `db.latest_snapshot_sql()` 그대로 — 나라마다 따로 잡는다(2026-09-17 결함)."""
    from batch.core import db

    assert job.UNIVERSE_SQL.endswith(f"AND u.snapshot_date = {db.latest_snapshot_sql()}")
