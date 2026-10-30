"""가상모의투자 스윙 계좌 S — 엔진 연결 (docs/paper.md 16.3.1, docs/infra.md 25.1871).

계좌 행은 0092(2026-10-29 개설)지만 시험은 개설일 막이 밖의 날짜로 행을 직접 넣어 본다: 단기 신호만·든 종목은 안 삼, `swing_close_v1`
(종가가 진입 시가 대비 −3%·+5%·10거래일)로 장 전 시장가 매도, 그리고 **S 가 있어도 다른 계좌의 행은 한 글자도 같다**.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import shadow as sh
from tests.test_paper_job_1445 import Client, _시세, _신호, 설정


def _계좌(accounts: tuple[str, ...], s: bool) -> Client:
    c = Client(accounts=accounts)
    if s:
        c.conn.execute(
            "INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)"
            " VALUES ('S', '가상 S (스윙)', 'swing_close', 1, '2026-10-14T00:00:00+00:00', 'open', 10, 'short')"
        )
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


def _중기() -> report_picks.SignalRow:
    return dataclasses.replace(_신호(), stock_id=3, ticker="000660", name="종목3", horizon="mid", signal_type="중기")


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str, signals: bool = True) -> dict:
    from datetime import datetime

    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    _줄, 기록 = job.run_step(
        c, market="KR", trade_date=trade_date, late=False, signal_rows=[_신호(), _중기()] if signals else [],
        settings=설정, price_series={}, series_unknown={}, allocate=signals,
        now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip
    return 기록


def _시세3(c: Client, day: str, close: float) -> None:
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (3, ?, ?, ?, ?, ?, 1000, 'KRW', 't', 't')",
        [day, close, close, close, close],
    )


def _날들(c: Client, monkeypatch: pytest.MonkeyPatch, 종가2: float) -> None:
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_000, 10_000)
    _시세3(c, "2026-10-15", 10_000)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    _시세(c, "2026-10-16", 10_050, 종가2)
    _시세3(c, "2026-10-16", 10_000)
    _하루(c, monkeypatch, "2026-10-16", signals=False)


def test_S_는_단기_신호만_시가_시장가로_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), s=True)
    _하루(c, monkeypatch, "2026-10-14")
    주문 = c.q("SELECT stock_id, side, order_type, tranche FROM paper_orders WHERE account_id = 'S'")
    assert 주문 == [(1, "buy", "market", 1)]  # 중기 신호(종목 3)는 사지 않는다, 한 번에 시가 시장가
    assert {r[0] for r in c.q("SELECT stock_id FROM paper_orders WHERE account_id = 'B'")} == {1, 3}


def test_S_는_종가가_손절선_이하면_다음_장_시가에_판다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), s=True)
    _날들(c, monkeypatch, 9_700)  # 진입 시가 10,000 → 10-16 종가 9,700 = −3% (등호 포함)
    assert c.q("SELECT price FROM paper_fills WHERE account_id = 'S' AND side = 'buy'") == [(10_000,)]
    매도 = c.q("SELECT stock_id, reason, fill_session, rationale_json FROM paper_orders"
               " WHERE account_id = 'S' AND side = 'sell'")  # fmt: skip
    assert [(s, r, f) for s, r, f, _j in 매도] == [(1, "스윙손절", "2026-10-19")]
    근거 = json.loads(매도[0][3])
    assert 근거["sell_rule"] == "swing_close" and "swing_close_v1" in 근거["flags"][0]["rationale"]
    # 결정 원장에 판 것으로 — 같은 까닭 코드
    assert c.q("SELECT kind, reason FROM paper_decisions WHERE account_id = 'S' AND kind = 'sell'") == [
        ("sell", "스윙손절")]
    # B(core, 단기 손절 −7%)는 들고 간다 — 엔진 플래그와 갈리는 자리
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B' AND side = 'sell'") == [(0,)]


def test_S_는_선_안이면_들고_가고_든_종목은_더_사지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), s=True)
    _날들(c, monkeypatch, 9_701)
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'S' AND side = 'sell'") == [(0,)]
    # 입금을 더해 목표 비중이 커져도(다른 계좌는 목표까지 더 산다) 같은 단기 신호가 다시 와도 든 종목이라 사지 않는다
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-16', 1000000, 'deposit', '2026-10-16T00:00:00+00:00', 'k2')"
    )
    _하루(c, monkeypatch, "2026-10-19")
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'S' AND side = 'buy'") == [(1,)]


def test_S_가_있어도_다른_계좌의_행은_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.3.1 '기존 계좌 무변화 장치 ④' — S 는 id 순서 맨 뒤라 앞 계좌가 보는 값이 바뀌지 않는다"""
    표들 = ("paper_orders", "paper_fills", "paper_decisions", "paper_daily")
    결과 = []
    for s in (False, True):
        c = _계좌(("A", "B", "C", "D", "E", "G", "H", "R1", "R2"), s=s)
        _날들(c, monkeypatch, 9_700)
        # 행 번호(id)는 S 행이 끼어 밀린다 — 번호를 뺀 내용으로 견준다
        결과.append({t: sorted(r[1:] for r in c.q(f"SELECT * FROM {t} WHERE account_id != 'S'")) for t in 표들})
        if s:
            assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'S'")[0][0] >= 2
    for t in 표들:
        assert 결과[0][t] == 결과[1][t], t


