"""KIS모의투자 미국 — 예약주문 (docs/paper.md 15장, docs/infra.md 25.1485).

- 응답 읽기(공식 예제 칸 이름) · 예약주문 몸통·tr_id · 잔고는 거래소마다 묻고 겹침을 뺀다
- `plan(currency="USD")`: 시장가가 없어 종가 ± `US_MARKETABLE_PCT` 지정가, 종가가 없으면 내지 않는다
- 배치 흐름: 예약 접수 시간(KST 10:00 ~ 개장 10분 전, 16:30~16:45 제외), 거래소 코드 없는 종목 빼기,
  예약번호는 글로만 두고 다음 날 날·종목·방향·수량으로 맞추기, 국내 줄과 `broker` 로 갈림
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from batch.sources.kis import KisFailed
from tests.test_kis_vts_1475 import _o, _세션, 키
from tests.test_paper_job_1445 import Client

# ── 응답 읽기·세션 ────────────────────────────────────────────────────────────


def test_parse_us_shapes() -> None:
    assert kv.parse_us_reservation({"rt_cd": "0", "output": {"OVRS_RSVN_ODNO": "0030001234"}}) == kv.Accepted(
        "0030001234", None)
    with pytest.raises(kv.NoResponse):  # 성공인데 번호가 없으면 '모름' — 거부로 닫지 않는다
        kv.parse_us_reservation({"rt_cd": "0", "output": {}})
    with pytest.raises(kv.NoResponse):  # ODNO 는 거래소 전송 조직번호 — 예약번호로 읽지 않는다 (검증 [참고] 2)
        kv.parse_us_reservation({"rt_cd": "0", "output": {"ODNO": "01790"}})
    with pytest.raises(KisFailed, match="APBK1234"):
        kv.parse_us_reservation({"rt_cd": "1", "msg_cd": "APBK1234", "msg1": "주문가능금액 부족"})
    ex = kv.parse_us_executions({"rt_cd": "0", "output": [
        {"ord_dt": "20261016", "odno": "77", "sll_buy_dvsn_cd": "02", "pdno": "AAPL", "ft_ord_qty": "3",
         "ft_ccld_qty": "3", "ft_ccld_unpr3": "201.5000", "rjct_rson": "", "rvse_cncl_dvsn": "00"},
        {"ord_dt": "20261016", "odno": "78", "sll_buy_dvsn_cd": "01", "pdno": "IBM", "ft_ord_qty": "2",
         "ft_ccld_qty": "0", "ft_ccld_unpr3": "0", "rjct_rson": "잔고 부족", "rvse_cncl_dvsn": "00"},
        {"ord_dt": "20261016", "odno": "79", "sll_buy_dvsn_cd": "02", "pdno": "MSFT", "ft_ord_qty": "1",
         "ft_ccld_qty": "0", "rjct_rson": "0", "rvse_cncl_dvsn": "00"},
        {"odno": ""}]})  # fmt: skip
    # 거부 사유 "0" 은 사유 없음 — 안 찬 주문을 거부로 세지 않는다 (검증 [참고] 1)
    assert ex == [kv.Execution("20261016", "77", "AAPL", "buy", 3, 3, 201.5, 0, False),
                  kv.Execution("20261016", "78", "IBM", "sell", 2, 0, None, 2, False),
                  kv.Execution("20261016", "79", "MSFT", "buy", 1, 0, None, 0, False)]  # fmt: skip
    assert kv.parse_us_balance({"rt_cd": "0", "output1": [
        {"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "3", "ord_psbl_qty": "3", "pchs_avg_pric": "201.5"},
        {"ovrs_pdno": "IBM", "ovrs_cblc_qty": "0"}]}) == [kv.Holding("AAPL", 3, 3, 201.5)]  # fmt: skip


def test_session_us_reservation_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    s, http = _세션(monkeypatch, [({"rt_cd": "0", "output": {"OVRS_RSVN_ODNO": "9"}}, ""),
                                 ({"rt_cd": "0", "output": {"OVRS_RSVN_ODNO": "10"}}, "")])  # fmt: skip
    assert s.order_us("buy", "AAPL", "NASD", 3, 201.456).order_no == "9"
    url, kw = http.sent[0]
    assert url == kv.BASE + kv.US_RESV_PATH and kw["headers"]["tr_id"] == "VTTT3014U"
    assert kw["json"] == {"CANO": "12345678", "ACNT_PRDT_CD": "01", "PDNO": "AAPL", "OVRS_EXCG_CD": "NASD",
                          "FT_ORD_QTY": "3", "FT_ORD_UNPR3": "201.46"}  # fmt: skip
    s.order_us("sell", "IBM", "NYSE", 2, 180.0)
    assert http.sent[1][1]["headers"]["tr_id"] == "VTTT3016U"
    # 모르는 거래소·0 단가는 보내지 않는다
    for 인자 in (("buy", "AAPL", "XNAS", 1, 1.0), ("buy", "AAPL", "NASD", 1, 0.0)):
        with pytest.raises(KisFailed):
            s.order_us(*인자)
    assert len(http.sent) == 2


def test_session_us_balance_each_exchange_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    """모의는 거래소마다 따로 받는다 `[확인필요]` — 거래소마다 묻고, 같은 종목이 두 번 오면 한 번만."""
    줄 = {"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "3", "ord_psbl_qty": "3", "pchs_avg_pric": "201"}
    s, http = _세션(monkeypatch, [({"rt_cd": "0", "output1": [줄]}, ""),
                                 ({"rt_cd": "0", "output1": [줄]}, ""),
                                 ({"rt_cd": "0", "output1": [{"ovrs_pdno": "IBM", "ovrs_cblc_qty": "2",
                                                              "ord_psbl_qty": "2"}]}, "")])  # fmt: skip
    보유, 합 = s.balance_us()
    assert [(h.code, h.quantity) for h in 보유] == [("AAPL", 3), ("IBM", 2)] and 합 == {"cash": None, "total": None}
    assert [kw["params"]["OVRS_EXCG_CD"] for _u, kw in http.sent] == ["AMEX", "NASD", "NYSE"]
    assert all(kw["headers"]["tr_id"] == "VTTS3012R" for _u, kw in http.sent)


# ── 무엇을 낼지 (미국) ──────────────────────────────────────────────────────


def test_plan_usd_marketable_limits() -> None:
    코드 = {2: "AAPL", 4: "IBM", 5: "NOPX"}
    낼것, 안냄 = bm.plan(
        [_o(10, 2, "buy", 3, 200.004), _o(11, 4, "sell", 2, None), _o(12, 4, "buy", 1, None), _o(13, 5, "buy", 1, None)],
        코드, internal_qty={4: 2}, broker={4: (2, 2), 2: (1, 1)}, bought_before={2, 4}, already=set(),
        cleaned_today=set(), halted=False, currency="USD", closes={2: 201.0, 4: 180.05},
    )  # fmt: skip
    # 정리(2: 증권사 1 · 내부 0)는 201 × 0.9 = 180.9 아래로, 시장가 매도는 180.05 × 0.9 = 162.045 → 162.04,
    # 시장가 매수는 180.05 × 1.1 = 198.055 → 198.06, 지정가 매수는 센트로 내림. 종가 없는 5 는 내지 않는다
    assert [(x.reason, x.stock_id, x.side, x.quantity, x.price) for x in 낼것] == [
        ("정리", 2, "sell", 1, 180.9), ("베낌", 4, "sell", 2, 162.04), ("베낌", 2, "buy", 3, 200.0),
        ("베낌", 4, "buy", 1, 198.06)]
    assert 안냄 == [(13, 5, "종가 없음 — 미국 시장가 대신 낼 지정가를 못 정함")]
    # 국내는 그대로 시장가(None)
    낼것, _ = bm.plan([_o(11, 4, "sell", 2, None)], 코드, {4: 2}, {4: (2, 2)}, {4}, already=set(),
                     cleaned_today=set(), halted=False)  # fmt: skip
    assert 낼것[0].price is None


# ── 배치 흐름 (미국) ──────────────────────────────────────────────────────────


class _UsSess:
    def __init__(self, holdings: list[kv.Holding] | None = None, executions: list[kv.Execution] | None = None) -> None:
        self.holdings, self.ex = holdings or [], executions or []
        self.orders: list[tuple] = []
        self.asked: list[tuple[str, str]] = []
        self.calls = 0

    def order(self, *_a: Any) -> kv.Accepted:
        pytest.fail("미국 배치가 국내 주문을 냈다")

    def executions(self, *_a: Any) -> Any:
        pytest.fail("미국 배치가 국내 체결을 물었다")

    def balance(self) -> Any:
        pytest.fail("미국 배치가 국내 잔고를 물었다")

    def order_us(self, side: str, code: str, exchange: str, q: int, price: float) -> kv.Accepted:
        self.orders.append((side, code, exchange, q, price))
        return kv.Accepted(f"R{len(self.orders)}", None)

    def executions_us(self, start: str, end: str) -> tuple[list[kv.Execution], bool]:
        self.asked.append((start, end))
        return self.ex, True

    def balance_us(self) -> tuple[list[kv.Holding], dict[str, float | None]]:
        return self.holdings, {"cash": None, "total": None}


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(job, "MIRROR_ACCOUNT", "B")
    c = Client()
    c.conn.execute("UPDATE stocks SET market = 'NASDAQ' WHERE id = 2")
    for sid, t, m in ((4, "IBM", "NYSE"), (5, "IEXQ", "IEX")):
        c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source,"
                       " fetched_at, asset_type) VALUES (?, ?, ?, 'US', ?, '전자', 'USD', 'active', 't', 't', 'stock')",
                       [sid, t, m, t])  # fmt: skip
    for sid, close in ((2, 201.0), (4, 180.05), (5, 10.0)):
        c.conn.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
                       " VALUES (?, '2026-10-15', ?, 'USD', 't', 't')", [sid, close])  # fmt: skip
    for oid, sid, side, typ, price, q in ((201, 2, "buy", "limit", 200.0, 3), (202, 5, "buy", "market", None, 1),
                                          (203, 4, "buy", "market", None, 1)):  # fmt: skip
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
            " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
            " VALUES (?, 'B', 'US', ?, ?, ?, ?, ?, 'USD', '2026-10-15', '2026-10-16', 'r', 1, 1, 't')",
            [oid, sid, side, typ, price, q],
        )  # fmt: skip
    return c


def _kst(hhmm: str, day: str = "2026-10-16") -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+09:00").astimezone(UTC)


def test_us_job_sends_reservations_and_keeps_kr_apart(c: Client) -> None:
    """10-16(금) 서머타임 — 개장 22:30 KST, 예약 마감 22:20. 미국 배치(21:27 KST)는 그 안이다."""
    s = _UsSess()
    기록 = job.run(c, "US", _kst("21:27"), session=s, clock=lambda: _kst("21:27"))
    # IEX 상장 5 는 KIS 거래소 코드가 없어 빠진다. 시장가 매수 4 는 180.05 × 1.1
    assert s.orders == [("buy", "AAPL", "NASD", 3, 200.0), ("buy", "IBM", "NYSE", 1, 198.06)]
    assert 기록["sent"] == {"accepted": 2} and 기록["not_sent"] == {"종목 코드 없음": 1}
    줄 = c.q("SELECT broker, market, paper_order_id, order_type, limit_price, broker_order_no, message, status"
             " FROM paper_broker_orders ORDER BY id")
    assert 줄 == [("kis_vts_us", "US", 201, "limit", 200.0, None, "예약번호 R1", "accepted"),
                 ("kis_vts_us", "US", 203, "limit", 198.06, None, "예약번호 R2", "accepted")]  # fmt: skip
    # 같은 날 국내 줄과 잔고 표 키가 겹치지 않는다
    assert c.q("SELECT broker, as_of FROM paper_broker_daily") == [("kis_vts_us", "2026-10-16")]
    job.run(c, "US", _kst("21:40"), session=s, clock=lambda: _kst("21:40"))
    assert len(s.orders) == 2  # 다시 돌아도 두 번 내지 않는다

    # 다음 거래일(10-19 월): 예약번호가 아닌 정규 주문번호로 체결이 온다 — 날·종목·방향·수량으로 맞춘다
    다음 = _UsSess(holdings=[kv.Holding("AAPL", 3, 3, 200.0)],
                 executions=[kv.Execution("20261016", "5501", "AAPL", "buy", 3, 3, 199.9, 0, False),
                             kv.Execution("20261016", "5502", "IBM", "buy", 1, 0, None, 0, True)])  # fmt: skip
    기록 = job.run(c, "US", _kst("21:27", "2026-10-19"), session=다음, clock=lambda: _kst("21:27", "2026-10-19"))
    assert 다음.asked == [("20261016", "20261018")] and 기록["reconciled"] == {"filled": 1, "unfilled": 1}
    # 체결 조회에 없는 미국 줄은 "예약 미전송" 으로 읽힌다 (검증 [권장] 1) — 10-19 에 낸 줄을 다음 거래일에 맞춘다
    c.conn.execute("UPDATE paper_orders SET fill_session = '2026-10-19', status = 'open' WHERE id = 202")
    c.conn.execute("UPDATE stocks SET market = 'NASDAQ' WHERE id = 5")
    job.run(c, "US", _kst("21:27", "2026-10-19"), session=_UsSess(), clock=lambda: _kst("21:27", "2026-10-19"))
    job.run(c, "US", _kst("21:27", "2026-10-20"), session=_UsSess(), clock=lambda: _kst("21:27", "2026-10-20"))
    assert c.q("SELECT status, message FROM paper_broker_orders WHERE paper_order_id = 202") == [
        ("unknown", bm.MISSING_US)]
    assert c.q("SELECT paper_order_id, status, broker_order_no, filled_qty, avg_price FROM paper_broker_orders"
               " WHERE trade_date = '2026-10-16' ORDER BY id") == [(201, "filled", "5501", 3, 199.9),
                                                                   (203, "unfilled", "5502", 0, None)]  # fmt: skip


def test_us_window(c: Client) -> None:
    # 09:30 KST 는 미국 현지로 전날 저녁 — 그날 개장이 지났으니 "마감 뒤"
    for 시각, 까닭 in (("22:25", "예약 접수 마감 뒤 — 보내지 않음"), ("09:30", "예약 접수 마감 뒤 — 보내지 않음"),
                     ("16:35", "예약 접수 정산 시간 — 보내지 않음")):  # fmt: skip
        s = _UsSess()
        assert job.run(c, "US", _kst(시각), session=s)["send"] == 까닭 and s.orders == []
    assert job._us_window(_kst("09:59"), datetime(2026, 10, 16).date()) == "예약 접수 시작 전 — 보내지 않음"
    assert job._us_window(_kst("10:00"), datetime(2026, 10, 16).date()) is None
    # 서머타임이 끝나면(11-02 월, 개장 23:30 KST) 마감은 23:20 — 22:25 는 된다
    assert job._us_window(_kst("22:25", "2026-11-02"), datetime(2026, 11, 2).date()) is None
    assert job._us_window(_kst("23:21", "2026-11-02"), datetime(2026, 11, 2).date()) == "예약 접수 마감 뒤 — 보내지 않음"


def test_weekly_reads_both_markets(c: Client) -> None:
    for 증권사, 날 in (("kis_vts", "2026-10-16"), ("kis_vts_us", "2026-10-16")):
        s = bm.compare([{"reason": "베낌", "status": "filled", "i_status": "filled", "filled_qty": 1, "quantity": 1,
                         "side": "buy", "trade_date": 날, "i_fill_date": 날, "avg_price": 101.0, "i_price": 100.0}])
        c.conn.execute("INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source,"
                       " fetched_at, summary_json) VALUES (?, ?, 'B', '[]', '{}', 'kis_vts', 't', ?)",
                       [증권사, 날, json.dumps(s)])  # fmt: skip
    줄 = job.summary_lines(c)
    assert 줄[0].startswith("[KIS모의투자 — B 주문 베끼기") and any(x.startswith("[KIS모의투자 — B 미국 주문 베끼기")
                                                                   for x in 줄)  # fmt: skip


def test_web_reads_both_broker_keys() -> None:
    """웹은 계산하지 않고 배치가 적은 줄을 증권사 칸으로 읽는다 — 칸 이름이 배치와 같아야 미국 칸이 보인다."""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "paper.ts").read_text(encoding="utf-8")
    assert f'BROKER_KEYS = {{ KR: "{job.BROKER}", US: "{job.BROKER_US}" }}' in 글
    assert "WHERE broker = ? ORDER BY as_of DESC LIMIT 1" in 글


def test_reconcile_same_qty_tranches_by_price() -> None:
    """미국 분할 회차 셋이 모두 2주 — 101달러 회차만 찼다. 날·종목·방향·수량만으로는 99달러 회차가 찬 것으로 뒤바뀌었다 (25.1490)."""
    rows = [{"id": i, "code": "AAPL", "side": "buy", "quantity": 2, "trade_date": "2026-10-16", "broker_order_no": None,
             "limit_price": p} for i, p in ((1, 101.0), (2, 100.0), (3, 99.0))]  # fmt: skip
    E = kv.Execution
    ex = [E("20261016", "903", "AAPL", "buy", 2, 0, None, 0, False, 99.0),
          E("20261016", "902", "AAPL", "buy", 2, 0, None, 0, False, 100.0),
          E("20261016", "901", "AAPL", "buy", 2, 2, 100.7, 0, False, 101.0)]  # fmt: skip
    got = {o.row_id: (o.status, o.order_no) for o in bm.reconcile(rows, ex, missing=bm.MISSING_US)}
    assert got == {1: ("filled", "901"), 2: ("unfilled", "902"), 3: ("unfilled", "903")}
    # 체결 조회가 단가를 안 주면(국내·칸 없음) 예전처럼 수량까지만 본다
    assert bm.reconcile(rows[:1], [E("20261016", "9", "AAPL", "buy", 2, 2, 100.7, 0, False)])[0].status == "filled"
    assert kv.parse_us_executions({"rt_cd": "0", "output": [
        {"ord_dt": "20261016", "odno": "1", "sll_buy_dvsn_cd": "02", "pdno": "AAPL", "ft_ord_qty": "2",
         "ft_ccld_qty": "0", "ft_ord_unpr3": "101.00000"}]})[0].price == 101.0  # fmt: skip
