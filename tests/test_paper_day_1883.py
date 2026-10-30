"""가상모의투자 당일 단타 계좌 T — 짝 매도 주문 (docs/paper.md 16.3.2, docs/infra.md 25.1883).

계좌 행은 0092(2026-10-29 개설)지만 시험은 개설일 막이 밖의 날짜로 행을 직접 넣어 본다: 단기 신호를 다음 장 시가에 사고 같은 장 종가에 판다,
하루 끝 보유 0, 늦은 실행엔 진입 안 함, 쉼 없음, 짝을 잃은 보유는 다음 장 시가, 그리고 다른 계좌의 행은 그대로.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _시세, _신호, 설정
from tests.test_paper_swing_1871 import _시세3, _중기


def _계좌(accounts: tuple[str, ...], t: bool, s: bool = False) -> Client:
    c = Client(accounts=accounts)
    for aid, rule in (("S", "swing_close"), ("T", "day_close")):
        if (aid == "T" and t) or (aid == "S" and s):
            c.conn.execute(
                "INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n,"
                " selection) VALUES (?, ?, ?, 1, '2026-10-14T00:00:00+00:00', 'open', 10, 'short')", [aid, aid, rule])
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')"
    )
    return c


@pytest.fixture(autouse=True)
def _고정(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, signals: bool = True,
         late: bool = False) -> tuple[list[str], dict]:  # fmt: skip
    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    return job.run_step(
        c, market="KR", trade_date=trade_date, late=late, signal_rows=[_신호(), _중기()] if signals else [],
        settings=설정, price_series={}, series_unknown={}, allocate=signals,
        now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip


def test_T_는_단기_신호를_시가에_사고_같은_장_종가에_파는_짝을_낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    주문 = c.q("SELECT stock_id, side, order_type, fill_session, fill_basis, tranche, reason, rationale_json"
               " FROM paper_orders WHERE account_id = 'T' ORDER BY side")  # fmt: skip
    assert [r[:7] for r in 주문] == [(1, "buy", "market", "2026-10-15", "open", 1, "2부 배분"),
                                     (1, "sell", "market", "2026-10-15", "close", 0, "단타종가")]  # fmt: skip
    assert json.loads(주문[1][7])["rule"] == "day_v1"
    # 웹 거래내역 근거표 한 줄 — 규칙으로 파는 주문이라 참고 행(25.2014)
    짝근거 = json.loads(주문[1][7])["flags"][0]
    assert 짝근거["reason_code"] == "단타종가" and [c["label"] for c in 짝근거["criteria"]] == ["짝 매수"]
    assert 짝근거["criteria"][0]["display"].startswith("회차 1 · ") and 짝근거["criteria"][0]["passed"] is None
    assert 짝근거["criteria"][0]["as_of"] == "2026-10-15"
    # 수량이 같다 — 산 만큼 판다
    assert len({q for (q,) in c.q("SELECT quantity FROM paper_orders WHERE account_id = 'T'")}) == 1


def test_T_는_하루_끝_보유가_0_이고_실현손익이_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_000, 10_300)
    _시세3(c, "2026-10-15", 10_000)
    _, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    체결 = c.q("SELECT side, price, fill_date FROM paper_fills WHERE account_id = 'T' ORDER BY id")
    assert 체결 == [("buy", 10_000, "2026-10-15"), ("sell", 10_300, "2026-10-15")]
    fills, _ = job._fills(c)
    assert pp.positions([f for f in fills if f.account_id == "T"]) == {}
    assert 기록["accounts"]["T"]["recon_ok"] is True
    assert c.q("SELECT positions_json FROM paper_daily WHERE account_id = 'T' ORDER BY as_of DESC LIMIT 1") == [("[]",)]
    assert c.q("SELECT realized_krw > 0 FROM paper_fills WHERE account_id = 'T' AND side = 'sell'") == [(1,)]
    # 짝으로 이미 팔았으니 안전망 매도(단타잔여)는 없다
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T' AND reason = ?", [pp.DAY_LEFT_CODE]) == [(0,)]


def test_T_는_늦은_실행에_진입하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _, 기록 = _하루(c, monkeypatch, "2026-10-14", late=True)  # 장 열린 뒤 — 주문 기준 종가
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T'") == [(0,)]
    assert 기록["accounts"]["T"]["buy"]["late_skip"] == 1
    assert c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'T' AND stock_id = 1") == [
        ("skip", pp.DAY_LATE_PREFIX + "장 열린 뒤 실행")]
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B'")[0][0] >= 1  # 다른 계좌는 그대로 낸다


def test_T_는_다시_사기_쉼이_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_000, 10_300)
    _시세3(c, "2026-10-15", 10_000)
    _하루(c, monkeypatch, "2026-10-15")  # 판 다음 날 같은 단기 신호가 다시 온다
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T' AND side = 'buy'") == [(2,)]


def test_짝을_잃은_보유는_다음_장_시가에_판다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    c.conn.execute("DELETE FROM paper_orders WHERE account_id = 'T' AND side = 'sell'")  # 짝 행이 빠진 날
    _시세(c, "2026-10-15", 10_000, 10_300)
    _시세3(c, "2026-10-15", 10_000)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert c.q("SELECT reason, fill_session, fill_basis FROM paper_orders WHERE account_id = 'T' AND side = 'sell'") == [
        (pp.DAY_LEFT_CODE, "2026-10-16", "open")]


def _고름(c: Client, 표: str, r: tuple) -> tuple:
    키 = {x[0]: tuple(x[1:]) for x in c.q("SELECT id, account_id, decided_on, stock_id, side, tranche FROM paper_orders")}
    if 표 != "paper_daily":  # 평가 행은 id 가 없고 첫 칸이 계좌 — 버리면 두 계좌 평가가 바뀌어도 통과했다(25.1886)
        r = r[1:]
    if 표 == "paper_fills":
        r = (키[r[0]], *r[1:])
    if 표 == "paper_decisions":
        cols = [d[1] for d in c.conn.execute("PRAGMA table_info(paper_decisions)")][1:]
        i = cols.index("detail_json")
        d = json.loads(r[i]) if r[i] else None
        if isinstance(d, dict) and "order_id" in d:
            r = (*r[:i], json.dumps({**d, "order_id": 키[d["order_id"]]}, ensure_ascii=False), *r[i + 1:])
    return r


def test_T_가_있어도_다른_계좌의_행은_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.3.2 무변화 — T 는 id 순서 맨 끝. S 까지 있는 아홉+1 계좌로, 둘째 날에도 신호"""
    표들 = ("paper_orders", "paper_fills", "paper_decisions", "paper_daily")
    결과 = []
    for t in (False, True):
        c = _계좌(("A", "B", "C", "D", "E", "G", "H", "R1", "R2"), t=t, s=True)
        _하루(c, monkeypatch, "2026-10-14")
        _시세(c, "2026-10-15", 10_000, 9_650)
        _시세3(c, "2026-10-15", 10_000)
        _하루(c, monkeypatch, "2026-10-15")
        _시세(c, "2026-10-16", 9_700, 9_800)
        _시세3(c, "2026-10-16", 10_100)
        _하루(c, monkeypatch, "2026-10-16", signals=False)
        # 행 번호와 그것을 가리키는 주문 번호(체결의 order_id, 놓친 회차 결정의 detail.order_id)는 T 주문이 끼면 밀린다 —
        # 주문의 자연 키(계좌·판정일·종목·매매·회차)로 바꿔 견준다(25.1883)
        결과.append({x: sorted(_고름(c, x, r) for r in c.q(f"SELECT * FROM {x} WHERE account_id != 'T'")) for x in 표들})
        if t:
            assert c.q("SELECT COUNT(*) FROM paper_fills WHERE account_id = 'T'")[0][0] >= 2
    for x in 표들:
        assert 결과[0][x] == 결과[1][x], x


