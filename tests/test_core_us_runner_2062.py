"""깔때기 ⑧-5 — 리플레이 실행기(`core_backtest.run_core`)의 미국 실행 (25.2062, docs/funnel.md 4장 7).

메모리 DB(마이그레이션 전부)에 미국 12종목(NASDAQ·NYSE, 달러, 수정종가 = 가격 수준 × 0.9 — 배당 재투자 계열을 흉내)과
SP500 을 넣고 `run_core("US")` 를 끝까지 — 핵심 다섯 행이 시장 US 로 실리고, 배당 포함 경고(`WARN_TOTAL_RETURN`)가 붙고,
분할 재수집 대기 종목은 유니버스에서 빠지며(경고 + 시점 유니버스 캐시에 없음), 시점 유니버스는 가격 수준으로 시총을 잰다.
탐색 + S·T 도 미국으로 돈다.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from batch.core import db
from batch.jobs import backtest as jb
from batch.jobs import core_backtest as cj
from batch.services import backtest as bt
from batch.services import core_strategy as cs
from tests.test_core_backtest_job_1534 import N, _영업일, 오늘
from tests.test_portfolio_job import MemClient

#: 분할 재수집 대기(대기열 `ratio_before = ratio_after` — 분할 꼴) 종목
보류 = 5
#: 배당만 어긋난 대기열 종목 — 빼지 않는다
배당꼴 = 7


def _db(monkeypatch: pytest.MonkeyPatch) -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(cj, "TursoClient", lambda: mem)
    c = mem.conn
    날 = _영업일("2022-09-01", 455)
    rng = random.Random(29)
    for i in range(1, N + 1):
        # 미국 listed_date 는 관측 첫 거래일(us_shares) — 이력 시작
        c.execute("INSERT INTO stocks (id, ticker, name_ko, market, country, currency, status, sector, listed_shares,"
                  " listed_date, source, fetched_at) VALUES (?, ?, ?, ?, 'US', 'USD', 'active', ?, 50000000, ?, 't', 't')",
                  [i, f"T{i:03d}", f"종목{i}", "NASDAQ" if i % 3 else "NYSE", ["반도체", "은행", "화학"][i % 3], 날[0]])  # fmt: skip
        c.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                  " VALUES ('2025-03-07', ?, 1, 'USD', 't')", [i])  # fmt: skip
        p, drift, vol = 100.0, rng.uniform(-0.0005, 0.002), rng.uniform(0.01, 0.03)
        for k, d in enumerate(날):
            p *= math.exp(drift + vol * rng.gauss(0, 1))
            대금 = 5e7 * rng.uniform(0.8, 1.2) * (1.7 if (k + 7 * i) // 30 % 2 == 0 else 1.0)
            c.execute("INSERT INTO prices (stock_id, date, open, close, adj_close, volume, value, currency, source,"
                      " fetched_at) VALUES (?, ?, ?, ?, ?, 1000, ?, 'USD', 't', 't')",
                      [i, d, round(p, 2), round(p, 2), round(p * 0.9, 4), 대금])  # fmt: skip
        rev, g = rng.uniform(1e9, 1e10), rng.uniform(-0.1, 0.4)
        for fy in range(2019, 2024):
            rev *= 1 + g
            oi = rev * 0.1
            값 = {"revenue": rev, "operating_income": oi, "net_income": oi * 0.7, "total_equity": rev * rng.uniform(0.3, 1.5),
                 "total_assets": rev * 2, "total_liabilities": rev * 0.8, "currency": "USD"}  # fmt: skip
            c.execute("INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
                      " consolidated, payload, source, fetched_at) VALUES (?, ?, ?, ?, ?, 1, ?, 't', 't')",
                      [i, f"{fy + 1}-03-15", f"{fy}{i:04d}", fy, jb.ANNUAL_REPORT_CODE, json.dumps(값)])  # fmt: skip
    a = 4000.0
    for d in _영업일("2021-09-01", 700):
        a *= math.exp(0.0003 + 0.01 * rng.gauss(0, 1))
        c.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('SP500', ?, ?, 't', 't')",
                  [d, a])  # fmt: skip
    c.execute("INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
              " VALUES (?, '2025-03-01T00:00:00+00:00', '2025-02-28', 0.5, 0.5)", [보류])  # fmt: skip
    # 반대편 대조(25.2066, 교차검증 note) — 배당만 어긋난 꼴(비가 다름)은 분할이 아니라 빼지 않는다
    c.execute("INSERT INTO adjust_refresh_queue (stock_id, detected_at, sample_date, ratio_before, ratio_after)"
              " VALUES (?, '2025-03-01T00:00:00+00:00', '2025-02-28', 0.98, 0.97)", [배당꼴])  # fmt: skip
    return mem


@pytest.fixture(scope="module")
def 실행() -> tuple[MemClient, int, dict]:
    mp = pytest.MonkeyPatch()
    엿본: dict = {}
    try:
        mem = _db(mp)
        원 = cj.pit_eligible

        def pit(*a, **k):  # type: ignore[no-untyped-def]
            엿본.update(k)
            out = 원(*a, **k)
            엿본["cache"] = out[1]
            return out

        mp.setattr(cj, "pit_eligible", pit)
        rc = cj.run_core("US", years=1, today=오늘, force=True)
        yield mem, rc, 엿본
    finally:
        mp.undo()


def test_미국_실행이_다섯_행을_시장_US_로_싣는다(실행: tuple[MemClient, int, dict]) -> None:
    mem, rc, 엿본 = 실행
    assert rc == 0
    행들 = mem.execute("SELECT * FROM backtest_runs ORDER BY id").dicts()
    assert [r["strategy"] for r in 행들] == list(cs.VARIANTS) and {r["market"] for r in 행들} == {"US"}
    for r in 행들:
        경고 = json.loads(r["warnings_json"])
        assert bt.WARN_TOTAL_RETURN in 경고 and bt.WARN_NO_DIVIDENDS not in 경고
        assert any(w.startswith(cj.WARN_SPLIT_PENDING) and w.endswith("1종목") for w in 경고), 경고
        # 운영과 일치 시험이 없는 변형만 단서(25.2063) — B·G 는 25.2059 가 묶었다
        assert (cj.WARN_US_UNVERIFIED in 경고) == (r["strategy"] not in cj.US_PARITY_VERIFIED), r["strategy"]
    # 모든 변형이 산다(달러 장부 — 환율 거름이 다시 걸리면 투자 비중이 0). R1·R2 는 무작위 대체의 기업행위 읽기를 지난다
    # (25.2062 — 예전엔 미국 분할 감지 설정 읽기가 창에 없어 실행이 멈췄다)
    assert all(json.loads(r["metrics_json"])["invested_share"] > 0 and r["turnover_avg"] > 0 for r in 행들)
    # 시점 유니버스는 미국 갈래(가격 수준)로 불렸고, 분할 대기 종목은 들지 않는다
    assert 엿본["country"] == "US" and 엿본["levels"] and all(
        set(x) for x in 엿본["levels"].values())  # fmt: skip
    assert 엿본["cache"] and all(보류 not in ids for ids in 엿본["cache"].values())
    assert any(배당꼴 in ids for ids in 엿본["cache"].values())


def test_미국_일치_시험_목록_못박음() -> None:
    """단서를 떼는 변형 목록은 미국 일치 시험이 실제로 있는 것만(25.2063) — B·G 는 tests/test_replay_parity_us_2059.py,
    S·T 는 tests/test_replay_parity_st_us_2061.py, H·D·E 는 tests/test_replay_parity_hde_us_2064.py, A·C 는
    tests/test_replay_parity_ac_us_2065.py. 넓히려면 그 변형의 미국 일치 시험을 먼저 더하고 여기를 고친다"""
    assert {"core_b", "core_g", "core_h", "core_d", "core_e", "core_a", "core_c", "st_s", "st_t"} == cj.US_PARITY_VERIFIED


def test_미국_탐색_S_T_도_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _db(monkeypatch)
    assert cj.run_core("US", years=1, today=오늘, force=True, explore=True, st=True) == 0
    행 = {r["strategy"]: r for r in mem.execute("SELECT strategy, market FROM backtest_runs").dicts()}
    assert set(cj.ST_VARIANTS) <= set(행) and {r["market"] for r in 행.values()} == {"US"}
    경고 = {r["strategy"]: json.loads(r["warnings_json"]) for r in mem.execute(
        "SELECT strategy, warnings_json FROM backtest_runs").dicts()}  # fmt: skip
    assert all(cj.WARN_US_UNVERIFIED not in 경고[v] for v in cj.ST_VARIANTS)  # S·T 는 25.2061 이 묶었다
    # 미국 S·T 도 배당 포함 총수익(25.2066) — 같은 묶음의 탐색 변형과 같은 경고
    assert all(bt.WARN_TOTAL_RETURN in w and bt.WARN_NO_DIVIDENDS not in w for w in 경고.values()), 경고
    # 탐색 D·E·A·C 는 25.2064·2065 가 묶었다 — 행마다 단서 유무가 목록과 같다(탐색 실행엔 단서 줄이 없다)
    assert all((cj.WARN_US_UNVERIFIED in w) == (v not in cj.US_PARITY_VERIFIED) for v, w in 경고.items()), 경고.keys()
