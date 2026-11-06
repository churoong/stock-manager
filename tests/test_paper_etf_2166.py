"""가상모의투자 ETF 전술 계좌 X(국내ETF)·Y(미국ETF) — 엔진 단계 (docs/paper.md 17장, docs/infra.md 25.2166).

계좌 행은 0094(2026-11-09 개설)지만 시험은 개설일 막이 밖의 날짜로 행을 직접 넣어 본다: 상승 국면이면 지수 ETF 를 평가
총액의 98% 로 사고, 같은 국면이면 그대로 두고, 국면이 바뀐 날 팔고 다음 실행에 새 국면 ETF(인버스 50%)를 산다.
계좌의 시장 실행에서만 움직이고, 다른 계좌의 행은 그대로.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.jobs import paper_etf
from batch.services import desk, report_picks
from batch.services import etf_tactical as et
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client, _신호, 설정

KODEX200, INVERSE, SPY, SH = 10, 11, 12, 13


def _계좌(accounts: tuple[str, ...] = ("B",), etf: tuple[str, ...] = ("X",)) -> Client:
    c = Client(accounts=accounts)
    for aid in etf:
        c.conn.execute(
            "INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)"
            " VALUES (?, ?, 'etf_switch', 1, '2026-10-01T00:00:00+00:00', 'open', 1, 'etf_tactical')", [aid, aid])
    for sid, ticker, country, cur, name in ((KODEX200, "069500", "KR", "KRW", "KODEX 200"),
                                            (INVERSE, "114800", "KR", "KRW", "KODEX 인버스"),
                                            (SPY, "SPY", "US", "USD", "SPY"), (SH, "SH", "US", "USD", "SH")):  # fmt: skip
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source,"
            " fetched_at, asset_type) VALUES (?, ?, 'M', ?, ?, 'ETF', ?, 'active', 't', 't', 'etf')",
            [sid, ticker, country, name, cur])  # fmt: skip
        c.conn.execute(
            "INSERT INTO etfs (symbol, country, name, source, fetched_at, stock_id) VALUES (?, ?, ?, 't', 't', ?)",
            [ticker, country, name, sid])  # fmt: skip
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-01', 1000000, 'deposit', '2026-10-01T00:00:00+00:00', 'k1')"
    )
    c.conn.execute("INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', '2026-10-14', 1400,"
                   " 't', 't')")  # fmt: skip
    return c


@pytest.fixture(autouse=True)
def _고정(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())


def _지수(c: Client, code: str, last: str, n: int = 230, 시작: float = 100.0, 오름: float = 1.0) -> None:
    """`last` 까지 평일 n 개 — 날마다 `오름` 씩 오르는 지수(상승 국면)"""
    d, 줄 = date.fromisoformat(last), []
    while len(줄) < n:
        if d.weekday() < 5:
            줄.append(d.isoformat())
        d -= timedelta(days=1)
    for i, day in enumerate(reversed(줄)):
        c.conn.execute("INSERT OR REPLACE INTO index_prices (index_code, date, close, source, fetched_at)"
                       " VALUES (?, ?, ?, 't', 't')", [code, day, 시작 + 오름 * i])  # fmt: skip


def _지수값(c: Client, code: str, day: str, close: float) -> None:
    c.conn.execute("INSERT OR REPLACE INTO index_prices (index_code, date, close, source, fetched_at)"
                   " VALUES (?, ?, ?, 't', 't')", [code, day, close])  # fmt: skip


def _시세(c: Client, sid: int, day: str, open_: float, close: float, cur: str = "KRW") -> None:
    c.conn.execute(
        "INSERT OR REPLACE INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 1000, ?, 't', 't')", [sid, day, open_, max(open_, close), min(open_, close), close,
                                                           cur])  # fmt: skip


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, market: str = "KR",
         signals: bool = True, allocate: bool = True, settings: dict | None = 설정) -> tuple[list[str], dict]:  # fmt: skip
    다음 = cal.next_session(market, date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    return job.run_step(
        c, market=market, trade_date=trade_date, late=False, signal_rows=[_신호()] if signals else [],
        settings=settings, price_series={}, series_unknown={}, allocate=allocate,
        now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip


def _주문(c: Client, aid: str = "X") -> list[tuple]:
    return c.q("SELECT stock_id, side, order_type, quantity, reason, fill_session, tranche FROM paper_orders"
               " WHERE account_id = ? ORDER BY id", [aid])  # fmt: skip


def _평가(cash: float, equity: float = 1_000_000) -> pp.Valuation:
    return pp.Valuation(invested_krw=equity, cash_krw=cash, holdings_krw=equity - cash, equity_krw=equity, pnl_krw=0,
                        pnl_pct=0.0)  # fmt: skip


# ── 계획(순수에 가까운 단위) ─────────────────────────────────────────────────


def test_plan_은_계좌의_시장_실행에서만_움직인다() -> None:
    c = _계좌(etf=("X", "Y"))
    _지수(c, "KOSPI", "2026-10-14")
    assert paper_etf.plan(c, "X", "US", "2026-10-14", {}, set()) == {"skip": "이 계좌의 시장이 아님"}
    assert paper_etf.plan(c, "Y", "KR", "2026-10-14", {}, set()) == {"skip": "이 계좌의 시장이 아님"}
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", {}, set())
    assert (p["regime"].state, p["want"], p["held"], p["action"], p["buy_role"]) == ("up", "long", None, "buy", "long")
    assert p["ids"] == {"long": KODEX200, "inverse": INVERSE} and p["sells"] == {}


def test_plan_은_같은_국면이면_두고_바뀌면_판다() -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    든 = {KODEX200: pp.Position(KODEX200, "KRW", 10, 300_000.0)}
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", 든, set())
    assert (p["action"], p["sells"], p["buy_role"]) == ("hold", {}, None)
    _지수값(c, "KOSPI", "2026-10-15", 150.0)  # 200일선 아래·20일 수익 음수 → 하락
    p = paper_etf.plan(c, "X", "KR", "2026-10-15", 든, set())
    assert p["action"] == "sell" and list(p["sells"]) == [KODEX200] and p["buy_role"] is None
    사유, 근거 = p["sells"][KODEX200]
    assert 사유 == [paper_etf.SWITCH_CODE] and 근거[0]["reason_code"] == paper_etf.SWITCH_CODE
    assert [r["label"] for r in 근거[0]["criteria"]] == ["지수 종가 대 200일선", "20거래일 수익", "국면"]
    # 이미 매도가 열려 있으면 다시 내지 않는다
    assert paper_etf.plan(c, "X", "KR", "2026-10-15", 든, {KODEX200})["sells"] == {}


def test_plan_은_모르는_보유나_두_몫을_모두_정리한다() -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    둘 = {KODEX200: pp.Position(KODEX200, "KRW", 1, 1.0), INVERSE: pp.Position(INVERSE, "KRW", 1, 1.0)}
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", 둘, set())
    assert p["held"] == "?" and sorted(p["sells"]) == [KODEX200, INVERSE] and p["buy_role"] is None
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", {1: pp.Position(1, "KRW", 3, 3.0)}, set())
    assert p["held"] == "?" and list(p["sells"]) == [1]


def test_지수가_모자라거나_묵으면_현금() -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14", n=150)  # 200일치 미만
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", {}, set())
    assert (p["regime"].state, p["action"], p["buy_role"]) == ("unknown", "hold", None)
    _지수(c, "KOSPI", "2026-10-14")
    p = paper_etf.plan(c, "X", "KR", "2026-10-30", {}, set())  # 마지막 값이 16일 묵음
    assert p["regime"].state == "unknown" and p["buy_role"] is None


def test_ETF_가_종목표에_없으면_사지_않고_까닭을_남긴다() -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    c.conn.execute("UPDATE etfs SET stock_id = NULL WHERE name = 'KODEX 200'")
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", {}, set())
    assert p["buy_role"] is None and p["tactical_missing"] == ["long"]
    assert p["skip"] == "지수 ETF가 종목표에 이어지지 않음 — 이름 대조 확인"  # 사람 글엔 코드 이름 없이(25.2170)


def test_buy_order_는_평가_총액_곱하기_비중을_시장가_한_줄로() -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", {}, set())
    v = _평가(1_000_000)
    주문, 기록 = paper_etf.buy_order(c, p, market="KR", valuation=v, open_buys=[], fx_rate=None,
                                   trade_date="2026-10-15")  # fmt: skip
    수량 = 1_000_000 * et.LONG_WEIGHT // 30_000  # 기준가로 — 시가 틈 여유 10% 로 나누지 않는다(98% 그대로)
    assert 수량 == 32
    assert [(o["stock_id"], o["quantity"], o["limit_price"], o["tranche"]) for o in 주문] == [(KODEX200, 수량, None, 0)]
    assert 기록 == {"regime": "up", "role": "long", "quantity": 수량}
    # 예약을 뺀 쓸 현금이 비중보다 작으면 현금까지만
    v2 = _평가(300_000)
    주문, _ = paper_etf.buy_order(c, p, market="KR", valuation=v2, open_buys=[], fx_rate=None, trade_date="2026-10-15")
    # 30만 ÷ (3만 × 1.01) = 9.9 → 9주 (수수료 여유를 빼면 10주 — 예약이 현금을 넘는다)
    assert 주문[0]["quantity"] == 9
    # 관문에 넘기는 값 — 예약과 같은 수수료 여유
    assert 주문[0]["cost_krw"] == pytest.approx(9 * 30_000 * (1 + pp.FEE_BUFFER))
    # 관문 현금 검사도 같은 값 — 쓸 현금 전체를 넘기면 검사가 늘 통과했다(25.2168)
    assert 주문[0]["cash_krw"] == 주문[0]["cost_krw"]
    # 같은 ETF 매수가 이미 열려 있으면 내지 않는다
    열림 = pp.Order(id=9, account_id="X", stock_id=KODEX200, side="buy", order_type="market", limit_price=None,
                    quantity=3, currency="KRW", fill_session="2026-10-15", ref_price=30_000.0)  # fmt: skip
    assert paper_etf.buy_order(c, p, market="KR", valuation=v, open_buys=[열림], fx_rate=None,
                               trade_date="2026-10-15")[0] == []  # fmt: skip


def test_buy_order_달러는_환율_여유까지_현금_상한() -> None:
    c = _계좌(etf=("Y",))
    _지수(c, "SP500", "2026-10-14")
    _시세(c, SPY, "2026-10-14", 100, 100, "USD")
    p = paper_etf.plan(c, "Y", "US", "2026-10-14", {}, set())
    주문, _ = paper_etf.buy_order(c, p, market="US", valuation=_평가(1_000_000), open_buys=[], fx_rate=1_400.0,
                                trade_date="2026-10-15")  # fmt: skip
    # 98% ÷ 14만 = 7.0 이지만 현금 100만 ÷ (14만 × 1.01 × 1.03) = 6.87 → 6주
    assert (주문[0]["quantity"], 주문[0]["currency"]) == (6, "USD")
    assert 주문[0]["cost_krw"] == pytest.approx(6 * 140_000 * (1 + pp.FEE_BUFFER) * (1 + pp.FX_BUFFER))
    assert 주문[0]["cost_krw"] <= 1_000_000


# ── 하루 단계 ────────────────────────────────────────────────────────────────


def test_X_는_오르면_사고_그대로_두고_내리면_팔고_다음_실행에_인버스(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    _시세(c, INVERSE, "2026-10-14", 5_000, 5_000)
    _, 기록 = _하루(c, monkeypatch, "2026-10-14")
    수량 = int(1_000_000 * et.LONG_WEIGHT // 30_000)
    assert _주문(c) == [(KODEX200, "buy", "market", 수량, f"{et.RULE}: 상승 국면 → 지수 ETF 98%", "2026-10-15", 0)]
    assert 기록["accounts"]["X"]["etf"]["regime"] == "up" and 기록["accounts"]["X"]["buy"]["quantity"] == 수량
    근거 = json.loads(c.q("SELECT rationale_json FROM paper_orders WHERE account_id = 'X'")[0][0])
    assert 근거["rule"] == et.RULE and 근거["criteria"][0]["source"] == "index_prices"
    assert c.q("SELECT kind, stock_id FROM paper_decisions WHERE account_id = 'X'") == [("buy", KODEX200)]
    # 단기 신호 종목(1)은 사지 않는다 — ETF 만
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'X' AND stock_id = 1") == [(0,)]
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B' AND stock_id = 1")[0][0] >= 1

    # 다음 날: 시가에 차고, 같은 상승 국면이라 새 주문 없음
    _지수값(c, "KOSPI", "2026-10-15", 330.0)
    _시세(c, KODEX200, "2026-10-15", 30_000, 30_300)
    _, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert c.q("SELECT stock_id, side, quantity, price FROM paper_fills WHERE account_id = 'X'") == [
        (KODEX200, "buy", 수량, 30_000)]
    assert 기록["accounts"]["X"]["etf"]["action"] == "hold" and len(_주문(c)) == 1

    # 지수가 무너진 날: 정리 매도만(사는 것은 판 돈이 든 다음 실행)
    _지수값(c, "KOSPI", "2026-10-16", 150.0)
    _시세(c, KODEX200, "2026-10-16", 30_300, 28_000)
    _, 기록 = _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert _주문(c)[1:] == [(KODEX200, "sell", "market", 수량, paper_etf.SWITCH_CODE, "2026-10-19", 0)]
    assert 기록["accounts"]["X"]["etf"]["action"] == "sell"
    assert c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'X' AND kind = 'sell'") == [
        ("sell", paper_etf.SWITCH_CODE)]

    # 다음 실행: 매도가 차서 현금 — 하락 국면이라 인버스 50%
    _지수값(c, "KOSPI", "2026-10-19", 148.0)
    _시세(c, KODEX200, "2026-10-19", 28_000, 27_900)
    _시세(c, INVERSE, "2026-10-19", 5_200, 5_200)
    _, 기록 = _하루(c, monkeypatch, "2026-10-19", signals=False)
    fills, _ = job._fills(c)
    assert pp.positions([f for f in fills if f.account_id == "X"]) == {}
    # 국내 ETF 매도는 증권거래세 0 — 수수료만(25.2166). 같은 날 종목 매도면 세금이 붙는다(아래 시험)
    assert c.q("SELECT fee > 0, tax FROM paper_fills WHERE account_id = 'X' AND side = 'sell'") == [(1, 0.0)]
    현금 = c.q("SELECT cash_krw, equity_krw FROM paper_daily WHERE account_id = 'X' ORDER BY as_of DESC LIMIT 1")[0]
    인버스 = int(min(현금[1] * et.INVERSE_WEIGHT / 5_200, 현금[0] / (5_200 * (1 + pp.FEE_BUFFER))))
    assert _주문(c)[2:] == [(INVERSE, "buy", "market", 인버스, f"{et.RULE}: 하락 국면 → 인버스 50%", "2026-10-20", 0)]
    assert 기록["accounts"]["X"]["recon_ok"] is True


def test_Y_는_미국_실행에서만_SPY_를_달러로_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(etf=("X", "Y"))
    _지수(c, "SP500", "2026-10-14")
    _시세(c, SPY, "2026-10-14", 500, 500, "USD")
    _, 기록 = _하루(c, monkeypatch, "2026-10-14", market="KR")
    assert _주문(c, "Y") == [] and 기록["accounts"]["Y"]["etf"]["skip"] == "이 계좌의 시장이 아님"
    _, 기록 = _하루(c, monkeypatch, "2026-10-14", market="US", signals=False)
    주당 = 500 * 1_400
    수량 = int(min(1_000_000 * et.LONG_WEIGHT / 주당, 1_000_000 / (주당 * (1 + pp.FEE_BUFFER) * (1 + pp.FX_BUFFER))))
    assert _주문(c, "Y") == [(SPY, "buy", "market", 수량,
                             f"{et.RULE}: 상승 국면 → 지수 ETF 98%", "2026-10-15", 0)]  # fmt: skip
    assert c.q("SELECT currency FROM paper_orders WHERE account_id = 'Y'") == [("USD",)]
    # X 는 국내 지수가 없어 모름 → 현금(아무것도 안 함)
    assert _주문(c, "X") == []


def test_ETF_계좌가_있어도_다른_계좌의_행은_그대로(monkeypatch: pytest.MonkeyPatch) -> None:
    def 행(etf: tuple[str, ...]) -> list[tuple]:
        c = _계좌(etf=etf)
        _지수(c, "KOSPI", "2026-10-14")
        _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
        _하루(c, monkeypatch, "2026-10-14")
        return c.q("SELECT account_id, stock_id, side, order_type, limit_price, quantity, tranche, reason"
                   " FROM paper_orders WHERE account_id <> 'X' ORDER BY id")  # fmt: skip

    assert 행(()) == 행(("X",)) != []


def test_국내_ETF_매도만_거래세가_없다() -> None:
    """같은 체결 길에서 종목(asset_type stock) 매도는 그대로 세금, ETF 매도는 0 — 다른 계좌는 바뀌지 않는다"""
    c = _계좌()

    def 주문(sid: int, side: str, day: str, 판정일: str) -> None:
        c.conn.execute(
            "INSERT INTO paper_orders (account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
            " decided_on, fill_session, fill_basis, tranche, reason, created_at, ref_price, rule_version)"
            " VALUES ('X', 'KR', ?, ?, 'market', NULL, 1, 'KRW', ?, ?, 'open', 0, ?, 't', 10000, 1)",
            [sid, side, 판정일, day, paper_etf.SWITCH_CODE])  # fmt: skip

    for sid in (KODEX200, 1):
        주문(sid, "buy", "2026-10-15", "2026-10-14")
        _시세(c, sid, "2026-10-15", 10_000, 10_000)
        _시세(c, sid, "2026-10-16", 10_000, 10_000)
    job.fill_orders(c, "KR", "2026-10-15", job._flows(c)[0], [], None, None, set(), "t", {})
    for sid in (KODEX200, 1):
        주문(sid, "sell", "2026-10-16", "2026-10-15")
    job.fill_orders(c, "KR", "2026-10-16", job._flows(c)[0], job._fills(c)[0], None, None, set(), "t", {})
    세금 = dict(c.q("SELECT stock_id, tax FROM paper_fills WHERE side = 'sell'"))
    assert 세금[KODEX200] == 0.0 and 세금[1] > 0


def test_시가가_여유보다_뛰면_그날_매수는_취소되고_다음_실행이_다시_낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    """98% 를 기준가로 사므로 시가 틈 여유는 약 2% — 넘게 뛰면 체결 때 현금 검사가 취소한다(빚 없음, 25.2166)"""
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    _하루(c, monkeypatch, "2026-10-14", signals=False)
    _지수값(c, "KOSPI", "2026-10-15", 331.0)
    _시세(c, KODEX200, "2026-10-15", 32_000, 32_000)  # +6.7% 갭 — 32주 × 32,000 > 100만
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert c.q("SELECT COUNT(*) FROM paper_fills WHERE account_id = 'X'") == [(0,)]
    상태 = c.q("SELECT status, quantity, fill_session FROM paper_orders WHERE account_id = 'X' ORDER BY id")
    assert 상태 == [("expired", 32, "2026-10-15"), ("open", int(1_000_000 * et.LONG_WEIGHT // 32_000), "2026-10-16")]
    # 현금은 음수가 되지 않았다
    assert c.q("SELECT MIN(cash_krw) >= 0 FROM paper_daily WHERE account_id = 'X'") == [(1,)]


# ── 25.2168 (25.2166 교차검증 권고) ──────────────────────────────────────────


def test_ETF_계좌는_따로_재는_실험_계좌다() -> None:
    """빠지면 순위표 공통 구간·KIS 도전자 후보에 들어가 KIS모의투자가 ETF 주문을 베낄 수 있다"""
    assert {"X", "Y"} <= set(pp.SEPARATE_IDS)
    assert pp.ETF_SWITCH_RULE == paper_etf.SELL_RULE and paper_etf.SELL_RULE in pp.SELL_RULES


def test_월간_복기의_규칙_점검은_ETF_전술에_해당_없음() -> None:
    행 = [{"as_of": "2026-10-16", "invested_krw": 1_000, "equity_krw": 1_000, "twr_index": 1.0,
           "positions_json": '[{"stock_id": 10, "value_krw": 980, "horizon": "long"}]'}]  # fmt: skip
    줄 = pp.summary_lines({"X": 행}, {}, "2026-10-12", "2026-10-01", {}, {}, {"max_stock_pct": 10.0}, {"X": "etf_switch"})
    점검 = [x for x in 줄 if "규칙 점검" in x]
    assert len(점검) == 1 and "해당 없음(ETF 전술" in 점검[0] and "상한 넘은 날" not in 점검[0]


def test_멈춤_스위치면_ETF_매수도_막히고_결정_원장에_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('paper_halt', '{\"on\": true}', 't')")
    _, 기록 = _하루(c, monkeypatch, "2026-10-14", signals=False)
    assert _주문(c) == [] and 기록["accounts"]["X"]["risk_blocked"] == 1
    assert c.q("SELECT kind, reason, stock_id FROM paper_decisions WHERE account_id = 'X'") == [
        ("blocked", "위험 점검: 멈춤 스위치가 켜져 있음", KODEX200)]


def test_주문_근거는_웹_거래내역이_읽는_자리와_ETF_비용(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    _하루(c, monkeypatch, "2026-10-14", signals=False)
    근거 = json.loads(c.q("SELECT rationale_json FROM paper_orders WHERE account_id = 'X'")[0][0])
    # 웹 TradesPane 은 signal.criteria·flags[].criteria 만 읽는다
    assert [r["label"] for r in 근거["signal"]["criteria"]] == ["지수 종가 대 200일선", "20거래일 수익", "국면"]
    assert 근거["costs"]["sell_tax_pct"] == 0.0  # 국내 ETF — 체결 길과 같은 비용
    _지수값(c, "KOSPI", "2026-10-15", 150.0)
    _시세(c, KODEX200, "2026-10-15", 30_000, 30_000)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    매도 = json.loads(c.q("SELECT rationale_json FROM paper_orders WHERE account_id = 'X' AND side = 'sell'")[0][0])
    assert 매도["costs"]["sell_tax_pct"] == 0.0 and 매도["flags"][0]["reason_code"] == paper_etf.SWITCH_CODE
    # 다른 계좌(종목) 매도세는 그대로 — 비용표를 바꾸지 않았다
    assert job.cost_snapshot("KR", None, None, "2026-10-16")["sell_tax_pct"] > 0


def test_국면은_사라는데_못_산_날은_리포트와_결정_원장에(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")  # KODEX 200 시세 없음
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14", signals=False)
    assert "⚠️ 가상 국내ETF 사지 못함 — ETF 시세 없음(이은 날부터 쌓인다)" in 줄
    assert c.q("SELECT kind, reason, stock_id FROM paper_decisions WHERE account_id = 'X'") == [
        ("skip", "ETF 전술: ETF 시세 없음(이은 날부터 쌓인다)", KODEX200)]
    할일 = desk.checks(halted=False, paper=기록, broker=None, recon_bad=[], warnings=0)
    assert {"level": "normal", "text": "가상 국내ETF 사지 못함 — ETF 시세 없음(이은 날부터 쌓인다)"} in 할일


def test_든_ETF_의_연결이_끊기면_팔지_않고_멈춤을_알린다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    _하루(c, monkeypatch, "2026-10-14", signals=False)
    _지수값(c, "KOSPI", "2026-10-15", 330.0)
    _시세(c, KODEX200, "2026-10-15", 30_000, 30_000)
    _하루(c, monkeypatch, "2026-10-15", signals=False)  # 32주 찼다
    c.conn.execute("UPDATE etfs SET stock_id = NULL WHERE name = 'KODEX 200'")  # 이름 대조가 끊긴 날
    _지수값(c, "KOSPI", "2026-10-16", 331.0)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'X' AND side = 'sell'") == [(0,)]
    assert 기록["accounts"]["X"]["etf"]["action"] == "hold"
    assert 기록["accounts"]["X"]["etf"]["tactical_missing"] == ["long"]  # 끊긴 몫은 실행 기록에(25.2171)
    assert "⚠️ 가상 국내ETF 멈춤 — 든 지수 ETF의 종목표 연결이 끊김 — 이름 대조 확인" in 줄
    # 상황실 '확인할 일' 도 같은 술어(25.2170)
    할일 = desk.checks(halted=False, paper=기록, broker=None, recon_bad=[], warnings=0)
    assert {"level": "high", "text": "가상 국내ETF 멈춤 — 든 지수 ETF의 종목표 연결이 끊김 — 이름 대조 확인"} in 할일

    # 연결이 끊긴 채 국면이 바뀌면 그래도 판다 — 25.2168 은 늘 들고 가 하락 국면에도 98% 를 들었다(25.2170)
    _지수값(c, "KOSPI", "2026-10-19", 150.0)
    _시세(c, KODEX200, "2026-10-16", 30_000, 30_000)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-19", signals=False)
    assert c.q("SELECT stock_id, quantity, reason FROM paper_orders WHERE account_id = 'X' AND side = 'sell'") == [
        (KODEX200, 32, paper_etf.SWITCH_CODE)]
    매도근거 = json.loads(c.q("SELECT rationale_json FROM paper_orders WHERE account_id = 'X' AND side = 'sell'")[0][0])
    assert "하락 국면 → 지수 ETF 정리" in 매도근거["flags"][0]["rationale"]  # 사람 글 — long·inverse 아님
    assert 기록["accounts"]["X"]["etf"]["action"] == "sell" and not [x for x in 줄 if "국내ETF 멈춤" in x]
    assert 기록["accounts"]["X"]["etf"]["skip"] is None  # 파는 날은 멈춤이 아니다 — 실행 기록에도 남기지 않는다


def test_인버스_연결만_끊겨도_상승_국면이면_지수_ETF_를_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    c.conn.execute("UPDATE etfs SET stock_id = NULL WHERE name = 'KODEX 인버스'")
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14", signals=False)
    assert [r[:2] for r in _주문(c)] == [(KODEX200, "buy")] and not [x for x in 줄 if "국내ETF" in x and "⚠️" in x]
    assert 기록["accounts"]["X"]["etf"]["action"] == "buy"


def test_경고는_나야_할_날에만(monkeypatch: pytest.MonkeyPatch) -> None:
    """다른 시장 실행·같은 국면·갈아타 파는 날·같은 날 다시 돈 실행(이미 열린 매수)엔 ⚠️ 도 결정 원장 skip 도 없다"""
    c = _계좌(etf=("X", "Y"))
    _지수(c, "KOSPI", "2026-10-14")
    _지수(c, "SP500", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)

    def 경고(줄: list[str]) -> list[str]:
        return [x for x in 줄 if x.startswith("⚠️") and ("ETF 사" in x or "ETF 멈" in x)]

    줄, 기록 = _하루(c, monkeypatch, "2026-10-14", signals=False)  # 국내 실행 — Y 는 판단하지 않는다
    assert 경고(줄) == [] and 기록["accounts"]["Y"]["etf"]["skip"] == "이 계좌의 시장이 아님"
    줄, _ = _하루(c, monkeypatch, "2026-10-14", signals=False)  # 같은 날 다시 — 매수가 이미 열려 있다
    assert 경고(줄) == [] and len(_주문(c)) == 1
    _지수값(c, "KOSPI", "2026-10-15", 330.0)
    _시세(c, KODEX200, "2026-10-15", 30_000, 30_000)
    줄, _ = _하루(c, monkeypatch, "2026-10-15", signals=False)  # 같은 국면 — 그대로
    assert 경고(줄) == []
    _지수값(c, "KOSPI", "2026-10-16", 150.0)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-16", signals=False)  # 갈아타 파는 날
    assert 경고(줄) == [] and 기록["accounts"]["X"]["etf"]["action"] == "sell"
    assert c.q("SELECT COUNT(*) FROM paper_decisions WHERE account_id IN ('X', 'Y') AND kind = 'skip'") == [(0,)]
    assert all("ETF" not in x["text"] for x in desk.checks(halted=False, paper=기록, broker=None, recon_bad=[],
                                                          warnings=0))  # fmt: skip


def test_경고_술어() -> None:
    assert et.warning({"etf": {"action": None, "skip": "이 계좌의 시장이 아님"}}) is None
    assert et.warning({"etf": {"action": "buy"}, "buy": {"skipped": et.ALREADY_OPEN}}) is None
    assert et.warning({"etf": {"action": "sell", "skip": None}, "buy": {"skipped": "살 것 없음"}}) is None
    assert et.warning({"etf": {"action": "buy"}, "buy": {"skipped": "환율 없음"}}) == ("normal", "사지 못함 — 환율 없음")
    assert et.warning({"etf": {"action": "hold", "skip": "끊김"}}) == ("high", "멈춤 — 끊김")
    assert et.warning({"etf": {"action": "hold", "skip": None}}) is None



# ── 25.2171 (25.2170 교차검증 권고) ──────────────────────────────────────────


def _든(*sids: int) -> dict[int, pp.Position]:
    return {s: pp.Position(s, "KRW", 10, 300_000.0) for s in sids}


def test_연결된_보유는_다른_몫이_끊겨도_그대로_판정한다() -> None:
    """지수 ETF 를 든 채 인버스 연결만 끊긴 날 — 보유를 인버스로 읽으면 상승 국면에 전량 매도가 나간다"""
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    c.conn.execute("UPDATE etfs SET stock_id = NULL WHERE name = 'KODEX 인버스'")
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", _든(KODEX200), set())
    assert (p["held"], p["action"], p["sells"], p.get("skip")) == ("long", "hold", {}, None)
    assert p["tactical_missing"] == ["inverse"] and et.warning({"etf": p}) is None
    # 하락으로 바뀐 날 — 연결된 지수 ETF 는 평소대로 판다(멈춤 아님). 다음 실행의 인버스는 '사지 못함'
    _지수값(c, "KOSPI", "2026-10-15", 150.0)
    p = paper_etf.plan(c, "X", "KR", "2026-10-15", _든(KODEX200), set())
    assert (p["action"], list(p["sells"]), p.get("skip")) == ("sell", [KODEX200], None)
    p = paper_etf.plan(c, "X", "KR", "2026-10-15", {}, set())
    assert (p["action"], p["buy_role"], p["skip"]) == ("buy", None, "인버스가 종목표에 이어지지 않음 — 이름 대조 확인")


def test_보유_여럿이거나_두_몫이_다_끊기면_팔지_않고_멈춘다() -> None:
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    c.conn.execute("UPDATE etfs SET stock_id = NULL WHERE name = 'KODEX 200'")
    # 보유 둘(모르는 것 하나 + 연결된 인버스) — 엔진이 만든 꼴이 아니지만 연결이 끊긴 날은 정리하지 않는다
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", _든(KODEX200, INVERSE), set())
    assert (p["action"], p["sells"]) == ("hold", {})
    assert p["skip"] == "든 ETF 의 종목표 연결이 끊김 — 팔지 않음, 이름 대조 확인"
    # 보유 하나, 두 몫이 다 끊김 — 어느 몫인지 모른다
    c.conn.execute("UPDATE etfs SET stock_id = NULL WHERE name = 'KODEX 인버스'")
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", _든(INVERSE), set())
    assert (p["action"], p["sells"], p["tactical_missing"]) == ("hold", {}, ["long", "inverse"])
    assert p["held"] == "?" and p["skip"] == "든 ETF 의 종목표 연결이 끊김 — 팔지 않음, 이름 대조 확인"
    # 하락 국면이어도 팔지 않는다 — 끊긴[0](지수 ETF)으로 읽으면 인버스 보유를 판다
    _지수값(c, "KOSPI", "2026-10-15", 150.0)
    assert paper_etf.plan(c, "X", "KR", "2026-10-15", _든(INVERSE), set())["sells"] == {}


def test_주식_신호가_묵은_날에도_ETF_는_판정대로_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`allocate` 거짓(신호 묵음·추천 못 읽음) — 종목 계좌는 배분하지 않고, ETF 전술은 지수로 판정해 산다(25.2171)"""
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14", allocate=False)
    assert [r[:2] for r in _주문(c)] == [(KODEX200, "buy")]
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B'") == [(0,)]
    assert not [x for x in 줄 if "국내ETF 사지 못함" in x]
    # 추천을 못 읽은 날 운영이 넘기는 꼴(settings None·신호 없음, daily.py `추천_오류`)도 같다
    c2 = _계좌()
    _지수(c2, "KOSPI", "2026-10-14")
    _시세(c2, KODEX200, "2026-10-14", 30_000, 30_000)
    줄, _ = _하루(c2, monkeypatch, "2026-10-14", signals=False, allocate=False, settings=None)
    assert [r[:2] for r in _주문(c2)] == [(KODEX200, "buy")]
    assert c2.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B'") == [(0,)]