# ── 25.1884 — 짝이 어긋나는 날 ───────────────────────────────────────────────────────────────


def _둘째날(c: Client, monkeypatch: pytest.MonkeyPatch, o: float | None, cl: float, chg: float | None = None,
          bar1: bool = True) -> dict:  # fmt: skip
    from tests.test_paper_fill_1620 import 시세

    _하루(c, monkeypatch, "2026-10-14")
    if bar1:
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at,"
            " change_pct) VALUES (1, '2026-10-15', ?, ?, ?, ?, 1000, 'KRW', 't', 't', ?)",
            [o, max(o or cl, cl), min(o or cl, cl), cl, chg],
        )
    시세(c, 3, "2026-10-15", 10_000, 10_000)
    _, 기록 = _하루(c, monkeypatch, "2026-10-15", signals=False)
    return 기록


def _상태(c: Client) -> list[tuple]:
    return c.q("SELECT side, status, close_note FROM paper_orders WHERE account_id = 'T' ORDER BY side")


def test_상한가_시가면_사지_않고_짝도_거둔다(monkeypatch: pytest.MonkeyPatch) -> None:
    """신호 날 종가 10,000 → 시가 12,970(+29.7%) — 엔진의 가격제한폭(±30% 호가 끝)엔 안 걸리지만 그림자와 같은 판정"""
    c = _계좌(("B",), t=True)
    _둘째날(c, monkeypatch, 12_970, 12_500)
    상태 = _상태(c)
    assert 상태[0][:2] == ("buy", "expired") and "상한가 시가" in 상태[0][2] and "단타 진입 안 함" in 상태[0][2]
    assert 상태[1] == ("sell", "expired", "단타 짝 매수 안 참")
    assert c.q("SELECT COUNT(*) FROM paper_fills WHERE account_id = 'T'") == [(0,)]
    assert c.q("SELECT kind FROM paper_decisions WHERE account_id = 'T' AND kind = 'missed'") == [("missed",)]
    # 다른 계좌(B, 지정가 회차)는 이 판정을 타지 않는다 — 상한가 매수 판정은 엔진 그대로
    assert all("단타" not in str(n) for (n,) in c.q("SELECT close_note FROM paper_orders WHERE account_id = 'B'"))


