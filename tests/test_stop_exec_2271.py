"""손절 매도 집행 판정 — 사전 등록 둘 (docs/factors.md 12.53, docs/infra.md 25.2271).

문이 차기 전에는 수만 내고 값을 내지 않는지, 제외·중복·문·통과 규칙이 등록 문장 그대로인지, 읽기 질의가 실제 스키마에서
도는지를 손으로 센 고정 자료로 본다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from batch.core import db
from batch.jobs import stop_judge as job
from batch.services import paper as pp
from batch.services import stop_exec as sx
from tests.test_portfolio_job import MemClient

ROOT = Path(__file__).resolve().parent.parent
D0 = date(2026, 12, 1)  # 표본 안의 화요일


def _e(i: int = 0, **kw: Any) -> dict[str, Any]:
    """손절 매도 한 건 — 결정일 D, 체결 D+1 시가 95(결정 종가 100 — 갭 −5%), 체결일 종가 97, 5거래일 뒤 +2%"""
    d = (D0 + timedelta(days=i)).isoformat()
    n = (D0 + timedelta(days=i + 1)).isoformat()
    base = {"account_id": "B", "market": "KR", "stock_id": 1, "decided_on": d, "reason": "손절", "ref_close": 100.0,
            "ref_date": d, "ret_5d": 2.0, "ret_60d": -20.0, "bench_5d": 1.0, "outcome_note": None, "order_type": "market",
            "status": "filled", "deferrals": 0, "fill_basis": "open", "close_note": None, "fill_date": n, "price": 95.0,
            "day_close": 97.0, "horizon": "mid", "ticker": "000001", "next_session": n, "bar_open": 95.0, "bar_close": 97.0,
            "bar_change_pct": -3.0, "has_next": True, "next_open": 96.0, "next_close": 96.5, "div_ex": False,
            "lock_window": False}  # fmt: skip
    return {**base, **kw}


def test_등록_상수가_사전_등록_문장과_같다() -> None:
    글 = (ROOT / "docs" / "factors.md").read_text(encoding="utf-8")
    절 = 글[글.index("### 12.53 50회차"):글.index("### 12.6 지난 회차 판정 상태")]
    assert "·".join(sx.ACCOUNTS) in 절  # 계좌 집합 id 고정
    assert f"α = {sx.ALPHA}" in 절 and f"alpha={sx.ALPHA}" in 절
    사건, 날, 묶음 = sx.PAIR_GATE
    assert f"g0 < 0 사건 ≥ {사건}" in 절 and f"그런 체결일 ≥ {날}" in 절 and f"그런 묶음 ≥ {묶음}" in 절
    assert f"**{sx.PAIR_DEADLINE}** 까지 문 미달" in 절 and f"**{sx.CLUSTER_DEADLINE}** 뒤 첫 실행" in 절
    assert f"그런 묶음 **{sx.CLUSTER_BLOCKS}** 개" in 절 and f"그때 {sx.CLUSTER_MIN_BLOCKS} 이상이면 판정" in 절
    assert f"마지막 달력일 + {sx.MATURE_DAYS}일" in 절 and f"서로 다른 종목이 {sx.CLUSTER_MIN_STOCKS} 이상" in 절
    assert f"{sx.VETO_WEIGHT:.2f} × ΣΔ5(%) < −{abs(sx.VETO_PCT):.1f}" in 절
    # 표본 시작은 이 모듈 커밋의 KST 날짜 다음 날 — 등록 문장(25.2270) 뒤, 그리고 한 번도 값이 나지 않았다
    assert sx.SAMPLE_FROM == "2026-11-25" and sx.JUDGED == {}
    # 원장 행
    assert re.search(r"\| 45 \| 손절 매도 집행 시각 짝 `stop_exec_pair`", 글) and "| 46 | 군집 손절일 유예" in 글


def test_짝_값은_시가_대_종가이고_여섯째_자리에서_반올림한다() -> None:
    k, v = sx.classify_pair(_e())
    assert k == "event" and v == {"g0": -0.05, "d": round(97 / 95 - 1, 6), "limit_down": False}


@pytest.mark.parametrize(("바꿈", "갈래"), [
    ({"reason": "추적손절"}, "other_reason"), ({"reason": "손절,기간초과"}, "overlap_reason"),
    ({"fill_date": None}, "unfilled"), ({"order_type": "limit"}, "not_market"),
    ({"ref_date": "2026-11-30"}, "date_mismatch"), ({"next_session": "2026-12-03"}, "date_mismatch"),
    ({"deferrals": 1}, "deferred"), ({"fill_basis": "close"}, "close_basis"),
    ({"close_note": "시가 없음 — 종가"}, "no_open"), ({"bar_open": None, "price": 97.0}, "no_open"),
    ({"day_close": None, "bar_open": 95.0}, "missing"), ({"div_ex": True}, "dividend_ex"),
    # 권리락: 종가 70 인데 등락률 −2% 면 기준가가 71.4 — 결정 종가 100 과 2% 넘게 어긋난다
    ({"day_close": 70.0, "bar_close": 70.0, "bar_change_pct": -2.0}, "corp_action"),
    ({"decided_on": "2026-11-23", "ref_date": "2026-11-23", "fill_date": "2026-11-24",
      "next_session": "2026-11-24"}, "seen_period"),
])  # fmt: skip
def test_제외는_수만_세고_값을_내지_않는다(바꿈: dict[str, Any], 갈래: str) -> None:
    assert sx.classify_pair(_e(**바꿈)) == (갈래, None)


def test_종가_하한가면_다음_시세의_시가로_잇고_없으면_대기다() -> None:
    하한 = {"day_close": 70.0, "bar_close": 70.0, "bar_change_pct": -30.0, "price": 75.0}
    assert sx.closed_limit_down(70.0, -30.0) and not sx.closed_limit_down(97.0, -3.0)
    k, v = sx.classify_pair(_e(**하한, next_open=66.0))
    assert k == "event" and v is not None and v["limit_down"] and v["d"] == round(66 / 75 - 1, 6)
    assert sx.classify_pair(_e(**하한, has_next=False)) == ("wait", None)
    # 다음 날 시가가 비면 그 날 종가
    assert sx.classify_pair(_e(**하한, next_open=None, next_close=68.0))[1]["d"] == round(68 / 75 - 1, 6)  # type: ignore[index]


def _짝_표본(날수: int, 종목: int, d_close: float = 97.0) -> list[dict[str, Any]]:
    return [_e(i * 7, stock_id=s, day_close=d_close, bar_close=d_close, bar_change_pct=d_close - 100.0)
            for i in range(날수) for s in range(1, 종목 + 1)]  # fmt: skip


def test_문이_차기_전에는_수만_내고_값을_내지_않는다() -> None:
    r = sx.judge_pair(_짝_표본(29, 4), "2027-12-01")  # 사건 116·체결일 29(< 30)
    assert r["status"] == "wait" and not r["gate"] and r["counts"]["gap_events"] == 116 and r["counts"]["gap_days"] == 29
    assert set(r) == {"name", "counts", "gate", "status"}
    글 = "\n".join(sx.lines(r))
    assert "수:" in 글 and "값" not in 글 and "0.02" not in 글
    # 기한이 지나도록 못 넘기면 판정 불가
    assert sx.judge_pair(_짝_표본(29, 4), "2028-05-01")["status"] == "expired"


def test_문을_넘기면_한_번_판정하고_적힌_뒤에는_다시_내지_않는다() -> None:
    표본 = _짝_표본(30, 4)  # 사건 120 · 체결일 30 · 묶음 8 — 모두 d = +2.1%
    r = sx.judge_pair(표본, "2027-12-01")
    assert r["status"] == "judge" and r["verdict"] == "pass" and r["bound"]["lower"] > 0
    assert r["first_half"] == pytest.approx(97 / 95 - 1, abs=1e-6) and r["tail"]["p_neg"] == 0
    assert "판정(한 번" in "\n".join(sx.lines(r))
    다시 = sx.judge_pair(표본, "2027-12-02", judged={"stop_exec_pair": "2027-12-01"})
    assert 다시["status"] == "judged_before" and "bound" not in 다시 and "tail" not in 다시
    # 종가가 시가보다 낮으면 접음
    assert sx.judge_pair(_짝_표본(30, 4, d_close=93.0), "2027-12-01")["verdict"] == "fold"


def test_갭_하락이_아닌_사건은_문과_결정_단위에_들지_않는다() -> None:
    오름 = [_e(i * 7, stock_id=s, price=101.0, bar_open=101.0) for i in range(40) for s in (1, 2, 3)]
    r = sx.judge_pair(오름, "2027-12-01")
    assert r["counts"]["events"] == 120 and r["counts"]["gap_events"] == 0 and r["status"] == "wait"
    assert sx.classify_pair(_e(price=100.0, bar_open=100.0))[1]["g0"] == 0  # type: ignore[index]  # 보합은 갭 하락이 아니다
    보합 = sx.judge_pair([_e(price=100.0, bar_open=100.0)], "2026-12-10")["counts"]
    assert (보합["events"], 보합["gap_events"], 보합["gap_days"]) == (1, 0, 0)


def test_제외를_먼저_걸고_그_뒤_계좌_사이_중복을_없앤다() -> None:
    # 같은 (종목·체결일)을 A(미룸 — 제외)와 B 가 팔았다 — A 가 앞선 계좌라도 사건은 B 것으로 한 건 남는다
    r = sx.judge_pair([_e(account_id="A", deferrals=2), _e(account_id="B"), _e(account_id="C")], "2026-12-10")
    assert r["counts"]["events"] == 1 and r["counts"]["by"] == {"deferred": 1, "event": 2}
    # 계좌 집합 밖(D·S)과 미국은 판정 표본이 아니다 — 미국은 수만
    r = sx.judge_pair([_e(account_id="D"), _e(account_id="S"), _e(market="US")], "2026-12-10")
    assert r["counts"]["events"] == 0 and r["counts"]["us_rows"] == 1


# ── stop_cluster_defer ──────────────────────────────────────────────────────────


def test_델타5_는_닷새_뒤_종가_대_체결가다() -> None:
    k, v = sx.classify_cluster(_e())
    assert k == "event" and v == {"delta5": round(1.02 / 0.95 - 1, 6), "e5": 1.0}
    assert sx.classify_cluster(_e(ret_5d=None)) == ("wait", None)  # 아직 안 찼다 — 제외가 아니다
    assert sx.classify_cluster(_e(ret_5d=None, outcome_note="시세 끊김")) == ("no_outcome", None)
    assert sx.classify_cluster(_e(lock_window=True)) == ("lock_window", None)
    assert sx.classify_cluster(_e(reason="손절,기간초과")) == ("other_reason", None)


def test_군집일은_겹친_사유까지_세고_제외를_걸기_전에_센다() -> None:
    행 = [_e(stock_id=1), _e(stock_id=2, reason="손절,기간초과"), _e(stock_id=3, deferrals=1), _e(stock_id=4, reason="추적손절"),
         _e(7, stock_id=1), _e(7, stock_id=1, account_id="C"), _e(stock_id=9, market="US"), _e(stock_id=8, account_id="D")]  # fmt: skip
    assert sx.cluster_days(행) == {D0.isoformat(): 3, (D0 + timedelta(days=7)).isoformat(): 1}


#: D0 뒤 첫 28일 묶음 경계까지의 날 수 — 군집일과 홑날(7일 뒤)이 같은 묶음에 들게
묶음_첫날 = (-D0.toordinal()) % pp.EVIDENCE_BLOCK_DAYS


def _군집_표본(묶음수: int, 군집_r5: float, 홑날_r5: float) -> list[dict[str, Any]]:
    """묶음(28일)마다 군집일 하루(종목 둘)와 홑날 하루(종목 하나)"""
    out = []
    for b in range(묶음수):
        i = 묶음_첫날 + b * 28
        out += [_e(i, stock_id=1, ret_5d=군집_r5), _e(i, stock_id=2, ret_5d=군집_r5), _e(i + 7, stock_id=3, ret_5d=홑날_r5)]
    return out


def test_닫힌_묶음만_세고_두_무리_묶음_열둘에서_한_번_본다() -> None:
    표본 = _군집_표본(12, 8.0, 2.0)
    끝 = D0 + timedelta(days=묶음_첫날 + 12 * 28 - 1)  # 열두째 묶음의 마지막 달력일
    이르다 = sx.judge_cluster(표본, (끝 + timedelta(days=sx.MATURE_DAYS)).isoformat())
    assert 이르다["status"] == "wait" and 이르다["counts"]["both_blocks"] < 12 and set(이르다) == {"name", "counts", "gate", "status"}
    r = sx.judge_cluster(표본, (끝 + timedelta(days=sx.MATURE_DAYS + 1)).isoformat())
    assert r["counts"]["both_blocks"] == 12 and r["status"] == "judge"
    # 묶음마다 차이가 똑같아 표준오차 0 — 하한 = 평균 > 0, 지수 초과도 군집 +7·홑날 +1
    assert r["verdict"] == "pass" and r["diff"]["mean"] == pytest.approx(1.08 / 0.95 - 1.02 / 0.95, abs=1e-5)
    assert not r["veto"]["vetoed"] and r["veto"]["account_days"] == 12
    # 덜 찬 묶음(5거래일 성과 대기)은 닫히지 않는다
    덜 = [*표본[:-1], {**표본[-1], "ret_5d": None}]
    assert sx.judge_cluster(덜, "2028-06-01")["counts"]["closed_blocks"] == 11
    # 적힌 뒤에는 값을 내지 않는다
    assert "diff" not in sx.judge_cluster(표본, "2028-06-01", judged={"stop_cluster_defer": "2028-01-01"})


def test_군집_손절분이_더_빠지면_닫고_거부권은_제외한_사건도_센다() -> None:
    닫음 = sx.judge_cluster(_군집_표본(12, -10.0, 2.0), "2028-06-01")
    assert 닫음["verdict"] == "close" and 닫음["cluster"]["upper"] < 0
    # 군집일 한 계좌의 합: 종목 둘 +13.7% × 2, 넷 −4.2% × 4 → 0.10 × (+10.5%) 는 한도 안.
    # 미룬(제외) 사건 −45%(D 종가 기준)가 더해지면 0.10 × (−34.5%) = −3.45% < −3% 로 넘는다
    표본 = _군집_표본(12, 8.0, 2.0)
    꼬리 = [_e(묶음_첫날, stock_id=s, ret_5d=-9.0) for s in (11, 12, 13, 14)]
    안 = sx.judge_cluster([*표본, *꼬리], "2028-06-01")["veto"]
    assert not 안["vetoed"]
    넘 = sx.judge_cluster([*표본, *꼬리, _e(묶음_첫날, stock_id=15, ret_5d=-45.0, deferrals=1)], "2028-06-01")
    assert 넘["veto"]["vetoed"] and 넘["veto"]["breaches"] == 1 and 넘["verdict"] != "pass"
    # 시세가 끊겨 5거래일 성과가 없는 제외 사건은 걸린 것으로 센다
    모름 = sx.judge_cluster([*표본, _e(묶음_첫날, stock_id=16, ret_5d=None, outcome_note="시세 끊김")], "2028-06-01")
    assert 모름["veto"]["unknown"] == 1 and 모름["veto"]["vetoed"]


def test_기한_뒤에는_묶음_여섯부터_판정하고_모자라면_판정_불가다() -> None:
    assert sx.judge_cluster(_군집_표본(6, 8.0, 2.0), "2028-11-30")["status"] == "wait"
    assert sx.judge_cluster(_군집_표본(6, 8.0, 2.0), "2028-12-01")["status"] == "judge"
    assert sx.judge_cluster(_군집_표본(5, 8.0, 2.0), "2028-12-01")["status"] == "expired"


def test_지수_초과가_서지_않으면_시장_반등_몫으로_넘긴다() -> None:
    표본 = [{**e, "bench_5d": e["ret_5d"]} for e in _군집_표본(12, 8.0, 2.0)]  # 초과 0 — ①②는 서고 ③은 안 선다
    assert sx.judge_cluster(표본, "2028-06-01")["verdict"] == "market_rebound"


# ── 읽기 (실제 스키마) ────────────────────────────────────────────────────────────


def _장부(mem: MemClient, account: str, sid: int, d: str, n: str, reason: str = "손절", 체결: bool = True) -> None:
    c = mem.conn
    c.execute("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
              " ret_5d, bench_5d, created_at) VALUES (?, 'KR', ?, ?, 'sell', ?, 100, ?, 2.0, 1.0, 't')", [account, d, sid, reason, d])  # fmt: skip
    cur = c.execute("INSERT INTO paper_orders (account_id, market, stock_id, side, order_type, quantity, currency, decided_on,"
                    " fill_session, tranche, reason, rule_version, status, created_at) VALUES (?, 'KR', ?, 'sell', 'market',"
                    " 10, 'KRW', ?, ?, 1, ?, 1, ?, 't')", [account, sid, d, n, reason, "filled" if 체결 else "open"])  # fmt: skip
    if 체결:
        c.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate,"
                  " fee, tax, slippage, cash_krw, horizon, day_close, created_at) VALUES (?, ?, ?, 'sell', ?, 95, 10, 'KRW',"
                  " 1, 0, 0, 0, 950, 'mid', 97, 't')", [cur.lastrowid, account, sid, n])  # fmt: skip


def test_읽기_질의가_실제_스키마에서_돌고_달력_일정_칸을_붙인다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
              " VALUES (1, '000001', 'KOSPI', 'KR', 'KRW', 't', 't'), (2, '000002', 'KOSPI', 'KR', 'KRW', 't', 't')")  # fmt: skip
    d, n = "2026-12-01", "2026-12-02"
    _장부(mem, "B", 1, d, n)
    _장부(mem, "A", 2, d, n, reason="손절,기간초과")
    _장부(mem, "S", 1, d, n, reason="스윙손절")  # 계좌 집합 밖
    _장부(mem, "C", 2, d, n, 체결=False)
    for sid in (1, 2):
        for day, o, cl in ((n, 95, 97), ("2026-12-03", 96, 98)):
            c.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, change_pct, currency, source,"
                      " fetched_at) VALUES (?, ?, ?, ?, ?, ?, 1, -3.0, 'KRW', 't', 't')", [sid, day, o, cl, o, cl])  # fmt: skip
    # 종목 2 는 12-04 가 배당 기준일 → 배당락일 12-03 (D+1~D+5 창 안)
    c.execute("INSERT INTO kr_corp_events (code, kind, record_date, source, fetched_at)"
              " VALUES ('000002', 'dividend', '2026-12-04', 't', 't')")  # fmt: skip
    events = job.load(mem)
    assert sorted((e["account_id"], e["stock_id"]) for e in events) == [("A", 2), ("B", 1), ("C", 2)]
    b = next(e for e in events if e["account_id"] == "B")
    assert (b["next_session"], b["bar_open"], b["bar_close"], b["has_next"], b["next_open"]) == (n, 95, 97, True, 96)
    assert not b["lock_window"] and not b["div_ex"]
    a = next(e for e in events if e["account_id"] == "A")
    assert a["lock_window"] and not a["div_ex"]  # 체결일(12-02)은 배당락일이 아니다
    줄 = "\n".join(job.run(mem, "2026-12-10"))
    assert "[stop_exec_pair] 상태 wait" in 줄 and "[stop_cluster_defer] 상태 wait" in 줄
    assert "events 1" in 줄 and "값:" not in 줄 and "000001" not in 줄  # 문 전에는 수만 — 종목 이름 없음
    # 군집일(종목 1·2 — 겹친 사유도 센다) 하루, 체결 없는 C 는 unfilled
    r = sx.judge_cluster(events, "2026-12-10")
    assert r["counts"]["cluster_days"] == 1 and r["counts"]["by"] == {"event": 1, "unfilled": 1}
    assert job.facts(events)["max_accounts_per_day"] == 2
    assert job.kospi_on(mem, [n]) is None  # 지수 행이 없으면 모름