def test_미국_ETF_는_환율이_없으면_사지_않고_알린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`allocate` 에서 뗀 뒤(25.2171) 달러 계좌를 지키는 막이는 `buy_order` 의 '환율 없음' 하나다(25.2172)"""
    c = _계좌(etf=("Y",))
    c.conn.execute("DELETE FROM fx_rates")
    _지수(c, "SP500", "2026-10-14")
    _시세(c, SPY, "2026-10-14", 500, 500, "USD")
    p = paper_etf.plan(c, "Y", "US", "2026-10-14", {}, set())
    assert paper_etf.buy_order(c, p, market="US", valuation=_평가(1_000_000), open_buys=[], fx_rate=None,
                               trade_date="2026-10-15") == ([], {"skipped": "환율 없음"})  # fmt: skip
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14", market="US", signals=False, allocate=False)
    assert _주문(c, "Y") == [] and 기록["accounts"]["Y"]["buy"] == {"skipped": "환율 없음"}
    assert "⚠️ 가상 미국ETF 사지 못함 — 환율 없음" in 줄 and "step_error" not in 기록["accounts"]["Y"]


def test_국면_근거는_모름_날도_웹_근거표_꼴() -> None:
    from batch.services import criteria as crit

    for r in (et.Regime("up", "2026-11-06", 110.0, 100.0, 0.02), et.Regime("unknown", None, None, None, None),
              et.Regime("unknown", "2026-10-01", 100.0, None, None)):  # fmt: skip
        줄 = paper_etf.criteria(r)
        assert 줄 and all(crit.displayable(x) for x in 줄), 줄



# ── 25.2173 (25.2172 교차검증 참고 셋) ───────────────────────────────────────


def test_ETF_종가가_지수_날짜보다_묵으면_사지_않고_알린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """위험 점검의 묵음 막이(5거래일) 안쪽이라도 — ETF 와 지수는 같은 장 값이어야 한다"""
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    _시세(c, KODEX200, "2026-10-13", 30_000, 30_000)  # 하루 묵음(10-14 ETF 수집이 빠진 날)
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", {}, set())
    assert paper_etf.buy_order(c, p, market="KR", valuation=_평가(1_000_000), open_buys=[], fx_rate=None,
                               trade_date="2026-10-15") == ([], {"skipped": "ETF 시세 묵음(종가 2026-10-13, 지수 2026-10-14)"})  # fmt: skip
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14", signals=False)
    assert _주문(c) == [] and "⚠️ 가상 국내ETF 사지 못함 — ETF 시세 묵음(종가 2026-10-13, 지수 2026-10-14)" in 줄
    # 같은 날 값이면 산다 — 경계
    _시세(c, KODEX200, "2026-10-14", 30_000, 30_000)
    주문, _ = paper_etf.buy_order(c, p, market="KR", valuation=_평가(1_000_000), open_buys=[], fx_rate=None,
                                trade_date="2026-10-15")  # fmt: skip
    assert [(o["stock_id"], o["ref_date"]) for o in 주문] == [(KODEX200, "2026-10-14")]


def test_이미_열린_매수가_환율_없음보다_먼저() -> None:
    """같은 날 다시 돈 미국 실행 — 앞 실행이 매수를 냈고 뒤 실행에만 환율이 묵으면 경고가 아니라 이미 냄"""
    c = _계좌(etf=("Y",))
    _지수(c, "SP500", "2026-10-14")
    _시세(c, SPY, "2026-10-14", 500, 500, "USD")
    p = paper_etf.plan(c, "Y", "US", "2026-10-14", {}, set())
    열림 = pp.Order(id=9, account_id="Y", stock_id=SPY, side="buy", order_type="market", limit_price=None,
                    quantity=1, currency="USD", fill_session="2026-10-15", ref_price=500.0)  # fmt: skip
    주문, 기록 = paper_etf.buy_order(c, p, market="US", valuation=_평가(1_000_000), open_buys=[열림], fx_rate=None,
                                   trade_date="2026-10-15")  # fmt: skip
    assert (주문, 기록) == ([], {"skipped": et.ALREADY_OPEN})
    assert et.warning({"etf": {"action": "buy"}, "buy": 기록}) is None


def test_보유_없이_두_몫이_다_끊기면_사지_못함(monkeypatch: pytest.MonkeyPatch) -> None:
    """멈춤(high)은 든 보유가 있을 때만 — 보유가 없으면 사야 할 몫을 못 사는 날(normal)"""
    c = _계좌()
    _지수(c, "KOSPI", "2026-10-14")
    c.conn.execute("UPDATE etfs SET stock_id = NULL")
    p = paper_etf.plan(c, "X", "KR", "2026-10-14", {}, set())
    assert (p["action"], p["buy_role"], p["sells"]) == ("buy", None, {})
    assert p["skip"] == "지수 ETF가 종목표에 이어지지 않음 — 이름 대조 확인"
    assert p["tactical_missing"] == ["long", "inverse"]
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14", signals=False)
    assert et.warning(기록["accounts"]["X"]) == ("normal", "사지 못함 — 지수 ETF가 종목표에 이어지지 않음 — 이름 대조 확인")
    assert "⚠️ 가상 국내ETF 사지 못함 — 지수 ETF가 종목표에 이어지지 않음 — 이름 대조 확인" in 줄
    assert not [x for x in 줄 if "멈춤" in x]