def test_시가가_비면_0분_거래를_하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    _둘째날(c, monkeypatch, None, 10_200)
    assert _상태(c) == [("buy", "expired", "시가 없음 — 단타 진입 안 함"), ("sell", "expired", "단타 짝 매수 안 참")]


def test_짝_매수가_시세를_기다리면_짝_매도도_기다린다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    기록 = _둘째날(c, monkeypatch, None, 0, bar1=False)  # 시장엔 10-15 시세가 있는데 종목 1 행만 늦다
    assert [s for _side, s, _n in _상태(c)] == ["open", "open"] and 기록.get("waiting_pair") == 1
    _시세(c, "2026-10-15", 10_000, 10_300)  # 늦게 온 행
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT side, price, fill_date FROM paper_fills WHERE account_id = 'T' ORDER BY id") == [
        ("buy", 10_000, "2026-10-15"), ("sell", 10_300, "2026-10-15")]


def test_종가가_하한가면_짝_매도를_다음_장으로_미룬다(monkeypatch: pytest.MonkeyPatch) -> None:
    """그림자와 다른 유일한 밤샘 — 하한가 종가엔 시장가 매도가 안 찬다(엔진 25.1553)"""
    c = _계좌(("B",), t=True)
    _둘째날(c, monkeypatch, 10_000, 7_000, chg=-30.0)
    assert c.q("SELECT side, price FROM paper_fills WHERE account_id = 'T'") == [("buy", 10_000)]
    assert c.q("SELECT status, fill_session, deferrals FROM paper_orders WHERE account_id = 'T' AND side = 'sell'") == [
        ("open", "2026-10-16", 1)]
    # 미룬 짝 매도는 다음 장 시가로 판정한다(개장 전에 있던 주문) — 짝 매수 확인은 미루기 전 한 번만
    _시세(c, "2026-10-16", 6_800, 7_100)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT price, fill_date FROM paper_fills WHERE account_id = 'T' AND side = 'sell'") == [
        (6_800, "2026-10-16")]


# ── 25.1885 — 상황실·웹·월간 복기 ────────────────────────────────────────────────────────────


