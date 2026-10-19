"""핵심 H 백테스트 조각 1d — 하루 재료 나눠 읽기·리스크 지표 주기·실행 연결·저장·워크플로
(docs/backtest.md 10장, docs/infra.md 25.1534). 오프라인, 작은 합성 자료."""

from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import asdict
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import pytest
import yaml

from batch.core import db
from batch.jobs import backtest as jb
from batch.jobs import core_backtest as cj
from batch.services import backtest as bt
from batch.services import core_backtest as cb
from batch.services import core_strategy as cs
from batch.services import metrics as m
from tests.test_portfolio_job import MemClient

뿌리 = Path(__file__).resolve().parent.parent


def _영업일(시작: str, n: int) -> list[str]:
    d, out = date.fromisoformat(시작), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# 워밍업 W(리스크 1년 창 200행이 첫 판단부터 차게) + 판단 S 거래일, 12종목
W, S, N = 230, 90, 12
날들 = _영업일("2022-01-03", W + S)
모의 = 날들[W:]


@cache
def _자료() -> tuple:
    rng = random.Random(15)
    universe, prices, turn, snaps = [], {}, {}, {}
    for i in range(1, N + 1):
        universe.append({"stock_id": i, "ticker": f"{i:06d}", "name": f"종목{i}", "market": "KOSPI" if i % 3 else "KOSDAQ",
                         "sector": ["반도체", "은행", "화학", "유통"][i % 4], "currency": "KRW", "listed_shares": 10_000_000})  # fmt: skip
        p, drift, vol = 10_000.0, rng.uniform(-0.0005, 0.0015), rng.uniform(0.01, 0.03)
        prices[i], turn[i] = {}, {}
        for k, d in enumerate(날들):
            # 변동성이 달마다 바뀌어 리스크 지표가 주마다 움직이게(월 주기의 양성 대조가 헛돌지 않게)
            p *= math.exp(drift + vol * (1 + (k // 21) % 3) * rng.gauss(0, 1))
            prices[i][d] = round(p, 2)
            turn[i][d] = 1e9 * rng.uniform(0.8, 1.2) * (1.7 if (k + 7 * i) // 30 % 2 == 0 else 1.0)
        rev, g, 마진 = rng.uniform(1e11, 1e12), rng.uniform(-0.1, 0.4), rng.uniform(0.05, 0.15)
        snaps[i] = []
        for fy in range(2017, 2023):
            rev *= 1 + g + rng.uniform(-0.05, 0.05)
            oi = rev * 마진
            snaps[i].append({"as_of_date": f"{fy + 1}-03-15", "fiscal_year": fy, "receipt_no": f"{fy}{i:04d}",
                             "consolidated": True, "values": {
                                 "revenue": rev, "operating_income": oi, "net_income": oi * 0.7,
                                 "total_equity": rev * rng.uniform(0.3, 1.5), "total_assets": rev * 2,
                                 "total_liabilities": rev * 0.8, "currency": "KRW"}})  # fmt: skip
    idx: dict[str, dict[str, float]] = {"KOSPI": {}, "KOSDAQ": {}}
    a, b = 2500.0, 800.0
    for d in 날들:
        a *= math.exp(0.0003 + 0.01 * rng.gauss(0, 1))
        b *= math.exp(0.0002 + 0.012 * rng.gauss(0, 1))
        idx["KOSPI"][d], idx["KOSDAQ"][d] = a, b
    # 6 은 모의 40~51 거래정지 — 그동안 종목 마지막 시세 < cutoff 라 리스크 창 끝(`broad_last`, 25.723)이 갈린다
    for d in 모의[40:52]:
        del prices[6][d], turn[6][d]
    return universe, prices, turn, snaps, idx


def _시장(**kw) -> cb.CoreMarket:
    universe, _p, _t, snaps, idx = _자료()
    return cb.CoreMarket(universe, snaps, idx, **kw)


def _돌림(variant: str, market) -> tuple[cs.CoreStrategy, bt.BacktestResult]:
    _u, prices, turn, _s, _i = _자료()
    s = cs.make_core_strategy(variant, market, sessions=날들, costs=bt.Costs())
    return s, bt.simulate(prices, 모의, 모의, s, bt.Costs(), turnover=turn, has_delisted=True)


def _주문(s: cs.CoreStrategy) -> list[tuple]:
    return [(lg.day, lg.cutoff, sorted(lg.sells.items()), sorted(lg.buys.items())) for lg in s.log]


@cache
def _따로() -> dict[str, tuple[cs.CoreStrategy, bt.BacktestResult]]:
    """변형마다 제 `CoreMarket` — 조각 1c 의 길(대조 기준)."""
    return {v: _돌림(v, _시장()) for v in cs.VARIANTS}


@cache
def _나눠() -> tuple[cb.SharedDays, dict[str, tuple[cs.CoreStrategy, bt.BacktestResult]]]:
    shared = cb.SharedDays(_시장())
    return shared, {v: _돌림(v, shared.reader()) for v in cs.VARIANTS}


def _보기(cutoff: str) -> bt.PriceView:
    _u, prices, turn, _s, _i = _자료()
    return bt.PriceView(prices, cutoff, turn)


# ── 하루 재료를 나눠 읽기 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("variant", sorted(cs.VARIANTS))
def test_나눠_읽어도_따로_돌린_것과_같다(variant: str) -> None:
    """DayView 는 (cutoff, 결정일)과 H 원장에만 달린다 — 나눠 읽은 변형의 주문·곡선이 제 판단기로 돈 것과 같아야 한다."""
    s0, r0 = _따로()[variant]
    _shared, 나눔 = _나눠()
    s1, r1 = 나눔[variant]
    assert _주문(s1) == _주문(s0)
    assert r1.curve == r0.curve
    assert [(x.date, x.weights) for x in r1.rebalances] == [(x.date, x.weights) for x in r0.rebalances]
    # 헛돌지 않게 — 사고판다
    assert sum(len(lg.buys) for lg in s1.log) > 0


def test_나눠_읽기는_하루를_한_번만_낸다() -> None:
    shared, 나눔 = _나눠()
    날수 = len(나눔["core_h"][0].log)
    assert 날수 == len(모의) - 1  # 마지막 날은 평가만
    assert shared.computed == 날수 and shared.replayed == 날수 * (len(cs.VARIANTS) - 1)
    # 변형마다 다른 매매를 했는데도(대조가 헛돌지 않게) 같은 재료를 읽었다
    assert len({repr(_주문(s)) for s, _r in 나눔.values()}) >= 3


def test_한_변형이_고쳐도_다른_변형은_못_본다() -> None:
    """기록은 읽기 전용이다 — 받은 행·표·고름을 고쳐도 다음 읽개는 처음 적힌 그대로 받는다."""
    shared = cb.SharedDays(_시장())
    가, 나 = shared.reader(), shared.reader()
    고친날 = 0
    for k in range(1, 30):
        view, day = _보기(모의[k - 1]), 모의[k]
        dv = 가.at(view, day)
        원본 = repr(copy.deepcopy(dv))
        if dv.rows:
            dv.rows[0].tranche_plan.append({"step": 99})
            dv.rows.clear()
            dv.pick_rows.clear()
            dv.votes.clear()
            dv.holding.clear()
            고친날 += 1
        assert repr(나.at(view, day)) == 원본
    assert 고친날 >= 5, "신호가 난 날이 적어 대조가 헛돈다"


def test_기록은_유니버스_크기_칸을_비우고_나머지는_그대로다() -> None:
    따로 = _시장()
    읽개 = cb.SharedDays(_시장()).reader()
    for k in range(1, 6):
        view, day = _보기(모의[k - 1]), 모의[k]
        원, 받은 = 따로.at(view, day), 읽개.at(view, day)
        for 칸 in cb.SHARED_DROPPED:
            assert not getattr(받은, 칸) and getattr(원, 칸), 칸
        for 칸 in ("rows", "pick_rows", "picks", "b_top", "votes", "company_weights", "ranked", "holding"):
            assert repr(getattr(받은, 칸)) == repr(getattr(원, 칸)), 칸


def test_결정일이_다르거나_거꾸로_가면_멈춘다() -> None:
    shared = cb.SharedDays(_시장())
    가, 나 = shared.reader(), shared.reader()
    가.at(_보기(모의[0]), 모의[1])
    with pytest.raises(ValueError):
        나.at(_보기(모의[0]), 모의[2])  # 적힌 cutoff 를 다른 결정일로
    가.at(_보기(모의[1]), 모의[2])
    with pytest.raises(ValueError):
        가.at(_보기(모의[0]), 모의[1])  # 읽개가 거꾸로
    가.at(_보기(모의[3]), 모의[4])
    셋 = shared.reader()
    셋.at(_보기(모의[0]), 모의[1])  # 적힌 날은 다시 받는다
    with pytest.raises(ValueError):
        셋.at(_보기(모의[2]), 모의[3])  # 적히지 않은 앞 cutoff — 판단기가 거꾸로 감을 막는다


# ── 리스크 지표 주기 ─────────────────────────────────────────────────────────


def _하루들(market: cb.CoreMarket, n: int = S - 1) -> list[cb.DayView]:
    return [market.at(_보기(모의[k - 1]), 모의[k]) for k in range(1, n + 1)]


@cache
def _주기별() -> dict[str | None, tuple[list[cb.DayView], cb.CoreMarket]]:
    out = {}
    for 주기 in (None, "week", "month"):
        jb._위험_캐시.clear()
        시장 = _시장(risk_every=주기)
        out[주기] = (_하루들(시장), 시장)
    return out


def test_리스크_주기_week_는_예전_길과_같다() -> None:
    """판단기 안에서 주마다 잰 값 = 예전 길(`build_pit_inputs` 의 `_위험_캐시`) — 점수·신호·고름이 날마다 같다."""
    옛, _ = _주기별()[None]
    주, 시장 = _주기별()["week"]
    for a, b in zip(옛, 주, strict=True):
        assert repr(a) == repr(b), a.cutoff
    # 헛돌지 않게 — 리스크 점수가 실제로 있고, 주마다 다시 쟀다
    assert any(fs.get("risk") is not None for d in 주 for fs in d.factor_scores.values())
    assert 시장.risk_computed >= N * len({d.score_cutoff for d in 주})


def test_리스크_주기_month_는_달_첫_점수에만_같고_달마다_한_번_잰다() -> None:
    주, _ = _주기별()["week"]
    달, 시장 = _주기별()["month"]
    점수날 = sorted({d.score_cutoff for d in 주})
    달첫 = {c for i, c in enumerate(점수날) if i == 0 or 점수날[i - 1][:7] != c[:7]}
    assert len(달첫) >= 3 and len(점수날) > len(달첫)
    다름 = 0
    for a, b in zip(주, 달, strict=True):
        if a.score_cutoff in 달첫:
            assert a.factor_scores == b.factor_scores, a.cutoff  # 그 달 첫 점수는 새로 잰 값
        elif a.factor_scores != b.factor_scores:
            다름 += 1
    assert 다름 > 0, "달 중간 주의 리스크가 묵어도 점수가 같다 — 대조가 헛돈다"
    assert 시장.risk_computed <= N * len({c[:7] for c in 점수날})


def test_리스크_주기는_둘_가운데_하나() -> None:
    with pytest.raises(ValueError):
        _시장(risk_every="day")


# ── 반 나눈 기간 ─────────────────────────────────────────────────────────────


def test_반_나눈_기간_손계산() -> None:
    곡선 = [("2020-01-01", 1.0), ("2021-01-01", 1.1), ("2022-01-01", 1.21), ("2023-01-01", 1.0), ("2024-01-01", 1.21)]
    반 = cj.split_half(곡선)
    assert (반["first"]["start"], 반["first"]["end"], 반["first"]["days"]) == ("2020-01-01", "2022-01-01", 3)
    assert (반["second"]["start"], 반["second"]["end"], 반["second"]["days"]) == ("2022-01-01", "2024-01-01", 3)
    # 앞 반 1 → 1.21 두 해(731일): 약 10%, 낙폭 없음. 뒤 반 1.21 → 1.0 → 1.21: 낙폭 1/1.21 − 1, 수익 0
    해 = m.years_between(date(2020, 1, 1), date(2022, 1, 1))
    assert 반["first"]["cagr"] == pytest.approx(1.21 ** (1 / 해) - 1) and 반["first"]["mdd"] == 0
    assert 반["second"]["cagr"] == pytest.approx(0.0) and 반["second"]["mdd"] == pytest.approx(1 / 1.21 - 1)
    assert cj.split_half(곡선[:2]) == {}


# ── 실행 연결·저장 ───────────────────────────────────────────────────────────

#: 실행 시험의 오늘 — 1년 백테스트 시작이 2024-03-01(`start_of_backtest`)
오늘 = date(2025, 3, 10)


def _db(monkeypatch: pytest.MonkeyPatch) -> MemClient:
    """마이그레이션으로 만든 메모리 DB 에 12종목 — 워밍업 약 390 거래일(모멘텀 12-1·200일선이 찬다) + 백테스트 약 65 거래일."""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(cj, "TursoClient", lambda: mem)
    c = mem.conn
    날 = _영업일("2022-09-01", 455)
    assert 날[0] < "2024-03-01" < 날[-1]
    rng = random.Random(23)
    for i in range(1, N + 1):
        c.execute("INSERT INTO stocks (id, ticker, name_ko, market, country, currency, status, sector, listed_shares,"
                  " listed_date, source, fetched_at) VALUES (?, ?, ?, ?, 'KR', 'KRW', 'active', ?, 50000000,"
                  " '2010-01-04', 't', 't')",
                  [i, f"{i:06d}", f"종목{i}", "KOSPI" if i % 3 else "KOSDAQ", ["반도체", "은행", "화학"][i % 3]])  # fmt: skip
        c.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                  " VALUES ('2025-03-07', ?, 1, 'KRW', 't')", [i])  # fmt: skip
        p, drift, vol = 10_000.0, rng.uniform(-0.0005, 0.002), rng.uniform(0.01, 0.03)
        for k, d in enumerate(날):
            p *= math.exp(drift + vol * rng.gauss(0, 1))
            대금 = 1e9 * rng.uniform(0.8, 1.2) * (1.7 if (k + 7 * i) // 30 % 2 == 0 else 1.0)  # 거래대금 늘어나는 구간
            c.execute("INSERT INTO prices (stock_id, date, close, adj_close, volume, value, currency, source, fetched_at)"
                      " VALUES (?, ?, ?, ?, 1000, ?, 'KRW', 't', 't')", [i, d, round(p, 2), round(p, 2), 대금])  # fmt: skip
        rev, g = rng.uniform(1e11, 1e12), rng.uniform(-0.1, 0.4)
        for fy in range(2019, 2024):
            rev *= 1 + g
            oi = rev * 0.1
            값 = {"revenue": rev, "operating_income": oi, "net_income": oi * 0.7, "total_equity": rev * rng.uniform(0.3, 1.5),
                 "total_assets": rev * 2, "total_liabilities": rev * 0.8, "currency": "KRW"}  # fmt: skip
            c.execute("INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
                      " consolidated, payload, source, fetched_at) VALUES (?, ?, ?, ?, ?, 1, ?, 't', 't')",
                      [i, f"{fy + 1}-03-15", f"{fy}{i:04d}", fy, jb.ANNUAL_REPORT_CODE, json.dumps(값)])  # fmt: skip
    a, b = 2500.0, 800.0
    for d in _영업일("2021-09-01", 700):
        a *= math.exp(0.0003 + 0.01 * rng.gauss(0, 1))
        b *= math.exp(0.0002 + 0.012 * rng.gauss(0, 1))
        for code, v in (("KOSPI", a), ("KOSDAQ", b)):
            c.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES (?, ?, ?, 't', 't')",
                      [code, d, v])  # fmt: skip
    return mem


@pytest.fixture(scope="module")
def 실행() -> tuple[MemClient, int]:
    mp = pytest.MonkeyPatch()
    try:
        mem = _db(mp)
        jb._위험_캐시[("남은", "값")] = {}  # 앞 실행이 남긴 캐시 — 실행이 비운다
        rc = cj.run_core("KR", years=1, today=오늘, force=True)
        assert not jb._위험_캐시
        yield mem, rc
    finally:
        mp.undo()


def _행들(mem: MemClient) -> list[dict]:
    return mem.execute("SELECT * FROM backtest_runs ORDER BY id").dicts()


def test_run_core_다섯_행을_한_묶음_날마다로_싣는다(실행: tuple[MemClient, int]) -> None:
    mem, rc = 실행
    assert rc == 0
    행들 = _행들(mem)
    assert [r["strategy"] for r in 행들] == list(cs.VARIANTS)
    assert {r["group_id"] for r in 행들} == {행들[0]["group_id"]}
    assert {r["rebalance"] for r in 행들} == {cj.REBALANCE} == {"daily"}
    assert {r["top_n"] for r in 행들} == {5}
    assert {r["market"] for r in 행들} == {"KR"}
    for r in 행들:
        곡선 = mem.execute("SELECT COUNT(*) FROM backtest_curves WHERE run_id = ?", [r["id"]]).scalar()
        assert 곡선 >= 40 and r["rebalances"] == 곡선 - 1  # 날마다(마지막 날은 평가만)
        assert r["start_date"] >= "2024-03-01"
    # 헛돌지 않게 — 어느 변형이든 사고판다
    assert any((r["turnover_avg"] or 0) > 0 for r in 행들)


def test_run_core_경고(실행: tuple[MemClient, int]) -> None:
    mem, _ = 실행
    for r in _행들(mem):
        경고 = json.loads(r["warnings_json"])
        for 말 in (cj.WARN_CORE, bt.WARN_SURVIVORSHIP, bt.WARN_NO_DIVIDENDS, jb.WARN_PIT_UNIVERSE, jb.WARN_NO_RISK_FREE):
            assert 말 in 경고, 말
        assert any(jb.WARN_NOT_DEFAULT_PARAMS in w for w in 경고)  # 1년은 기본(5년)이 아니다
        assert cj.WARN_RISK_MONTHLY not in 경고  # 기본은 주마다


def test_run_core_반_나눈_기간과_실행_기록(실행: tuple[MemClient, int]) -> None:
    mem, _ = 실행
    행들 = _행들(mem)
    for r in 행들:
        mj = json.loads(r["metrics_json"])
        반 = mj["split_half"]
        assert 반["first"]["end"] == 반["second"]["start"]
        assert 반["first"]["start"] == r["start_date"] and 반["second"]["end"] == r["end_date"]
        for 쪽 in ("first", "second"):
            assert {"cagr", "mdd", "days", "start", "end"} <= set(반[쪽])
        assert mj["risk_every"] == "week" and mj["account"] == cs.VARIANTS[r["strategy"]].account
        assert "luck" not in mj  # DSR 은 조각 1e
    run = mem.execute("SELECT * FROM batch_runs WHERE job_name = ?", [cj.JOB_NAME]).dicts()
    assert len(run) == 1 and run[0]["status"] == "success" and run[0]["market"] == "KR"
    로그 = json.loads(run[0]["step_log"])
    assert 로그["group_id"] == 행들[0]["group_id"] and 로그["rebalance"] == "daily"
    assert set(로그["split_half"]) == set(cs.VARIANTS)
    t = 로그["timing"]
    assert t["days_computed"] == 행들[0]["rebalances"] and t["days_replayed"] == 4 * t["days_computed"]
    assert set(t["variants_s"]) == set(cs.VARIANTS)
    # 월 백테스트의 판정 칸(`backtest`)에는 아무것도 적지 않는다
    assert not mem.execute("SELECT COUNT(*) FROM batch_runs WHERE job_name = 'backtest'").scalar()


def test_run_core_가중_대_동일가중_진단을_싣는다(실행: tuple[MemClient, int]) -> None:
    """31회차 P1(25.1538) — 나눠 읽은 하루 재료로 끝난 뒤 센다. 날마다 하나, 진단이 깨지지 않는다"""
    mem, _ = 실행
    run = mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?", [cj.JOB_NAME]).dicts()
    eq = json.loads(run[0]["step_log"])["holding_eq"]
    assert "error" not in eq, eq
    t = json.loads(run[0]["step_log"])["timing"]
    assert eq["days"] == t["days_computed"]
    assert eq["differ_days"] >= eq["missing_days"] + eq["repeat_days"] + eq["n"] - 0
    assert eq["verdict"] in ("undecidable", "keep", "equal", "none")


def test_run_core_는_국내만() -> None:
    with pytest.raises(NotImplementedError):
        cj.run_core("US")


def test_run_core_month_는_경고를_단다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _db(monkeypatch)
    assert cj.run_core("KR", years=1, today=오늘, force=True, risk_every="month", variants=("core_b",)) == 0
    (r,) = _행들(mem)
    assert cj.WARN_RISK_MONTHLY in json.loads(r["warnings_json"])
    assert json.loads(r["metrics_json"])["risk_every"] == "month"


# ── 예전 길은 그대로 ─────────────────────────────────────────────────────────


def test_store_run_기본은_예전_그대로() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    곡선 = [(d, 1 + i / 100) for i, d in enumerate(_영업일("2024-01-02", 30))]
    r = bt.BacktestResult(curve=곡선, rebalances=[bt.Rebalance(곡선[0][0], {1: 1.0}, 1.0, 0.0)], costs=bt.Costs())
    s = bt.summarize(r)
    jb.store_run(mem, "KR", "composite", r, s, 20, "g", [], "t")  # type: ignore[arg-type]
    (row,) = _행들(mem)
    assert row["rebalance"] == "monthly"
    assert set(json.loads(row["metrics_json"])) == set(asdict(s.metrics)) | set(jb.exposure_fields(r))


def test_월_백테스트는_리스크를_예전_캐시로_낸다() -> None:
    """`risk_for` 를 주지 않는 길(월 백테스트 `run`)은 예전처럼 `_위험_캐시` 를 쓰고 같은 값을 낸다."""
    universe, prices, turn, snaps, idx = _자료()
    view = bt.PriceView(prices, 모의[10], turn)
    jb._위험_캐시.clear()
    옛 = jb.build_pit_inputs(universe, snaps, view, {}, idx["KOSPI"])
    assert len(jb._위험_캐시) == N
    시장 = _시장(risk_every="week")
    새 = jb.build_pit_inputs(universe, snaps, view, {}, idx["KOSPI"], risk_for=시장._risk_fn(view, idx["KOSPI"]))
    assert repr(옛) == repr(새) and 시장.risk_computed == N
    jb._위험_캐시.clear()


# ── 워크플로 ────────────────────────────────────────────────────────────────


def test_워크플로_핵심H_는_수동_전용_국내_120분() -> None:
    wf = yaml.safe_load((뿌리 / ".github/workflows/backtest.yml").read_text(encoding="utf-8"))
    트리거 = wf.get("on") or wf.get(True)
    assert "핵심H" in 트리거["workflow_dispatch"]["inputs"]["what"]["options"]
    core, 월 = wf["jobs"]["core"], wf["jobs"]["run"]
    assert core["timeout-minutes"] == 120
    # 수동(workflow_dispatch)만 — 예약·웹 깨우기(repository_dispatch)로는 돌지 않는다
    assert "github.event_name == 'workflow_dispatch'" in core["if"] and "inputs.what == '핵심H'" in core["if"]
    assert "inputs.what != '핵심H'" in 월["if"]
    단계 = {s.get("name"): s for s in core["steps"]}
    실행줄 = 단계["핵심 H 백테스트"]["run"]
    assert "python -m batch.jobs.core_backtest --market KR" in 실행줄 and "$MARKET" not in 실행줄
    assert "steps.gate.outputs.deferred != 'true'" in 단계["핵심 H 백테스트"]["if"]
    assert "turso_read_gate.py backtest_core" in 단계["Turso 읽기 진도 문 (docs/infra.md 25.886)"]["run"]
    # 월 백테스트와 같은 비밀·환경·시세 사본 단계
    for 열쇠 in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "DB_BACKEND", "D1_API_TOKEN", "PRICE_REPLICA_PATH",
                "OFFLINE_MODE", "BACKTEST_TRIGGER"):  # fmt: skip
        assert core["env"][열쇠] == 월["env"][열쇠], 열쇠
    월단계 = [s.get("name") for s in 월["steps"]]
    for 이름 in ("DB 백엔드 고정 (이 워크플로의 모든 단계가 같은 DB 를 쓴다, docs/infra.md 25.472·25.480)",
                "시세 사본 꺼내기 (docs/infra.md 25.888)", "시세 사본 풀기", "시세 사본 맞추기", "시세 사본 암호화",
                "시세 사본 넣어 두기"):  # fmt: skip
        assert 이름 in 월단계 and 단계[이름] == next(s for s in 월["steps"] if s.get("name") == 이름), 이름