def test_판정은_그림자와_같은_함수() -> None:
    """엔진 판정(`_스윙넘음`)이 `shadow.close_exit` 를 그대로 쓴다 — 그림자 집계와 판정이 갈리지 않게"""
    class C:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            class R:
                rows = [("2026-10-15", 10_000.0), ("2026-10-16", 10_500.0)]

            class 빈:
                rows: list = []
            if "kr_corp_events" in sql:  # 권리락 일정(25.1999) — 이 종목엔 없다
                return 빈()
            assert sql == job.SWING_CLOSES_SQL and args[-1] == sh.SWING_CLOSE.max_sessions
            return R()

    행 = {"stock_id": 1, "lots": [("2026-10-15", 4.0, 10_000.0)]}
    팖 = job._스윙넘음(C(), 행, date(2026, 10, 19))
    assert 팖 is not None and 팖["reason_code"] == pp.SWING_CODES["target"] == "스윙목표"
    # 권리락이 없으면 되돌림 근거·꼬리가 없다(25.2000)
    assert [c["label"] for c in 팖["criteria"]] == ["진입가", "판정 종가"] and "되돌림" not in 팖["rationale"]
    assert job._스윙넘음(C(), {"stock_id": 1, "lots": []}, date(2026, 10, 19)) is None


def test_스윙_사유_코드는_손절과_이름이_다르다() -> None:
    # `손절` 이면 "손절 보유의 남은 회차 거둠" 길을 탄다(paper.md 16.3.1)
    assert pp.SELL_RULES[pp.SWING_RULE] == frozenset(pp.SWING_CODES.values())
    assert not pp.SELL_RULES[pp.SWING_RULE] & {"손절", "목표도달", "기간초과"}