def test_T_는_따로_재고_사전_등록과_화면_글이_있다() -> None:
    from batch.services import desk

    assert "T" in pp.SEPARATE_IDS and desk.KIND["T"] and desk.prereg("T", 1) is not None
    날 = [f"2026-{m:02d}-{d:02d}" for m in (9, 10, 11) for d in range(1, 29)]
    계열 = {a: [(d, 1 + i * 0.001) for i, d in enumerate(날)] for a in ("B", "H", "R1")}
    계열["T"] = [(d, 1 + i * 0.003) for i, d in enumerate(날[40:])]
    표 = desk.leaderboard(계열)
    assert 표["window_start"] == 날[0] and next(r for r in 표["rows"] if r["id"] == "T")["separate"]
    assert [r["id"] for r in desk.champion(계열, "H")] == ["B"]


def test_KIS_체결_보정에서_T_를_뺀다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import desk as desk_job
    from batch.services import desk

    받은: dict = {}
    monkeypatch.setattr(desk_job, "_칸합침", lambda *a, **k: {})
    monkeypatch.setattr(desk, "fill_adjust", lambda **k: 받은.update(k["starts"]) or {})

    class C:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            class R:
                rows: list = []

                def dicts(self):  # noqa: ANN202
                    return []
            return R()

    순위 = {"window_start": "2026-09-01", "rows": [
        {"id": "B", "open": "2026-09-01", "young": False, "separate": False, "window_pct": 1.0},
        {"id": "S", "open": "2026-11-04", "young": False, "separate": True, "window_pct": 2.0},
        {"id": "T", "open": "2026-11-05", "young": False, "separate": True, "window_pct": 3.0}]}  # fmt: skip
    desk_job._보정판(C(), 순위, {}, {}, "H", {}, {}, "2026-11-20")
    assert 받은 == {"B": "2026-09-01", "S": "2026-11-04"}  # T 는 회전 × KIS 칸 보정을 하지 않는다


def test_월간_복기의_손절_점검은_T_에_해당_없음() -> None:
    행 = [{"as_of": "2026-10-16", "invested_krw": 1_000, "equity_krw": 1_000, "twr_index": 1.0, "positions_json": "[]"}]
    줄 = pp.summary_lines({"T": 행}, {}, "2026-10-12", "2026-10-01", {}, {}, {}, {"T": "day_close"})
    assert any(x.startswith("  단타 규칙 점검") and "해당 없음(당일 단타" in x for x in 줄)


# ── 25.1886 (25.1883 교차검증 [권고]·[참고]) ─────────────────────────────────────────────────


def test_T_는_장_끝난_뒤_손_실행에도_진입하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), t=True)
    다음 = cal.next_session("KR", date(2026, 10, 14))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    _, 기록 = job.run_step(c, market="KR", trade_date="2026-10-14", late=False, after_close=True,
                          signal_rows=[_신호()], settings=설정, price_series={}, series_unknown={}, allocate=True,
                          now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"))  # fmt: skip
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T'") == [(0,)]
    결정 = c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'T' AND stock_id = 1")
    assert 결정 == [("skip", pp.DAY_LATE_PREFIX + "장 끝난 뒤 손 실행")]
    assert 결정[0][1].startswith(pp.NONCOMPARABLE_SKIP_PREFIXES)  # 견줄 새 후보에서 빠진다


def test_T_진입_방식이_open_이_아니면_그_계좌만_건너뛴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """회차 지정가면 짝 매도가 첫 회차에만 붙어(유일 색인 tranche=0) 나머지 회차가 밤을 넘겼다 — 칸 값 실수 막이"""
    c = _계좌(("B",), t=True)
    c.conn.execute("UPDATE paper_accounts SET entry_rule = 'tranche' WHERE id = 'T'")
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert "error" in 기록["accounts"]["T"] and c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'T'") == [(0,)]
    assert any("가상 단타 당일 단타 진입 방식" in x for x in 줄) and 기록["accounts"]["B"]["orders"] >= 1


def test_웹_매도_규칙_글은_모든_매도_규칙을_덮는다() -> None:
    """25.1886 — 배치 `SELL_RULES` 와 웹 `SELL_RULE_TEXT` 의 열쇠가 어긋나면 화면에 날 값(`day_close`)이 보였다"""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "paper.ts").read_text(encoding="utf-8")
    표 = 글[글.index("const SELL_RULE_TEXT"): 글.index("};", 글.index("const SELL_RULE_TEXT"))]
    assert all(f"  {k}:" in 표 for k in pp.SELL_RULES), sorted(pp.SELL_RULES)
    웹 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "paperDecisions.ts").read_text(encoding="utf-8")
    assert f'DAY_LATE_SKIP_PREFIX = "{pp.DAY_LATE_PREFIX}"' in 웹


