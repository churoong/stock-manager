"""S·T 리플레이 ↔ 운영 일치 시험 (깔때기 ⑦-5, 25.2037).

같은 합성 자료(시가 ≠ 종가, 상한가 시가, 하한가 종가, 거래량 0 정지)와 같은 신호 행으로
- 운영: 날마다 그날 시세를 넣고 `jobs/paper.run_step`(그 안에서 지난 장 주문을 `fill_orders` 로 판정 — 운영 장부가
  스스로 찬다. 1910 의 "엔진 체결 베끼기" 허용 ① 이 없다)
- 리플레이: `services/swing_day_replay.simulate_account`
를 돌려 **체결 하나하나**(날·종목·방향·가격·수량·현금 영향)와 날마다 현금이 같은지 본다. 비용 판은 운영 기본 비용
(`jobs/paper.costs_for` — 설정이 비면 백테스트 기본값). 허용(이름 붙은 차이) 없이 같아야 한다 — 이 자료는 모듈 머리의
차이 ①~⑤ 가 닿지 않게 짰다(권리락 없음, 정지는 거래량 0 행, 늘 장 전 결정, 시가가 모든 날 있음).
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import paper as pp
from batch.services import report_picks
from batch.services import swing_day_replay as sdr
from tests.test_core_strategy_1532 import _행
from tests.test_paper_day_1883 import _계좌

_달력 = cal.exchange_calendar("KR").sessions_in_range("2026-03-02", "2026-04-30")
날들 = [d.date().isoformat() for d in _달력][:18]
설정 = {"max_weight_per_sector": 30.0, "max_weight_per_stock": 10.0, "min_order_amount": 0}
입금 = 10_000_000.0

#: 종목 → 날마다 (시가, 종가, 거래량). 사건: 1 은 오르다 목표(+5%), 2 는 손절 뒤 매도 날 시가가 하한가(−30%)라 미룸,
#: 3 은 상한가 시가(505 → 655, +29.7% — 가격제한폭 끝 656 아래라 `judge` 는 차고 T 진입 막기가 거른다)와 거래량 0 정지가 낀다
def _자료() -> dict[int, list[tuple[float, float, int]]]:
    n = len(날들)
    s1 = [(1000.0 + 6 * i, 1003.0 + 7 * i, 1000) for i in range(n)]
    s2 = [(2000.0, 2000.0, 1000)] * 3 + [(1990.0, 1930.0, 1000), (1351.0, 1351.0, 1000)] + [
        (1351.0, 1360.0, 1000)] * (n - 5)  # fmt: skip
    s3 = [(500.0, 500.0, 1000), (500.0, 505.0, 1000), (655.0, 650.0, 1000)] + [(650.0, 640.0, 1000)] * 3 + [
        (640.0, 640.0, 0)] + [(645.0, 646.0, 1000)] * (n - 7)  # fmt: skip
    # 날들[11] 종목 1 시가 갭 상승(+25%, 상한가 시가 막기 29.5% 아래) — 빠듯한 판(신호 비중 50%)에서 먼저 판정되는 1 이 예상보다 비싸 차, 같은 장
    # 2 의 예약을 빼면 현금이 모자라 만료되는 길(25.2039)
    s1[11] = (1341.0, s1[11][1], 1000)
    return {1: s1, 2: s2, 3: s3}


#: 신호 행 — 판단일(cutoff)마다 단기 신호 종목
#: 날들[9]·[10] 의 2 는 판 뒤 쉼 경계(S 가 2 를 날들[5] 에 판다 — cutoff 날들[9] 는 쉼 안, 날들[10] 은 밖, 25.2039),
#: 날들[5] 의 3 은 T 가 거래량 0 정지 날(날들[6]) 에 사려다 만료되는 길
신호: dict[str, list[int]] = {날들[0]: [1, 2], 날들[1]: [3], 날들[2]: [3], 날들[4]: [1, 3], 날들[5]: [3],
                              날들[8]: [2, 3], 날들[9]: [2], 날들[10]: [1, 2, 3], 날들[12]: [2]}  # fmt: skip


def _종가(sid: int, d: str) -> float:
    return _자료()[sid][날들.index(d)][1]


#: 묵은 신호 행 — (판단일, 종목) → 기준 종가 날. 관문의 묵음 막힘(`rg.STALE_PRICE_SESSIONS`)을 운영과 같은 길로
#: 지나게 한다(25.2040, 25.2039 교차검증 [권고]: 관문 연결은 그때까지 아무 시험도 지나지 않았다)
묵은: dict[tuple[str, int], str] = {(날들[12], 2): 날들[3]}


def _행들(cutoff: str, weight: float = 10.0) -> list[report_picks.SignalRow]:
    out = []
    for sid in 신호.get(cutoff, []):
        기준 = 묵은.get((cutoff, sid), cutoff)
        out.append(dataclasses.replace(_행(sid, cutoff, _종가(sid, 기준), weight=weight, sector="전자"),
                                       close_date=기준))  # fmt: skip
    return out


@pytest.fixture(autouse=True)
def _고정(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, _n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())


막힘_기록: dict[str, dict[str, int]] = {}


def _운영(account: str, monkeypatch: pytest.MonkeyPatch, 설정: dict = 설정) -> tuple[list[pp.Fill], dict[str, float]]:
    c = _계좌(("B",), t=account == "T", s=account == "S")
    c.conn.execute("UPDATE paper_cash_flows SET amount_krw = ?, flow_date = ?, created_at = ?",
                   [int(입금), 날들[0], f"{날들[0]}T00:00:00+00:00"])
    # 계좌 개설일 전엔 사지 않는다(25.1891) — 합성 창 앞으로 당긴다
    c.conn.execute("UPDATE paper_accounts SET created_at = '2026-01-02T00:00:00+00:00' WHERE id = ?", [account])
    c.conn.execute("DELETE FROM stocks WHERE id NOT IN (1, 2, 3)")
    for sid in (1, 2, 3):
        if not c.q("SELECT 1 FROM stocks WHERE id = ?", [sid]):
            c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, sector, currency, status, source,"
                           " fetched_at, asset_type) VALUES (?, ?, 'KOSPI', 'KR', ?, '전자', 'KRW', 'active', 't', 't',"
                           " 'stock')", [sid, f"{sid:06d}", f"종목{sid}"])  # fmt: skip
    # 바탕 고정 자료의 2 는 미국(AAPL) — 국내 종목으로 맞춘다(운영 매도 판정은 그 시장 종목만 본다)
    c.conn.execute("UPDATE stocks SET sector = '전자', ticker = printf('%06d', id), country = 'KR', market = 'KOSPI',"
                   " currency = 'KRW', asset_type = 'stock' WHERE id IN (1, 2, 3)")
    현금: dict[str, float] = {}
    for i, d in enumerate(날들):
        for sid, 줄 in _자료().items():
            o, cl, v = 줄[i]
            c.conn.execute(
                "INSERT INTO prices (stock_id, date, open, high, low, close, volume, change_pct, currency, source,"
                " fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'KRW', 't', 't')",
                [sid, d, o, max(o, cl), min(o, cl), cl, v, (cl / 줄[i - 1][1] - 1) * 100 if i else None],
            )  # fmt: skip
        if i == len(날들) - 1:
            break
        다음 = 날들[i + 1]
        오늘 = date.fromisoformat(다음)
        monkeypatch.setattr(cal, "local_today", lambda _m=None, _d=오늘: _d)
        job.run_step(c, market="KR", trade_date=d, late=False, signal_rows=_행들(d, 설정.get("_weight", 10.0)), settings=설정, price_series={},
                     series_unknown={}, allocate=True, now=datetime.fromisoformat(f"{다음}T00:00:00+00:00"))  # fmt: skip
        체결 = [f for f in job._fills(c)[0] if f.account_id == account]
        현금[d] = pp.cash([pp.CashFlow(1, 날들[0], 입금, "deposit")], 체결)
    # 운영 관문 막힘 — 결정 원장 blocked 를 체결 장(판단일 다음 장)으로(리플레이 기록과 같은 날 축)
    막힘_기록[account] = {}
    for 판단일, n in c.q("SELECT decided_on, COUNT(*) FROM paper_decisions WHERE account_id = ? AND kind = 'blocked'"
                         " GROUP BY decided_on", [account]):  # fmt: skip
        막힘_기록[account][날들[날들.index(판단일) + 1]] = n
    return [f for f in job._fills(c)[0] if f.account_id == account], 현금


def _리플레이(account: str, 설정: dict = 설정) -> sdr.STResult:
    prices = {sid: {d: 줄[i][1] for i, d in enumerate(날들)} for sid, 줄 in _자료().items()}
    opens = {sid: {d: 줄[i][0] for i, d in enumerate(날들)} for sid, 줄 in _자료().items()}
    turnover = {sid: {d: float(줄[i][2]) * 줄[i][1] for i, d in enumerate(날들)} for sid, 줄 in _자료().items()}
    캐시 = {sid: {"id": sid, "ticker": f"{sid:06d}", "name": f"종목{sid}", "sector": "전자", "country": "KR",
                  "currency": "KRW", "asset_type": "stock"} for sid in (1, 2, 3)}  # fmt: skip
    return sdr.simulate_account(account, rows_at=lambda view, _d: _행들(view.cutoff, 설정.get("_weight", 10.0)), prices=prices, opens=opens,
                                dates=날들, costs_at=lambda d: job.costs_for("KR", None, None, d), stock_cache=캐시,
                                settings=설정, deposit=입금, turnover=turnover)  # fmt: skip


def _꼴(fills: list[pp.Fill]) -> list[tuple]:
    return sorted((f.fill_date, f.stock_id, f.side, round(f.price, 6), f.quantity, round(f.cash_krw, 4)) for f in fills)


@pytest.mark.parametrize("판", ["보통", "빠듯"])
@pytest.mark.parametrize("account", ["S", "T"])
def test_체결_하나하나가_운영과_같다(account: str, 판: str, monkeypatch: pytest.MonkeyPatch) -> None:
    설정판 = 설정 if 판 == "보통" else 빠듯
    운영, 현금 = _운영(account, monkeypatch, 설정판)
    # 마지막 날 체결은 운영이 그날 run_step 을 안 돌려 판정되지 않는다 — 리플레이도 마지막 날 앞까지만 견준다
    끝 = 날들[-1]
    r = _리플레이(account, 설정판)
    rp = [f for f in r.fills if f.fill_date < 끝]
    assert 운영, "운영이 아무것도 안 샀다 — 자료가 견줄 거리를 못 지난다"
    assert _꼴(rp) == _꼴(운영)
    # 날마다 현금(그날 판단 뒤 = 그날까지의 체결) — 운영은 날 d 의 run_step 이 d 장 주문을 판정한 뒤
    for d, x in 현금.items():
        assert abs(pp.cash([pp.CashFlow(1, 날들[0], 입금, "deposit")], [f for f in r.fills if f.fill_date <= d]) - x) < 1e-6, d


#: 현금이 빠듯한 판(25.2039, 25.2037 교차검증 [권고]) — 종목 상한을 크게 둬 한 신호가 현금 대부분을 쓰게 해, 같은 장
#: 다른 매수 예약 때문에 만료되는 길과 관문 막힘을 지나게 한다
빠듯 = {"max_weight_per_sector": 100.0, "max_weight_per_stock": 60.0, "min_order_amount": 0, "_weight": 50.0}


def test_자료가_견줄_거리를_지난다() -> None:
    """S 는 목표·손절·하한가 미룸·쉼 경계, T 는 상한가 시가 안 삼·종가 짝 매도·정지 날 만료, 빠듯한 판은 예약·관문 —
    리플레이가 실제로 그 길을 지나는지(25.2039: 예전 단언은 `사유 is not None` 처럼 늘 참인 것이 섞였다)."""
    s = _리플레이("S")
    판것 = {k for g in s.log for k in g.get("sold", {})}
    assert {pp.SWING_CODES["target"], pp.SWING_CODES["stop"]} <= 판것, 판것
    assert sum(g["deferred"] for g in s.log) >= 1  # 하한가 잠김 매도 미룸
    산날 = [f.fill_date for f in s.fills if f.side == "buy" and f.stock_id == 2]
    assert 날들[10] not in 산날 and 날들[11] in 산날  # 쉼 경계 — cutoff 날들[9] 막힘, 날들[10] 삼(체결은 다음 장)
    t = _리플레이("T")
    t사유 = {k for g in t.log for k in g["expired"]}
    assert any(k.startswith("상한가 시가") for k in t사유) and "단타 짝 매수 안 참" in t사유
    assert any(k.startswith("정지") for k in t사유), t사유
    assert pp.DAY_CLOSE_CODE in {k for g in t.log for k in g.get("sold", {})}
    # 관문 묵음 막힘(묵은 신호 행) — T 가 실제로 지난다(25.2040). 운영도 같은 날 결정 원장에 blocked 를 남긴다
    # (`test_관문_막힘이_운영과_같다`)
    assert sum(g.get("blocked", 0) for g in t.log) >= 1
    # 같은 장 예약 현금 부족은 빠듯한 판 T 가 지난다 — 이 글은 예약이 0 이어도 같은 고정 문구라 **길이 있음** 만 본다.
    # 예약 연결 자체는 `test_체결_하나하나가_운영과_같다[T-빠듯]` 이 묶는다(예약을 0 으로 바꾸면 체결이 갈린다)
    빠 = _리플레이("T", 빠듯).log
    assert "현금 부족 (다른 미체결 매수 예약 포함)" in {k for g in 빠 for k in g["expired"]}


def test_관문_막힘이_운영과_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """리플레이 T 의 관문 막힘(날마다 수)이 운영 결정 원장의 blocked 와 같다(25.2040)."""
    _운영("T", monkeypatch)
    운영막힘 = 막힘_기록["T"]
    rp = {g["day"]: g.get("blocked", 0) for g in _리플레이("T").log if g.get("blocked")}
    assert 운영막힘 and rp == 운영막힘
