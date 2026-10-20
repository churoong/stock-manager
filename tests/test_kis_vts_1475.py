"""증권사 모의투자 연결 (docs/paper.md 15장, docs/infra.md 25.1475).

- 실계좌로 새지 않는가: 도메인 상수·주소 검사·저장소에 실전 주문 tr_id 가 없는가·실전 키를 읽지 않는가
- 응답 읽기(공식 예제 칸 이름) · 무엇을 낼지(`plan`) · 어제 것 맞추기(`reconcile`)
- 배치 흐름: 실제 마이그레이션 위에서 가짜 세션으로 — 줄을 먼저 적고 보내고, 다시 돌아도 두 번 보내지 않는가
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import requests

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from batch.sources import kis
from batch.sources import kis_vts as kv
from batch.sources.kis import KisFailed
from tests.test_paper_job_1445 import Client

뿌리 = Path(__file__).resolve().parent.parent
키 = {"KIS_VTS_APP_KEY": "k", "KIS_VTS_APP_SECRET": "s", "KIS_VTS_ACCOUNT": "12345678-01"}


# ── 실계좌로 새지 않는가 ─────────────────────────────────────────────────────


def test_mock_domain_only() -> None:
    assert kv.BASE == "https://openapivts.koreainvestment.com:29443"
    assert kv.BASE != kis.BASE
    assert kv._check_url(kv.BASE + kv.ORDER_PATH, kv.TR_BUY)
    with pytest.raises(KisFailed):
        kv._check_url(kis.BASE + kv.ORDER_PATH, kv.TR_BUY)
    with pytest.raises(KisFailed):  # 앞만 같은 다른 호스트
        kv._check_url(kv.BASE + ".evil.example/uapi", kv.TR_BUY)
    with pytest.raises(KisFailed):  # 실전 tr_id
        kv._check_url(kv.BASE + kv.ORDER_PATH, "T" + kv.TR_BUY[1:])


def test_no_live_order_tr_id_in_repo() -> None:
    """주문·정정(끝이 U 인 tr_id)은 모의(V…)만 저장소에 있다 — 실전 주문 경로를 만들지 않는다(CLAUDE.md 단계 관문)."""
    본 = []
    for d in ("batch", "scripts", "web/lib", "web/app"):
        for p in (뿌리 / d).rglob("*"):
            if p.is_file() and p.suffix in (".py", ".ts", ".tsx") and "node_modules" not in p.parts:
                본 += [(p.name, m) for m in re.findall(r"\b[A-Z]{4}\d{4}U\b", p.read_text(encoding="utf-8"))]
    assert 본, "tr_id 를 하나도 못 찾았다 — 훑기가 망가졌다"
    assert all(m.startswith("V") for _f, m in 본), [x for x in 본 if not x[1].startswith("V")]


def test_reads_only_mock_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in 키:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KIS_APP_KEY", "real")
    monkeypatch.setenv("KIS_APP_SECRET", "real")
    assert not kv.configured()
    assert kv.configured(키)
    assert kv.account({"KIS_VTS_ACCOUNT": "1234567801"}) == ("12345678", "01")
    # 모의투자 화면은 8자리만 보여 준다 — 상품코드는 종합계좌 01 (25.1485)
    assert kv.account({"KIS_VTS_ACCOUNT": "1234-5678"}) == ("12345678", "01")
    assert kv.account({"KIS_VTS_ACCOUNT": "1234567"}) is None and kv.account({"KIS_VTS_ACCOUNT": "123456780"}) is None


class _Http:
    """요청을 적고 정해 둔 응답을 준다."""

    def __init__(self, replies: list[tuple[dict, str]]) -> None:
        self.replies, self.sent = list(replies), []

    def _r(self, url: str, kw: dict) -> Any:
        self.sent.append((url, kw))
        body, cont = self.replies.pop(0)
        return type("R", (), {"status_code": 200, "json": lambda _s: body, "headers": {"tr_cont": cont}})()

    def post(self, url: str, **kw: Any) -> Any:
        return self._r(url, kw)

    def get(self, url: str, **kw: Any) -> Any:
        return self._r(url, kw)


def _세션(monkeypatch: pytest.MonkeyPatch, replies: list[tuple[dict, str]]) -> tuple[kv.Session, _Http]:
    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    받은: dict[str, Any] = {}

    def 토큰(_c: Any, _n: Any, **kw: Any) -> tuple[str, bool]:
        받은.update(kw)
        return "tok", False

    monkeypatch.setattr(kis, "access_token", 토큰)
    http = _Http(replies)
    s = kv.Session(object(), http=http, sleep=lambda _x: None)
    assert 받은["base"] == kv.BASE and 받은["name"] == "kis_vts" and 받은["key_env"] == "KIS_VTS_APP_KEY"
    return s, http


def test_session_sends_mock_order_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    s, http = _세션(monkeypatch, [({"rt_cd": "0", "output": {"ODNO": "0000123", "ORD_TMD": "084501"}}, ""),
                                 ({"rt_cd": "0", "output": {"ODNO": "0000124"}}, "")])  # fmt: skip
    assert s.order("buy", "005930", 3, 9_800.0) == kv.Accepted("0000123", "084501")
    url, kw = http.sent[0]
    assert url == kv.BASE + kv.ORDER_PATH and kw["headers"]["tr_id"] == "VTTC0012U"
    assert kw["json"] | {} == {"CANO": "12345678", "ACNT_PRDT_CD": "01", "PDNO": "005930", "ORD_DVSN": "00",
                               "ORD_QTY": "3", "ORD_UNPR": "9800", "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "",
                               "CNDT_PRIC": ""}  # fmt: skip
    s.order("sell", "005930", 2, None)  # 시장가 매도
    _u, kw = http.sent[1]
    assert kw["headers"]["tr_id"] == "VTTC0011U"
    assert (kw["json"]["ORD_DVSN"], kw["json"]["ORD_UNPR"], kw["json"]["SLL_TYPE"]) == ("01", "0", "01")
    assert "KIS_APP_KEY" not in json.dumps(kw["headers"]) and kw["headers"]["appkey"] == "k"


def test_balance_follows_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    쪽1 = {"rt_cd": "0", "output1": [{"pdno": "005930", "hldg_qty": "3", "ord_psbl_qty": "3", "pchs_avg_pric": "9800"}],
          "output2": [{"dnca_tot_amt": "1000000", "tot_evlu_amt": "1029400"}], "ctx_area_fk100": "f", "ctx_area_nk100": "n"}
    쪽2 = {"rt_cd": "0", "output1": [{"pdno": "000660", "hldg_qty": "0", "ord_psbl_qty": "0"}], "output2": [{}]}
    s, http = _세션(monkeypatch, [(쪽1, "M"), (쪽2, "")])  # fmt: skip
    보유, 합 = s.balance()
    assert 보유 == [kv.Holding("005930", 3, 3, 9800.0)] and 합 == {"cash": 1_000_000.0, "total": 1_029_400.0,
                                                               "holdings_value": None, "cash_d2": None}  # fmt: skip
    assert http.sent[1][1]["params"]["CTX_AREA_NK100"] == "n" and http.sent[1][1]["headers"]["tr_cont"] == "N"


def test_network_errors_hide_url_and_mean_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """연결 오류 문장에는 요청 주소(조회는 `?CANO=` 계좌번호)가 실린다 — 이름만 남기고, 잇지도 않는다 (검증 [권장] 2)."""
    s, http = _세션(monkeypatch, [])

    def 끊김(url: str, **kw: Any) -> Any:
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}?CANO=12345678")

    http.get = 끊김  # type: ignore[method-assign]
    with pytest.raises(kv.NoResponse) as e:
        s.balance()
    assert "12345678" not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__
    # JSON 이 아닌 응답(게이트웨이 502 등)·주문번호 없는 성공도 '모름' — 거부로 닫지 않는다 (검증 [권장] 3)
    s, http = _세션(monkeypatch, [])
    http.post = lambda url, **kw: type("R", (), {"status_code": 502, "json": lambda _s: (_ for _ in ()).throw(ValueError()),
                                                  "headers": {}})()  # type: ignore[method-assign]  # fmt: skip
    with pytest.raises(kv.NoResponse):
        s.order("buy", "005930", 1, 9_800.0)
    with pytest.raises(kv.NoResponse):
        kv.parse_order({"rt_cd": "0", "output": {}})


def test_parse_errors_keep_kis_code_only() -> None:
    with pytest.raises(KisFailed, match="APBK0919 주문가능금액을 초과"):
        kv.parse_order({"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "주문가능금액을 초과 했습니다"})
    ex = kv.parse_executions({"rt_cd": "0", "output1": [
        {"ord_dt": "20261016", "odno": "1", "pdno": "005930", "sll_buy_dvsn_cd": "02", "ord_qty": "3",
         "tot_ccld_qty": "2", "avg_prvs": "9790", "rjct_qty": "0", "cncl_yn": "N"},
        {"odno": ""}]})  # fmt: skip
    assert ex == [kv.Execution("20261016", "1", "005930", "buy", 3, 2, 9790.0, 0, False)]


def test_국내_번호_없는_줄도_주문_단가로_회차를_가른다() -> None:
    """같은 종목·같은 수량 회차 둘이 모두 응답 없이(번호 없이) 남았다 — 9,800 회차만 찼는데 9,600 줄이 "체결 @9,800" 이
    되던 것 (25.1566, KIS 경로 감사 [참고] 1). 시장가(단가 0)는 단가를 보지 않는다"""
    ex = kv.parse_executions({"rt_cd": "0", "output1": [
        {"ord_dt": "20261016", "odno": "7", "pdno": "005930", "sll_buy_dvsn_cd": "02", "ord_qty": "3",
         "tot_ccld_qty": "3", "avg_prvs": "9800", "rjct_qty": "0", "cncl_yn": "N", "ord_unpr": "9800"},
        {"ord_dt": "20261016", "odno": "8", "pdno": "005930", "sll_buy_dvsn_cd": "02", "ord_qty": "3",
         "tot_ccld_qty": "0", "avg_prvs": "0", "rjct_qty": "0", "cncl_yn": "N", "ord_unpr": "9600"},
        {"ord_dt": "20261016", "odno": "9", "pdno": "000660", "sll_buy_dvsn_cd": "01", "ord_qty": "1",
         "tot_ccld_qty": "1", "avg_prvs": "100", "rjct_qty": "0", "cncl_yn": "N", "ord_unpr": "0"}]})  # fmt: skip
    assert [e.price for e in ex] == [9800.0, 9600.0, None]
    줄 = [{"id": 1, "trade_date": "2026-10-16", "code": "005930", "side": "buy", "quantity": 3, "limit_price": 9600.0,
           "broker_order_no": None},
          {"id": 2, "trade_date": "2026-10-16", "code": "005930", "side": "buy", "quantity": 3, "limit_price": 9800.0,
           "broker_order_no": None},
          {"id": 3, "trade_date": "2026-10-16", "code": "000660", "side": "sell", "quantity": 1, "limit_price": None,
           "broker_order_no": None}]  # fmt: skip
    결과 = {o.row_id: (o.status, o.avg_price) for o in bm.reconcile(줄, ex)}
    assert 결과 == {1: ("unfilled", None), 2: ("filled", 9800.0), 3: ("filled", 100.0)}


# ── 무엇을 낼지 ───────────────────────────────────────────────────────────────


def _o(oid: int, sid: int, side: str, q: int, price: float | None) -> dict[str, Any]:
    return {"id": oid, "stock_id": sid, "side": side, "order_type": "market" if price is None else "limit",
            "limit_price": price, "quantity": q}  # fmt: skip


def test_하루_상한은_같은_날_앞_실행이_낸_줄까지_센다() -> None:
    """상한 3, 앞 실행이 이미 2줄 — 이번엔 1줄만 (25.1567, KIS 경로 감사 [참고] 3 — 예전엔 실행마다 3)"""
    주문 = [_o(20 + i, 1, "buy", 1, 9_000.0 + i) for i in range(4)]
    낼것, 안냄 = bm.plan(주문, {1: "005930"}, internal_qty={}, broker={}, bought_before=set(), already=set(),
                       cleaned_today=set(), halted=False, max_orders=3, sent_today=2)  # fmt: skip
    assert len(낼것) == 1 and sum("하루 주문 상한 3" in w for _o2, _s, w in 안냄) == 3
    낼것, _ = bm.plan(주문, {1: "005930"}, internal_qty={}, broker={}, bought_before=set(), already=set(),
                     cleaned_today=set(), halted=False, max_orders=3, sent_today=5)  # fmt: skip
    assert 낼것 == []


def test_plan_sells_first_clamps_and_cleans_only_ours() -> None:
    코드 = {1: "005930", 3: "000660", 9: "035720"}
    낼것, 안냄 = bm.plan(
        [_o(10, 1, "buy", 3, 9_805.0), _o(11, 3, "sell", 5, None), _o(12, 1, "buy", 2, 9_600.0)], 코드,
        internal_qty={3: 5}, broker={3: (4, 4), 9: (7, 7), 1: (2, 2)}, bought_before={1, 3},
        already={12}, cleaned_today=set(), halted=False,
    )  # fmt: skip
    # 1 은 증권사 2 · 내부 0 → 우리가 산 종목이라 정리 2. 9 는 산 적 없어(손으로 산 것) 두고, 3 매도는 증권사에 4 뿐이라 4
    assert [(x.reason, x.stock_id, x.side, x.quantity, x.price) for x in 낼것] == [
        ("정리", 1, "sell", 2, None), ("베낌", 3, "sell", 4, None), ("베낌", 1, "buy", 3, 9_800.0)]
    assert 안냄 == []
    # 멈춤이면 아무것도
    낼것, 안냄 = bm.plan([_o(10, 1, "buy", 3, 9_800.0)], 코드, {}, {}, set(), already=set(), cleaned_today=set(),
                       halted=True)  # fmt: skip
    assert 낼것 == [] and 안냄 == [(10, 1, "멈춤 스위치가 켜져 있음")]
    # 팔 것이 없으면 까닭, 상한을 넘으면 뒤의 매수부터 뺀다
    낼것, 안냄 = bm.plan([_o(11, 3, "sell", 5, None), _o(20, 1, "buy", 1, 9_800.0), _o(21, 1, "buy", 1, 9_700.0)],
                       코드, {}, {}, set(), already=set(), cleaned_today=set(), halted=False, max_orders=1)  # fmt: skip
    assert [x.paper_order_id for x in 낼것] == [20]
    assert 안냄 == [(11, 3, "증권사 모의에 팔 수량 없음"), (21, 1, "하루 주문 상한 1")]


def test_plan_cleanup_spares_todays_buys_and_frozen() -> None:
    """같은 날 다시 돌면 시가에 찬 오늘 매수가 증권사에만 있는 것으로 보인다 — 팔지 않는다 (검증 [필수] 1).
    기업행위 경계 종목(분할로 증권사 수량만 늘어남)도 팔지 않는다"""
    코드 = {1: "005930", 3: "000660"}
    낼것, _ = bm.plan([], 코드, internal_qty={}, broker={1: (3, 3), 3: (10, 10)}, bought_before={1, 3}, already=set(),
                     cleaned_today=set(), halted=False, bought_today={1: 3}, frozen={3})  # fmt: skip
    assert 낼것 == []
    낼것, _ = bm.plan([], 코드, internal_qty={}, broker={1: (5, 5)}, bought_before={1}, already=set(),
                     cleaned_today=set(), halted=False, bought_today={1: 3})  # fmt: skip
    assert [(x.reason, x.quantity) for x in 낼것] == [("정리", 2)]


def test_reconcile_statuses() -> None:
    E = kv.Execution
    rows = [
        {"id": 1, "code": "005930", "side": "buy", "quantity": 3, "trade_date": "2026-10-16", "broker_order_no": "7"},
        {"id": 2, "code": "000660", "side": "sell", "quantity": 5, "trade_date": "2026-10-16", "broker_order_no": None},
        {"id": 3, "code": "005930", "side": "buy", "quantity": 2, "trade_date": "2026-10-16", "broker_order_no": "9"},
        {"id": 4, "code": "005930", "side": "buy", "quantity": 1, "trade_date": "2026-10-16", "broker_order_no": None},
        {"id": 5, "code": "005930", "side": "buy", "quantity": 4, "trade_date": "2026-10-16", "broker_order_no": "11"},
    ]  # fmt: skip
    ex = [E("20261016", "7", "005930", "buy", 3, 2, 9_790.0, 0, False),
          E("20261016", "8", "000660", "sell", 5, 5, 20_100.0, 0, False),
          E("20261016", "9", "005930", "buy", 2, 0, None, 2, False),
          E("20261016", "11", "005930", "buy", 4, 0, None, 0, True)]  # fmt: skip
    got = {o.row_id: (o.status, o.order_no, o.filled, o.avg_price, o.message) for o in bm.reconcile(rows, ex)}
    assert got == {1: ("partial", "7", 2, 9_790.0, None), 2: ("filled", "8", 5, 20_100.0, None),
                   3: ("rejected", "9", 0, None, None), 4: ("unknown", None, 0, None, "체결 조회에 없음"),
                   5: ("unfilled", "11", 0, None, "취소됨")}  # fmt: skip
    # 체결 조회를 다 못 받았으면 못 찾은 줄은 닫지 않는다 (검증 [권장] 5), 주문번호 앞 0 은 떼고 견준다
    got = bm.reconcile(rows[:1] + rows[3:4], [E("20261016", "0000007", "005930", "buy", 3, 3, 9_800.0, 0, False)],
                       complete=False)  # fmt: skip
    assert [(o.row_id, o.status) for o in got] == [(1, "filled")]


# ── 배치 흐름 ─────────────────────────────────────────────────────────────────


class _Sess:
    def __init__(self, holdings: list[kv.Holding] | None = None, executions: list[kv.Execution] | None = None,
                 fail: dict[str, Exception] | None = None) -> None:  # fmt: skip
        self.holdings, self.ex, self.fail = holdings or [], executions or [], fail or {}
        self.orders: list[tuple] = []
        self.asked: list[tuple[str, str]] = []
        self.calls = 0

    def order(self, side: str, code: str, q: int, price: float | None) -> kv.Accepted:
        self.orders.append((side, code, q, price))
        if code in self.fail:
            raise self.fail[code]
        return kv.Accepted(f"N{len(self.orders)}", "084500")

    def executions(self, start: str, end: str) -> tuple[list[kv.Execution], bool]:
        self.asked.append((start, end))
        return self.ex, True

    def balance(self) -> tuple[list[kv.Holding], dict[str, float | None]]:
        return self.holdings, {"cash": 1_000_000.0, "total": 1_000_000.0}

    def present_us(self, won: bool = False) -> dict:
        """국내 배치가 계좌 전체 원화 총액을 내려고 읽는 미국 체결기준현재잔고 — 보유 없음·달러 예수금 0 (25.1497)."""
        return {"output1": [], "output2": [{"crcy_cd": "USD", "frcr_dncl_amt_2": "0", "frst_bltn_exrt": "1350"}],
                "output3": {}}


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    # 이 파일의 흐름 시험은 B 의 주문으로 짓는다 — 운영은 H 를 베낀다(25.1482, `test_mirror_account_is_h`)
    monkeypatch.setattr(job, "MIRROR_ACCOUNT", "B")
    c = Client()
    for oid, sid, side, typ, price, q, 회차 in ((101, 1, "buy", "limit", 9_800, 3, 1),
                                               (102, 3, "sell", "market", None, 2, 0),
                                               (103, 1, "buy", "limit", 9_600, 2, 2)):  # fmt: skip
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
            " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
            " VALUES (?, 'B', 'KR', ?, ?, ?, ?, ?, 'KRW', '2026-10-15', '2026-10-16', 'r', 1, ?, 't')",
            [oid, sid, side, typ, price, q, 회차],
        )  # fmt: skip
    return c


def _at(hhmm: str, day: str = "2026-10-16") -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+09:00").astimezone(UTC)


def test_job_sends_once_and_records(c: Client) -> None:
    s = _Sess(holdings=[kv.Holding("000660", 2, 2, 20_000.0)])
    # 내부도 3 을 2 주 들고 있다
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                   " decided_on, fill_session, reason, rule_version, status, created_at)"
                   " VALUES (90, 'B', 'KR', 3, 'buy', 'market', 2, 'KRW', '2026-10-14', '2026-10-15', 'r', 1,"
                   " 'filled', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                   " VALUES (90, 'B', 3, 'buy', '2026-10-15', 20000, 2, 'KRW', 1, 0, 0, 0, -40000, 't')")  # fmt: skip
    기록 = job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert s.orders == [("sell", "000660", 2, None), ("buy", "005930", 3, 9_800.0), ("buy", "005930", 2, 9_600.0)]
    assert 기록["sent"] == {"accepted": 3} and 기록["diff"] == 0 and 기록["planned"] == {"cleanup": 0, "mirror": 3}
    줄 = c.q("SELECT paper_order_id, status, broker_order_no, reason, source FROM paper_broker_orders ORDER BY id")
    assert 줄 == [(102, "accepted", "N1", "베낌", "kis_vts"), (101, "accepted", "N2", "베낌", "kis_vts"),
                 (103, "accepted", "N3", "베낌", "kis_vts")]  # fmt: skip
    assert c.q("SELECT as_of, account_id, diff_json FROM paper_broker_daily") == [("2026-10-16", "B", "{}")]
    # 다시 돌아도 두 번 보내지 않는다
    job.run(c, "KR", _at("08:50"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:50"))
    assert len(s.orders) == 3


def test_job_reconciles_next_day_and_cleans_up(c: Client) -> None:
    s = _Sess()
    job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    # 매도 102 는 증권사에 팔 수량이 없어 내지 않았다 — 매수 둘만
    assert [o[0] for o in s.orders] == ["buy", "buy"]
    # 다음 거래일: 101 은 다 찼고 103 은 안 찼다. 내부 B 는 아무것도 안 들었다(가정) → 증권사 3 주는 정리
    다음 = _Sess(holdings=[kv.Holding("005930", 3, 3, 9_800.0)],
               executions=[kv.Execution("20261016", "N1", "005930", "buy", 3, 3, 9_800.0, 0, False),
                           kv.Execution("20261016", "N2", "005930", "buy", 2, 0, None, 0, False)])  # fmt: skip
    기록 = job.run(c, "KR", _at("08:40", "2026-10-19"), session=다음, sleep=lambda _x: None,
                 clock=lambda: _at("08:40", "2026-10-19"))  # fmt: skip
    assert 다음.asked == [("20261016", "20261018")]
    assert 기록["reconciled"] == {"filled": 1, "unfilled": 1}
    assert c.q("SELECT paper_order_id, status, filled_qty, avg_price FROM paper_broker_orders"
               " WHERE trade_date = '2026-10-16' ORDER BY id") == [(101, "filled", 3, 9_800.0), (103, "unfilled", 0, None)]
    assert 다음.orders == [("sell", "005930", 3, None)] and 기록["planned"] == {"cleanup": 1, "mirror": 0}
    assert json.loads(c.q("SELECT diff_json FROM paper_broker_daily WHERE as_of = '2026-10-19'")[0][0]) == {"1": [3, 0]}


def test_job_same_day_rerun_does_not_sell_todays_fill(c: Client) -> None:
    """08:40 에 산 것이 시가에 찼고, 리포트 실패로 09:35 에 다시 돈다 — 증권사엔 3 주, 내부 장부엔 아직 0 (검증 [필수] 1)."""
    s = _Sess()
    job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    다시 = _Sess(holdings=[kv.Holding("005930", 3, 3, 9_800.0)])
    기록 = job.run(c, "KR", _at("09:35"), session=다시, sleep=lambda _x: None, clock=lambda: _at("09:35"))
    assert 다시.orders == [] and 기록["planned"] == {"cleanup": 0, "mirror": 0}


def test_deferred_sell_is_mirrored_again_next_session(c: Client) -> None:
    """정지로 미룬 매도는 같은 내부 주문이 다음 장에 다시 열린다 — 그날도 낸다 (검증 [권장] 4)."""
    s = _Sess(holdings=[kv.Holding("000660", 2, 2, 20_000.0)])
    job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    c.conn.execute("UPDATE paper_orders SET status = 'expired' WHERE id IN (101, 103)")
    c.conn.execute("UPDATE paper_orders SET fill_session = '2026-10-19' WHERE id = 102")
    다음 = _Sess(holdings=[kv.Holding("000660", 2, 2, 20_000.0)],
               executions=[kv.Execution("20261016", "N1", "000660", "sell", 2, 0, None, 0, False)])  # fmt: skip
    job.run(c, "KR", _at("08:40", "2026-10-19"), session=다음, sleep=lambda _x: None,
            clock=lambda: _at("08:40", "2026-10-19"))  # fmt: skip
    assert ("sell", "000660", 2, None) in 다음.orders
    assert c.q("SELECT trade_date, status FROM paper_broker_orders WHERE paper_order_id = 102 ORDER BY trade_date") == [
        ("2026-10-16", "unfilled"), ("2026-10-19", "accepted")]


def 기록_상태(c: Client) -> list[str]:
    return [r[0] for r in c.q("SELECT status FROM paper_broker_orders ORDER BY id")]


def test_job_off_and_timing(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    assert job.run(c, "JP", _at("08:40"), session=_Sess()) == {"off": "모르는 시장 JP"}
    monkeypatch.delenv("KIS_VTS_ACCOUNT")
    # 일부만 있으면 설정 실수 — 빈 칸 이름을 돌려줘 일일 배치가 알린다 (25.1492)
    assert job.run(c, "KR", _at("08:40"), session=_Sess()) == {"off": "KIS모의투자 키 일부만 있음",
                                                                "missing": ["KIS_VTS_ACCOUNT"]}  # fmt: skip
    monkeypatch.delenv("KIS_VTS_APP_KEY")
    monkeypatch.delenv("KIS_VTS_APP_SECRET")
    assert job.run(c, "KR", _at("08:40"), session=_Sess()) == {"off": "KIS모의투자 키 없음"}
    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("KIS_VTS_ACCOUNT", "1234567801")
    # 너무 이르면 보내지 않는다(잔고는 적는다), 08:30 앞 10분 안이면 기다렸다 보낸다
    s = _Sess()
    기록 = job.run(c, "KR", _at("07:00"), session=s, sleep=lambda _x: pytest.fail("기다리면 안 된다"))
    assert 기록["send"] == "접수 시작 전 — 보내지 않음" and s.orders == []
    잔 = []
    job.run(c, "KR", _at("08:25"), session=s, sleep=잔.append, clock=lambda: _at("08:30"))
    assert 잔 == [305.0] and len(s.orders) == 2
    # 장 끝난 뒤엔 보내지 않는다
    c.conn.execute("DELETE FROM paper_broker_orders")
    s = _Sess()
    assert job.run(c, "KR", _at("15:25"), session=s)["send"] == "장 끝난 뒤 — 보내지 않음" and s.orders == []


def test_job_halt_and_no_response(c: Client) -> None:
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('paper_halt', '{\"on\": true}', 't')")
    s = _Sess()
    기록 = job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert s.orders == [] and 기록["halted"] and 기록["not_sent"] == {"멈춤 스위치가 켜져 있음": 3}
    c.conn.execute("DELETE FROM settings")
    # 응답이 없으면 sending 으로 두고 다시 보내지 않는다(두 번 주문 막기). 거부는 rejected
    s = _Sess(fail={"005930": kv.NoResponse("모의 응답 없음 (ConnectionError)")})
    기록 = job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert 기록["sent"] == {"no_response": 2}
    assert 기록_상태(c) == ["sending", "sending"]
    job.run(c, "KR", _at("08:45"), session=s, sleep=lambda _x: None)
    assert len(s.orders) == 2
    c.conn.execute("DELETE FROM paper_broker_orders")
    s = _Sess(fail={"005930": KisFailed("모의 주문 실패 APBK0919 주문가능금액을 초과")})
    job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert c.q("SELECT DISTINCT status, message FROM paper_broker_orders") == [
        ("rejected", "모의 주문 실패 APBK0919 주문가능금액을 초과")]


# ── 비교 보고 (docs/paper.md 15.3, 25.1478) ───────────────────────────────────


def _줄(side: str, st: str, i_st: str, *, fq: int = 0, avg: float | None = None, ip: float | None = None,
       msg: str | None = None, reason: str = "베낌", fd: str = "2026-10-16", note: str | None = None) -> dict[str, Any]:  # fmt: skip
    return {"trade_date": "2026-10-16", "side": side, "status": st, "filled_qty": fq, "quantity": 3, "avg_price": avg,
            "message": msg, "reason": reason, "i_status": i_st, "i_price": ip, "i_fill_date": fd,
            "i_note": note}  # fmt: skip


def test_compare_counts_match_and_signed_slippage() -> None:
    s = bm.compare([
        _줄("buy", "filled", "filled", fq=3, avg=10_100.0, ip=10_000.0),     # 둘 다 참, 매수 1% 불리
        _줄("sell", "filled", "filled", fq=3, avg=19_800.0, ip=20_000.0),    # 둘 다 참, 매도 1% 불리
        _줄("buy", "unfilled", "filled", ip=9_800.0),                        # 가상만 참 — 가상이 낙관
        _줄("buy", "partial", "expired", fq=1, avg=9_700.0),                 # KIS만 참(부분)
        _줄("buy", "rejected", "expired", msg="모의 주문 실패 APBK0919 주문가능금액을 초과"),  # 거부 — 표본 밖(25.1561)
        _줄("buy", "unfilled", "expired"),                                   # 둘 다 안 참(판정 만료)
        _줄("buy", "unknown", "filled"),                                     # 모름 — 일치에서 뺀다
        _줄("sell", "filled", "filled", fq=3, avg=1.0, ip=1.0, fd="2026-10-19"),  # 미룬 매도가 뒤 장에서 참 — 이 날은 가상 안 참
        _줄("sell", "filled", "", reason="정리"),                             # 정리는 따로
        _줄("buy", "accepted", "filled"),                                    # 아직 안 끝남
    ])  # fmt: skip
    assert (s["n"], s["both"], s["internal_only"], s["kis_only"], s["neither"]) == (6, 2, 1, 2, 1)
    assert (s["partial"], s["rejected"], s["unknown"], s["cleanup"], s["excluded"]) == (1, 1, 1, 1, 0)
    assert s["match_pct"] == 50.0
    assert s["slip_buy"] == {"n": 1, "avg": pytest.approx(1.0)} and s["slip_sell"] == {"n": 1, "avg": pytest.approx(1.0)}
    assert s["reject_reasons"] == {"APBK0919": 1}
    줄 = bm.compare_lines(s, 2, "2026-10-19")
    assert 줄[0] == "[KIS모의투자 — H 주문 베끼기, 지난 28일]"
    assert "체결 일치 50% (둘 다 참 2 · 가상만 참 1 · KIS만 참 2 · 둘 다 안 참 1)" in 줄[1]
    assert 줄[2] == "  가격(+ 면 가상모의투자가 낙관): 매수 +1.00% (n=1) · 매도 +1.00% (n=1)"
    assert 줄[-1] == "  거부 1 · 모름 1 · 정리 주문 1 · 잔고가 어긋난 종목 2 (2026-10-19)"
    assert bm.compare_lines(bm.compare([]), 0, None) == []
    # 거부만 있는 주도 요약을 낸다 (25.1564 — 예전엔 빈 목록이라 주간 요약에서 KIS 절이 사라졌다)
    거부만 = bm.compare([_줄("buy", "rejected", "filled", msg="EGW00201 초당 거래건수 초과") for _ in range(5)])
    줄 = bm.compare_lines(거부만, 0, "2026-10-20")
    assert 줄[-1] == "  거부 5 · 잔고가 어긋난 종목 0 (2026-10-20)" and any("EGW00201 5" in x for x in 줄)


def test_판정_아닌_가상_만료와_거부는_일치율_표본_밖() -> None:
    """현금 부족·멈춤 등 가상의 판정 아닌 만료와 KIS 거부가 "KIS만 참"·"가상만 참" 으로 세져 괴리 알림 방향이 틀리던 것
    (25.1561 — KIS 경로 감사 [권고] 1). 체결 칸도 같다"""
    줄들 = [_줄("buy", "filled", "filled", fq=3, avg=10_000.0, ip=10_000.0)]
    줄들 += [_줄("buy", "filled", "expired", fq=3, avg=10_000.0, note=n) for n in
             ("현금 부족 (다른 미체결 매수 예약 포함)", "기업행위 경계 — 멈춤 (docs/paper.md 2.3)", "쓸 수 있는 환율 없음",
              "그날 시세를 받지 못해 판정 못 함")]  # fmt: skip
    줄들 += [_줄("sell", "filled", "expired", fq=3, avg=1.0, note="팔 보유가 모자람")]
    줄들 += [_줄("buy", "rejected", "filled", ip=10_000.0, msg="EGW00201 초당 거래건수 초과")]
    # 판정 만료(종가 > 회차 가격)는 그대로 표본 — KIS 가 찼으면 KIS만 참
    줄들 += [_줄("buy", "filled", "expired", fq=3, avg=9_900.0, note="종가 10100 > 회차 가격 9900")]
    s = bm.compare(줄들)
    assert (s["n"], s["both"], s["kis_only"], s["internal_only"], s["excluded"], s["rejected"]) == (2, 1, 1, 0, 5, 1)
    assert s["kept_pct"] == 100.0
    칸 = bm.cells([{**r, "i_order_type": "limit" if r["side"] == "buy" else "market", "i_fill_basis": "open"}
                  for r in 줄들])  # fmt: skip
    assert 칸["cells"]["buy_limit"]["n"] == 2


def test_장_열린_뒤_낸_시장가는_비교_표본_밖() -> None:
    """가상은 종가로, KIS 는 장중에 찬다 — 체결 칸만 빼고 비교(가격 차이·괴리)는 넣던 것 (25.1561, 감사 [참고] 2)"""
    늦은 = [{**_줄("sell", "filled", "filled", fq=3, avg=9_900.0, ip=10_000.0), "i_order_type": "market",
             "i_fill_basis": "close"} for _ in range(3)]  # fmt: skip
    지정가 = {**_줄("buy", "filled", "filled", fq=3, avg=10_000.0, ip=10_000.0), "i_order_type": "limit",
             "i_fill_basis": "close"}  # 지정가 매수는 잣대가 같아 그대로  # fmt: skip
    s = bm.compare([*늦은, 지정가])
    assert (s["n"], s["excluded"], s["slip_sell"]["n"]) == (1, 3, 0)


def test_daily_summary_stored_and_weekly_reads_it(c: Client) -> None:
    from batch.jobs import paper as paper_job

    job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    c.conn.execute("UPDATE paper_orders SET status = 'expired' WHERE id IN (101, 103)")
    다음 = _Sess(executions=[kv.Execution("20261016", "N1", "005930", "buy", 3, 3, 9_790.0, 0, False),
                            kv.Execution("20261016", "N2", "005930", "buy", 2, 0, None, 0, False)])  # fmt: skip
    기록 = job.run(c, "KR", _at("08:40", "2026-10-19"), session=다음, sleep=lambda _x: None,
                 clock=lambda: _at("08:40", "2026-10-19"))  # fmt: skip
    # 101 은 KIS 만 참, 103 은 둘 다 안 참 — 가상이 찬 것이 없어 할인율(25.1504)은 모름
    assert 기록["compare"] == {"n": 2, "match_pct": 50.0, "kept_pct": None}
    s = json.loads(c.q("SELECT summary_json FROM paper_broker_daily WHERE as_of = '2026-10-19'")[0][0])
    assert (s["kis_only"], s["neither"]) == (1, 1)
    줄 = job.summary_lines(c)
    assert 줄[0] == "[KIS모의투자 — B 주문 베끼기, 지난 28일]" and "잔고가 어긋난 종목 0 (2026-10-19)" in 줄[-1]
    # 주간 요약은 가상모의투자 평가 행이 없으면 통째로 비고(입금 전), 있으면 이 절을 붙인다 — 붙이는 줄은 summary_section 끝
    assert paper_job.summary_section(c, datetime(2026, 10, 25, tzinfo=UTC)) == []


def test_mirror_account_is_h() -> None:
    """KIS모의투자는 H(지주사)를 베낀다 — 2026-10-16 사용자 결정 (docs/company.md 10장)."""
    assert job.MIRROR_ACCOUNT == "H"


def test_holdings_diff_ignores_us_rerun_and_pre_link(c: Client) -> None:
    """잔고 대조의 거짓 어긋남 셋 (25.1483, 감사 [권장] 1·2·3): 미국 보유, 같은 날 다시 돌 때 오늘 산 것, 키를 넣기 전에 산 가상 보유."""
    # 가상 B 가 연결 전부터 3(국내)·2(미국 AAPL)를 들고 있다
    for oid, sid in ((91, 3), (92, 2)):
        c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                       " decided_on, fill_session, reason, rule_version, status, created_at)"
                       " VALUES (?, 'B', 'KR', ?, 'buy', 'market', 2, 'KRW', '2026-10-13', '2026-10-14', 'r', 1,"
                       " 'filled', 't')", [oid, sid])  # fmt: skip
        c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                       " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                       " VALUES (?, 'B', ?, 'buy', '2026-10-14', 100, 2, 'KRW', 1, 0, 0, 0, -200, 't')", [oid, sid])  # fmt: skip
    c.conn.execute("DELETE FROM paper_orders WHERE id = 102")  # 3 을 파는 주문은 이 시험에서 빼 둔다
    기록 = job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 기록["diff"] == 0 and 기록["before_link"] == 1  # 미국 2 는 세지 않고, 국내 3 은 연결 전 보유
    # 10:30 다시 — 시가에 찬 오늘 매수(005930 3+2)가 증권사엔 있다
    다시 = job.run(c, "KR", _at("10:30"), session=_Sess(holdings=[kv.Holding("005930", 5, 5, 9_700.0)]),
                 sleep=lambda _x: None, clock=lambda: _at("10:30"))  # fmt: skip
    assert 다시["diff"] == 0


def test_연결_뒤_거부된_매수는_연결_전_보유로_숨지_않는다(c: Client) -> None:
    """KIS 가 거부한 매수(가상은 참)가 "연결 전 보유" 로 빠져 어긋남이 0 으로 보이던 것 (25.1561 — KIS 경로 감사 [권고] 2)"""
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                   " decided_on, fill_session, reason, rule_version, status, created_at)"
                   " VALUES (91, 'B', 'KR', 3, 'buy', 'market', 2, 'KRW', '2026-10-15', '2026-10-16', 'r', 1,"
                   " 'filled', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                   " VALUES (91, 'B', 3, 'buy', '2026-10-16', 100, 2, 'KRW', 1, 0, 0, 0, -200, 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, quantity, trade_date, reason, status, message, source, created_at)"
                   " VALUES ('kis_vts', 91, 'B', 'KR', 3, '000660', 'buy', 'market', 2, '2026-10-16', '베낌',"
                   " 'rejected', 'EGW00201 초당 거래건수 초과', 't', 't')")  # fmt: skip
    c.conn.execute("DELETE FROM paper_orders WHERE id = 102")  # 3 을 파는 주문은 이 시험에서 빼 둔다
    기록 = job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 기록["diff"] == 1 and 기록.get("before_link", 0) == 0


def test_연결_전부터_든_종목의_거부된_회차는_어긋남이_아니다(c: Client) -> None:
    """연결 전부터 든 종목 3 의 연결 뒤 회차를 KIS 가 거부하고 가상도 안 찼다 — 둘 다 새로 산 게 없는데 영구 어긋남으로
    세던 것 (25.1564 — 25.1561 반박 [권고] B). 연결 전 보유는 베끼기 시작한 날 전에 든 것으로 가른다"""
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, quantity, currency,"
                   " decided_on, fill_session, reason, rule_version, status, created_at)"
                   " VALUES (91, 'B', 'KR', 3, 'buy', 'market', 2, 'KRW', '2026-10-13', '2026-10-14', 'r', 1,"
                   " 'filled', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                   " fx_rate, fee, tax, slippage, cash_krw, created_at)"
                   " VALUES (91, 'B', 3, 'buy', '2026-10-14', 100, 2, 'KRW', 1, 0, 0, 0, -200, 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, reason, rule_version, status, close_note, created_at)"
                   " VALUES (93, 'B', 'KR', 3, 'buy', 'limit', 90, 1, 'KRW', '2026-10-15', '2026-10-16', 'r', 1,"
                   " 'expired', '종가 100 > 회차 가격 90', 't')")  # fmt: skip
    c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                   " order_type, quantity, trade_date, reason, status, message, source, created_at)"
                   " VALUES ('kis_vts', 93, 'B', 'KR', 3, '000660', 'buy', 'limit', 1, '2026-10-16', '베낌',"
                   " 'rejected', 'EGW00201 초당 거래건수 초과', 't', 't')")  # fmt: skip
    c.conn.execute("DELETE FROM paper_orders WHERE id = 102")
    기록 = job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 기록["diff"] == 0 and 기록["before_link"] == 1


def test_partial_keys_are_named_and_alerted() -> None:
    """앱 시크릿 이름을 틀려 하루를 꺼진 채 지날 뻔했다(2026-10-16 시험 주문, 25.1491) — 빈 칸 이름(값 없이)과 알림 문장 (25.1492)."""
    from batch.jobs import daily

    assert kv.missing({"KIS_VTS_APP_KEY": "k", "KIS_VTS_ACCOUNT": "12345678"}) == ["KIS_VTS_APP_SECRET"]
    assert kv.missing({"KIS_VTS_APP_KEY": "k", "KIS_VTS_APP_SECRET": "s", "KIS_VTS_ACCOUNT": "1234567"}) == [
        "KIS_VTS_ACCOUNT(숫자 8·10자리 아님)"]
    assert kv.missing({}) == ["KIS_VTS_APP_KEY", "KIS_VTS_APP_SECRET", "KIS_VTS_ACCOUNT"] and kv.missing(키) == []
    assert "KIS_VTS_APP_SECRET" in (daily._kis_알림문({"off": "x", "missing": ["KIS_VTS_APP_SECRET"]}) or "")
    assert "실패" in (daily._kis_알림문({"error": "토큰 HTTP 403"}) or "")
    assert daily._kis_알림문({"off": "KIS모의투자 키 없음"}) is None and daily._kis_알림문({"sent": {}}) is None


def test_plan_skips_frozen_mirror_orders() -> None:
    """기업행위 경계 종목은 정리뿐 아니라 베낀 주문도 내지 않는다 — 분할 전 수량으로 증권사에서만 팔린다 (25.1495, 감사 [참고] 1)."""
    낼것, 안냄 = bm.plan([_o(11, 3, "sell", 2, None), _o(12, 1, "buy", 1, 9_800.0)], {1: "005930", 3: "000660"}, {3: 2},
                       {3: (2, 2)}, {3}, already=set(), cleaned_today=set(), halted=False, frozen={3})  # fmt: skip
    assert [x.paper_order_id for x in 낼것] == [12] and 안냄 == [(11, 3, "기업행위 경계 — 베끼지 않음")]