# ── 25.1887 (25.1886 교차검증 [필수]·[권고]) ─────────────────────────────────────────────────


def test_늦은_실행이_낸_안전망_매도는_짝으로_오인하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """[필수] 짝을 종가 판정·안 미룸으로만 알아보면, 장 열린 뒤 실행이 낸 `단타잔여`(종가 판정)를 "짝 매수 안 참" 으로
    거둬 보유가 하루 더 밤을 넘겼다 — 사유로 알아본다"""
    c = _계좌(("B",), t=True)
    _하루(c, monkeypatch, "2026-10-14")
    c.conn.execute("DELETE FROM paper_orders WHERE account_id = 'T' AND side = 'sell'")  # 짝 행이 빠진 날
    _시세(c, "2026-10-15", 10_000, 10_300)
    _시세3(c, "2026-10-15", 10_000)
    _하루(c, monkeypatch, "2026-10-15", signals=False, late=True)  # 장 열린 뒤 — 안전망 매도가 종가 판정
    assert c.q("SELECT reason, fill_basis FROM paper_orders WHERE account_id = 'T' AND side = 'sell'") == [
        (pp.DAY_LEFT_CODE, "close")]
    _시세(c, "2026-10-16", 10_200, 10_400)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT price, fill_date FROM paper_fills WHERE account_id = 'T' AND side = 'sell'") == [
        (10_400, "2026-10-16")]


def test_늦은_실행_날_관문이_막은_매수는_blocked_로_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1886 '관문 막힘 보존' — 막힌 것까지 skip 으로 덮으면 막힌수와 원장이 어긋났다. 단기 신호 둘 가운데 관문이 하나만
    막게 하고(나머지는 지나 늦은 실행 skip), 막힌 것은 blocked 로 남는지 본다"""
    import dataclasses as dc

    from batch.services import risk_gate as rg

    진짜 = rg.gate

    def 하나만(후보, 문맥):  # noqa: ANN001, ANN202
        통과, 막힘 = 진짜(후보, 문맥)
        return [c for c in 통과 if c.stock_id != 3], 막힘 + [(c, [("x", "시험 막힘")]) for c in 통과 if c.stock_id == 3]

    monkeypatch.setattr(job.rg, "gate", 하나만)
    c = _계좌(("B",), t=True)
    다음 = cal.next_session("KR", date(2026, 10, 14))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    둘째 = dc.replace(_신호(), stock_id=3, ticker="000660", name="종목3")
    _, 기록 = job.run_step(c, market="KR", trade_date="2026-10-14", late=True, signal_rows=[_신호(), 둘째], settings=설정,
                          price_series={}, series_unknown={}, allocate=True,
                          now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"))  # fmt: skip
    결정 = dict(c.q("SELECT stock_id, kind FROM paper_decisions WHERE account_id = 'T'"))
    assert 결정 == {1: "skip", 3: "blocked"}
    assert 기록["accounts"]["T"]["risk_blocked"] == 1


def _주문(**kw) -> pp.Order:
    기본 = {"id": 1, "account_id": "T", "stock_id": 7, "side": "buy", "order_type": "market", "limit_price": None,
            "quantity": 10, "currency": "KRW", "fill_session": "2026-10-30", "ref_price": 10_000.0}  # fmt: skip
    return pp.Order(**(기본 | kw))


def _체결(**kw) -> pp.Fill:
    기본 = {"order_id": 1, "account_id": "T", "stock_id": 7, "side": "buy", "fill_date": "2026-10-30", "price": 1.0,
            "quantity": 10, "currency": "KRW", "fx_rate": 1.0, "fee": 0.0, "tax": 0.0, "slippage": 0.0,
            "cash_krw": -10.0}  # fmt: skip
    return pp.Fill(**(기본 | kw))


def test_단타_진입_막기_순수_함수(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pp.day_entry`(25.2031 — 가상모의투자 체결 판정과 S·T 리플레이가 같이 쓴다)."""
    o = _주문()
    보통 = pp.Bar("2026-10-30", 10_100.0, 10_300.0, 1000)
    assert pp.day_entry(o, *pp.judge(o, 보통), 보통) == (10_100.0, "시가")
    시가없음 = pp.Bar("2026-10-30", None, 10_300.0, 1000)
    assert pp.day_entry(o, *pp.judge(o, 시가없음), 시가없음) == (None, "시가 없음 — 단타 진입 안 함")
    상한 = pp.Bar("2026-10-30", 12_990.0, 12_000.0, 1000)  # 신호 날 종가 10,000 대비 +29.9%
    가격, 까닭 = pp.day_entry(o, *pp.judge(o, 상한), 상한)
    assert 가격 is None and 까닭.startswith("상한가 시가")
    # T 가 아니거나 매도·안 찬 판정은 그대로
    assert pp.day_entry(_주문(account_id="S"), 10_300.0, pp.NO_OPEN_CLOSE_NOTE, 시가없음) == (
        10_300.0, pp.NO_OPEN_CLOSE_NOTE)  # fmt: skip
    assert pp.day_entry(_주문(side="sell"), 10_300.0, pp.NO_OPEN_CLOSE_NOTE, 시가없음)[0] == 10_300.0
    assert pp.day_entry(o, None, "정지", None) == (None, "정지")