def test_모르는_매도_규칙의_계좌는_그_계좌만_건너뛴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """16.3.1 '코드가 먼저, 행은 나중' — 순서가 뒤집혀도 다른 계좌(H 와 그 뒤 KIS 베끼기)는 돈다"""
    c = _계좌(("B",), s=False)
    c.conn.execute(
        "INSERT INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection)"
        " VALUES ('Z', '모름', 'zzz', 1, '2026-10-14T00:00:00+00:00', 'open', 10, 'short')"
    )
    다음 = cal.next_session("KR", date(2026, 10, 14))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    from datetime import datetime

    줄, 기록 = job.run_step(c, market="KR", trade_date="2026-10-14", late=False, signal_rows=[_신호()], settings=설정,
                          price_series={}, series_unknown={}, allocate=True,
                          now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"))  # fmt: skip
    assert 기록["accounts"]["Z"] == {"error": "모르는 매도 규칙 zzz"}
    assert 기록["accounts"]["B"]["orders"] >= 1
    # 리포트 경고 줄과 상황실 확인할 일에도(25.1873 — 텔레그램 줄만 있고 상황실엔 안 보였다)
    assert "⚠️ 가상 Z 모르는 매도 규칙(zzz) — 오늘 이 계좌는 평가·매매하지 않았습니다" in 줄
    from batch.services import desk

    확인 = desk.checks(halted=False, paper=기록, broker=None, recon_bad=[], warnings=0)
    assert any(x["level"] == "high" and "가상 Z 모르는 매도 규칙 zzz" in x["text"] for x in 확인)


def test_S_의_결정은_기존_계좌의_성과_채우기_몫을_나눠_쓰지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1872 (16.3.1 무변화 장치 ①) — 시장마다 창 하나에 하루 `OUTCOME_BATCH` 인 대기열을 S 가 나누면 밀린 날 H 회사
    가중치가 읽는 `ret_20d` 가 늦었다. 바퀴를 둘로 — 상한을 2 로 줄이고 S 결정 3·B 결정 2 를 쌓아 본다"""
    c = _계좌(("B",), s=True)
    monkeypatch.setattr(job, "OUTCOME_BATCH", 2)
    # S 결정이 더 최근이라 최근 것부터 읽는 질의에서 S 가 먼저 읽힌다 — 바퀴가 하나면(또는 기존 바퀴가 S 를 빼지
    # 않으면) 상한 2 를 S 가 다 먹어 B 가 하나도 안 읽힌다(25.1873, 25.1872 교차검증 [권고] — 같은 날이면 순서에 기댔다)
    for aid, sid, 날 in (("S", 1, "2026-10-02"), ("S", 3, "2026-10-02"), ("S", 2, "2026-10-02"),
                        ("B", 1, "2026-10-01"), ("B", 3, "2026-10-01")):
        c.conn.execute(job.DECISION_INSERT_SQL, [aid, "KR", 날, sid, "buy", "r", None, 10_000.0, 날, "t", 1])
    본: list[str] = []
    원래 = c.execute

    def 엿봄(sql, args=None):  # noqa: ANN001, ANN202
        if "FROM paper_decisions" in sql and "ret_5d IS NULL" in sql and "outcome_note IS NULL" in sql:
            for r in 원래(sql, args).rows:
                본.append(c.conn.execute("SELECT account_id FROM paper_decisions WHERE id = ?", [r[0]]).fetchone()[0])
        return 원래(sql, args)

    monkeypatch.setattr(c, "execute", 엿봄)
    job.fill_outcomes(c, "KR", "2026-10-28", "t", "2026-10-27")
    assert 본.count("B") == 2  # S 가 셋이어도 B 의 두 줄은 모두 읽힌다(상한 2 를 S 와 나누지 않는다)
    assert 본.count("S") == 2  # S 는 제 바퀴 상한 안에서


def test_상황실은_S_를_공통_구간과_챔피언_후보에서_따로_둔다() -> None:
    """25.1872 (16.3.1 무변화 장치 ②) — S 가 열리는 날 A~G 의 순위표 공통 구간 시작이 S 개설일로 밀리고, 챔피언 후보의
    유의수준(α/도전자 수)이 S 하나만큼 빡빡해졌다"""
    from batch.services import desk

    날 = [f"2026-{m:02d}-{d:02d}" for m in (9, 10, 11) for d in range(1, 29)]
    계열 = {a: [(d, 1 + i * 0.001) for i, d in enumerate(날)] for a in ("B", "H", "R1")}
    계열["S"] = [(d, 1 + i * 0.002) for i, d in enumerate(날[40:])]  # 늦게 열고 20 평가를 넘김
    표 = desk.leaderboard(계열)
    assert 표["window_start"] == 날[0]  # S 개설일로 밀리지 않는다
    s줄 = next(r for r in 표["rows"] if r["id"] == "S")
    assert s줄["separate"] and not s줄["young"] and 표["rows"][-1]["id"] == "S"
    assert s줄["window_pct"] == pytest.approx((계열["S"][-1][1] / 계열["S"][0][1] - 1) * 100)
    assert all(not r["separate"] for r in 표["rows"] if r["id"] != "S")
    assert [r["id"] for r in desk.champion(계열, "H")] == ["B"]  # S 는 도전자 수에 들지 않는다
    assert desk.SEPARATE_IDS == pp.SEPARATE_IDS
    assert desk.prereg("S", 1) is not None and desk.KIND["S"]



# ── 25.1873 (25.1871·25.1872 교차검증 [권고]) ─────────────────────────────────────────────────


def _날짜들(n: int) -> list[str]:
    d, out = date(2026, 10, 15), []
    for _ in range(n):
        out.append(d.isoformat())
        d = cal.next_session("KR", d)
    return out


def test_S_는_진입한_날_종가도_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    """진입한 날이 1거래일째 — 그날 종가가 손절선이면 다음 장 시가에 판다(`date >= 첫 매수일`)"""
    c = _계좌(("B",), s=True)
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_000, 9_650)
    _시세3(c, "2026-10-15", 10_000)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert c.q("SELECT reason, fill_session FROM paper_orders WHERE account_id = 'S' AND side = 'sell'") == [
        ("스윙손절", "2026-10-16")]


def test_S_는_10거래일째_종가에_기한으로_판다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _계좌(("B",), s=True)
    _하루(c, monkeypatch, "2026-10-14")
    날들 = _날짜들(10)
    for i, d in enumerate(날들):
        _시세(c, d, 10_000, 10_100)
        _시세3(c, d, 10_000)
        if i in (0, 8, 9):  # 체결하는 날, 9번째, 10번째에만 돌린다
            _하루(c, monkeypatch, d, signals=False)
            팖 = c.q("SELECT reason, fill_session FROM paper_orders WHERE account_id = 'S' AND side = 'sell'")
            assert 팖 == ([] if i < 9 else [("스윙기한", cal.next_session("KR", date.fromisoformat(d)).isoformat())]), i


def test_S_판정은_시가가_빈_날을_세지_않고_첫_로트에서_잰다(monkeypatch: pytest.MonkeyPatch) -> None:
    """그림자(`load_bars`)처럼 시가·고가·저가·종가가 다 있는 날만 — 엔진 질의가 그림자와 같은 날을 센다. 선은 첫 로트"""
    c = _계좌(("B",), s=False)
    c.conn.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (1, '2026-10-15', 10000, 10000, 10000, 10000, 1, 'KRW', 't', 't'),"
        "        (1, '2026-10-16', NULL, 9600, 9600, 9600, 1, 'KRW', 't', 't'),"
        "        (1, '2026-10-19', 10000, 10000, 10000, 10000, 1, 'KRW', 't', 't')"
    )
    불림: list[tuple] = []
    진짜 = sh.close_exit
    monkeypatch.setattr(sh, "close_exit", lambda e, cs, r=sh.SWING_CLOSE: 불림.append((e, list(cs))) or 진짜(e, cs, r))
    로트 = [("2026-10-16", 1.0, 11_000.0), ("2026-10-15", 4.0, 10_000.0)]  # 순서가 섞여도 첫 로트(10-15)
    assert job._스윙넘음(c, {"stock_id": 1, "lots": 로트}, date(2026, 10, 20)) is None  # 10-16(시가 없음)은 안 셈
    assert 불림 == [(10_000.0, [10_000.0, 10_000.0])]  # 엔진이 그림자와 같은 함수를 부른다


def test_사면_바로_팔_후보를_걸러도_S_거르개가_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`if 즉시매도:` 가 signal_rows 에서 다시 만들어 S 거르개(단기만·든 종목 뺌)를 지웠다(잠복 — 지금은 S 규칙에서
    즉시매도가 늘 빈다). 즉시매도가 단기 신호(종목 1)를 거르면 S 는 아무것도 사지 않아야 한다 — 중기(종목 3)를 사면 안 된다"""
    c = _계좌(("B",), s=True)
    monkeypatch.setattr(job, "prebuy_sell_flags", lambda *a, **k: {1: ["재무악화"]})
    _하루(c, monkeypatch, "2026-10-14")
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'S'") == [(0,)]


def test_S_는_KIS_베끼기_장중_감시_대조_지주사_표_밖() -> None:
    """16.3.1 무변화 장치 ③ — 상수로 묶는다"""
    # 모듈 값이 아니라 정의 글로 — 다른 시험이 `MIRROR_ACCOUNT` 를 바꾼 동안 장중 감시 모듈이 처음 읽히면 그 값으로 굳어
    # 함께 돌릴 때만 거짓으로 깨졌다(25.1882, 25.1881 교차검증 [참고])
    import inspect
    import json as _json

    from batch.jobs import broker_mirror, intraday_guard
    from batch.services import desk

    assert '\nMIRROR_ACCOUNT = "H"\n' in inspect.getsource(broker_mirror)
    assert "\nGUARD_ACCOUNT = bmj.MIRROR_ACCOUNT\n" in inspect.getsource(intraday_guard)
    assert "S" not in desk.CONTROL_IDS
    c = Client(accounts=None, charters=True)
    헌장 = _json.loads(c.q("SELECT charter_json FROM paper_accounts WHERE id = 'H'")[0][0])
    assert 헌장["voters"] and "S" not in 헌장["voters"]  # 투표 회사 목록에 없다(25.1874 — 글자 찾기 대신 파싱)


def test_월간_복기의_손절_점검은_S_에_해당_없음() -> None:
    행 = [{"as_of": "2026-10-16", "invested_krw": 1_000, "equity_krw": 1_000, "twr_index": 1.0, "positions_json": "[]"}]
    줄 = pp.summary_lines({"B": 행, "S": 행}, {}, "2026-10-12", "2026-10-01", {}, {}, {}, {"S": "swing_close"})
    s줄 = next(x for x in 줄 if x.startswith("  스윙 규칙 점검"))
    assert "해당 없음(스윙 규칙" in s줄 and "손절선 아래로 들고 간 날" not in s줄
    assert "−3% 이하" in s줄  # 규칙 정의처의 값, 다른 글과 같은 마이너스 기호(25.1876)
    assert any(x.startswith("  기준 규칙 점검") and "손절선 아래로 들고 간 날 0" in x for x in 줄)


def test_KIS_보정_이웃_견줌과_운용_보고_순위에서_S_를_뺀다() -> None:
    from batch.services import desk

    표 = {"window_start": "2026-09-01", "rows": [
        {"id": "B", "window_pct": 5.0, "young": False, "separate": False},
        {"id": "R1", "window_pct": 1.0, "young": False, "separate": False},
        {"id": "S", "window_pct": 1.1, "young": False, "separate": True}]}  # fmt: skip
    보정 = {a: {"adj_pp": 0.0, "pi_pp": 0.0, "band_pp": 0.5} for a in ("B", "R1", "S")}
    out = desk.adjust_rows(표, 보정)
    assert out["R1"]["close_call"] is False and out["S"]["close_call"] is False  # 다른 구간끼리 견주지 않는다
    줄 = desk.report_lines({}, 표, "H")
    assert any(x.strip().startswith("1.") and "기준" in x for x in 줄)  # 순위 줄이 실제로 나온다
    import re

    assert not any(re.match(r"^\s*\d+\. 스윙\b", x) for x in 줄)  # S 는 순위 줄("  3. 스윙 +1.10%")에 없다



def test_KIS_보정_구간은_S_의_개설일부터(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1874 (25.1873 교차검증 [권고]) — S 의 순위 값은 개설일부터인데 보정 구간 시작이 공통 구간이면 어긋난다"""
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
        {"id": "S", "open": "2026-11-04", "young": False, "separate": True, "window_pct": 2.0}]}  # fmt: skip
    desk_job._보정판(C(), 순위, {}, {}, "H", {}, {}, "2026-11-20")
    assert 받은 == {"B": "2026-09-01", "S": "2026-11-04"}



def test_월간_S_문구는_규칙_값을_따라간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1876 (25.1874 교차검증 [참고]) — 기대값을 코드와 같은 식으로 만들면 글로 박아도 통과했다. 값을 바꿔 본다"""
    monkeypatch.setattr(sh, "SWING_CLOSE", dataclasses.replace(sh.SWING_CLOSE, stop_pct=-0.04))
    행 = [{"as_of": "2026-10-16", "invested_krw": 1_000, "equity_krw": 1_000, "twr_index": 1.0, "positions_json": "[]"}]
    줄 = pp.summary_lines({"S": 행}, {}, "2026-10-12", "2026-10-01", {}, {}, {}, {"S": "swing_close"})
    assert any("−4% 이하" in x for x in 줄)


def test_S_판정은_유상증자_권리락을_그림자처럼_되돌려_견준다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1999 (결함 루프) — 원시 종가로 견주면 권리락 날의 기계적 하락(계수 0.911)이 거짓 "스윙손절" 이었다.
    그림자(수정주가 — 락일 앞 값에 계수)와 같은 비율로: 락일부터의 종가만 계수로 되돌린다. 진입가 전체에 곱하면 락일
    **앞** 종가(+4.5%)가 낮아진 진입가와 견주어져 거짓 목표(+14.7%)가 난다"""
    from batch.jobs import monitor_targets as mt

    f = 0.911
    받음: list[tuple] = []
    # 넘기는 종목·진입일까지 묶는다 — since 를 오늘로 바꾸면 운영에선 늘 빈 목록이라 결함이 되돌아온다(25.2000,
    # 25.1999 교차검증 [권고])
    monkeypatch.setattr(mt, "rights_factors_since",
                        lambda _c, sid, since: 받음.append((sid, since)) or [("2026-10-30", f)])  # fmt: skip

    def 판정(종가들: list[tuple[str, float]]) -> dict | None:
        class C:
            def execute(self, sql, args=None):  # noqa: ANN001, ANN201
                class R:
                    rows = 종가들
                assert sql == job.SWING_CLOSES_SQL
                return R()

        return job._스윙넘음(C(), {"stock_id": 1, "country": "KR", "lots": [("2026-10-29", 10.0, 10_000.0)]},
                            date(2026, 11, 2))  # fmt: skip

    # 락일 앞 +4.5%, 락일 원시 −5%(되돌리면 +4.3%) — 들고 간다
    assert 판정([("2026-10-29", 10_450.0), ("2026-10-30", 9_500.0)]) is None
    assert 받음 == [(1, "2026-10-29")]
    # 그림자 수정주가 계열과 같은 판정(진입 시가·락일 앞 종가에 계수)
    assert sh.close_exit(10_000.0 * f, [10_450.0 * f, 9_500.0]) is None
    # 락일 뒤 진짜 하락(8,700 → 되돌려 9,550, −4.5%)은 손절 — 근거에 계수를 적는다
    팖 = 판정([("2026-10-29", 10_000.0), ("2026-10-30", 9_500.0), ("2026-10-31", 8_700.0)])
    assert 팖 is not None and 팖["reason_code"] == "스윙손절" and "-4.5" in 팖["rationale"]
    assert "(권리락 되돌림 ×0.911)" in 팖["rationale"]  # 원시 판정 종가 ÷ 진입가(−13%)와 다른 까닭
    assert sh.close_exit(10_000.0 * f, [10_000.0 * f, 9_500.0, 8_700.0]) == ("stop", 2)
    # 근거는 웹 근거표 꼴(label·display·threshold·source·as_of — 25.2000, 예전 꼴은 빈 칸으로 그려졌다)
    assert [c["label"] for c in 팖["criteria"]] == ["진입가", "판정 종가", "권리락 되돌림"]
    assert all(c["display"] and c["threshold"] and c["source"] and c["as_of"] for c in 팖["criteria"])
    assert 팖["criteria"][2]["display"].startswith("×0.911 → 되돌린 종가 9,550원")
    assert 팖["criteria"][1]["display"] == "8,700원 (3거래일째)" and 팖["criteria"][1]["as_of"] == "2026-10-31"
    # 진입가 행은 진입일·진입가 그대로, 판정은 "판정 종가" 행 하나(진입가·되돌림은 참고) — 25.2001
    assert 팖["criteria"][0] == {"label": "진입가", "display": "10,000원", "threshold": "첫 매수 체결가(슬리피지 전)",
                                 "source": "가상모의투자 체결", "as_of": "2026-10-29", "passed": None}
    assert [c["passed"] for c in 팖["criteria"]] == [None, True, None]
    # 진입일 = 락일이면 되돌리지 않는다(이미 떨어진 값에 샀다) — 가짜가 거르지 않아도 막는다(25.2001)
    monkeypatch.setattr(mt, "rights_factors_since", lambda *_a: [("2026-10-29", f)])
    assert 판정([("2026-10-29", 10_000.0), ("2026-10-30", 10_200.0)]) is None
    # 미국은 권리락 일정을 읽지 않는다
    monkeypatch.setattr(mt, "rights_factors_since", lambda *_a: (_ for _ in ()).throw(AssertionError("읽음")))

    class U:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            class R:
                rows = [("2026-10-29", 100.0)]
            return R()

    assert job._스윙넘음(U(), {"stock_id": 2, "country": "US", "lots": [("2026-10-29", 1.0, 100.0)]},
                        date(2026, 10, 29)) is None  # fmt: skip

    # 미국 보유 근거는 달러로 찍는다(원으로 찍으면 "100원") — 25.2001
    class U2:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            class R:
                rows = [("2026-10-29", 106.0)]
            return R()

    팖 = job._스윙넘음(U2(), {"stock_id": 2, "country": "US", "currency": "USD", "lots": [("2026-10-29", 1.0, 100.0)]},
                      date(2026, 10, 29))  # fmt: skip
    assert 팖 is not None and [c["display"] for c in 팖["criteria"]] == ["100.00 USD", "106.00 USD (1거래일째)"]


def test_swing_exit_순수_함수는_권리락을_진입_뒤_날마다만_되돌린다() -> None:
    """S 판정의 순수 함수(25.2030 — 리플레이와 같이 쓴다). 락일 = 진입일이면 세지 않고, 락일 뒤 날만 그 계수로 나눈다."""
    from batch.services import shadow as sh

    종가 = [("2026-10-01", 100.0), ("2026-10-02", 99.0), ("2026-10-05", 90.0)]
    # 10-05 에 계수 0.9 권리락 — 되돌리면 100 → 손절 아님, 원시 90 은 −10% 손절
    assert sh.swing_exit("2026-10-01", 100.0, 종가, [("2026-10-05", 0.9)]) is None
    assert sh.swing_exit("2026-10-01", 100.0, 종가, []) == ("stop", 2, 90.0, 1.0)
    # 진입일 = 락일은 세지 않는다
    assert sh.swing_exit("2026-10-05", 90.0, [("2026-10-05", 90.0), ("2026-10-06", 86.0)], [("2026-10-05", 0.9)]) == (
        "stop", 1, 86.0, 1.0)  # fmt: skip
    # 목표 — 되돌린 종가·계수를 돌려준다
    got = sh.swing_exit("2026-10-01", 100.0, [("2026-10-01", 100.0), ("2026-10-02", 95.0)], [("2026-10-02", 0.9)])
    assert got is not None and got[0] == "target" and abs(got[2] - 95.0 / 0.9) < 1e-9 and got[3] == 0.9
    # 기한 — 10번째 종가
    열흘 = [(f"2026-10-{d:02d}", 100.0) for d in range(1, 11)]
    assert sh.swing_exit("2026-10-01", 100.0, 열흘) == ("time", 9, 100.0, 1.0)
    assert sh.swing_exit("2026-10-01", 100.0, 열흘[:9]) is None