def test_단타_짝_매도_상태_순수_함수() -> None:
    """`pp.pair_sell_state`(25.2031) — 짝 매수가 찼으면 None, 기다리면 wait, 안 찼으면 expire."""
    짝 = _주문(id=2, side="sell", fill_basis="close", reason=pp.DAY_CLOSE_CODE)
    assert pp.pair_sell_state(짝, pp.DAY_CLOSE_CODE, [_체결()], []) is None
    assert pp.pair_sell_state(짝, pp.DAY_CLOSE_CODE, [], [_주문()]) == "wait"
    assert pp.pair_sell_state(짝, pp.DAY_CLOSE_CODE, [], []) == "expire"
    # 다른 날 체결·다른 종목 매수는 짝이 아니다
    assert pp.pair_sell_state(짝, pp.DAY_CLOSE_CODE, [_체결(fill_date="2026-10-29"), _체결(stock_id=8)], []) == "expire"
    # 짝이 아닌 것 — 안전망 매도(단타잔여)·미룬 매도·S 계좌
    assert pp.pair_sell_state(짝, "단타잔여", [], []) is None
    assert pp.pair_sell_state(_주문(id=2, side="sell", reason=pp.DAY_CLOSE_CODE, deferrals=1), pp.DAY_CLOSE_CODE,
                              [], []) is None  # fmt: skip
    assert pp.pair_sell_state(_주문(id=2, side="sell", account_id="S"), pp.DAY_CLOSE_CODE, [], []) is None


def test_쉼_집합과_스윙_후보_순수_함수() -> None:
    """`pp.rest_set`·`pp.short_candidates`(25.2033 — 가상모의투자 run_step 과 S·T 리플레이가 같이 쓴다)."""
    체결 = [_체결(stock_id=1, side="sell", fill_date="2026-10-26"), _체결(stock_id=2, side="sell", fill_date="2026-10-23"),
            _체결(stock_id=3, side="buy", fill_date="2026-10-29")]  # fmt: skip
    # 쉼 시작(10-26) 이후 판 1 + 오늘 파는 4 + 매도 열린 5. 쉼 앞에 판 2·산 3 은 아니다
    assert pp.rest_set("swing_close", 체결, "2026-10-26", {4}, {5}) == {1, 4, 5}
    # 당일 단타는 쉼 없음 — 파는·열린 매도만
    assert pp.rest_set(pp.DAY_RULE, 체결, "2026-10-26", {4}, {5}) == {4, 5}

    from types import SimpleNamespace

    행 = [SimpleNamespace(stock_id=s, horizon=h) for s, h in ((1, "short"), (2, "mid"), (3, "short"), (4, "short"))]
    assert [r.stock_id for r in pp.short_candidates(행, {3})] == [1, 4]
