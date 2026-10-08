"""참고 분석이 유니버스 재료를 다시 읽지 않는다 — 저장된 원값으로 같은 점수 (docs/infra.md 25.1027).

2026-10-08 사용자 "관심종목 참고 분석 DB 사용량 줄여줘". 요청 한 번에 약 149만 행을 읽던 것을 저장된 `factors.raw_json`
(그날 점수 작업이 쓴 것)으로 바꿨다. 바꿔도 **결과가 같아야** 한다 — 이 파일이 그것을 대 본다.
"""

from __future__ import annotations

import json
import random

import pytest

from batch.core import db
from batch.jobs import analyze_extra as job
from batch.services import scoring as sc
from tests.test_portfolio_job import MemClient


def _종목들(n: int, seed: int) -> list[sc.StockInput]:
    rnd = random.Random(seed)
    이름들 = sorted({m.name for f in sc.FACTORS for m in sc.FACTOR_METRICS[f]})
    out = []
    for i in range(n):
        metrics: dict = {k: (None if rnd.random() < 0.08 else rnd.uniform(-1, 3)) for k in 이름들}
        # 회복 기간 결측 + 깊이·하한 — 치환이 실제로 일어나게 (25.685·25.691)
        if rnd.random() < 0.3:
            metrics["mdd_recovery_days"] = None
            metrics["mdd_abs"] = rnd.uniform(0.05, 0.6)
            metrics[sc.UNRECOVERED_ROWS] = float(rnd.randint(10, 700))
        out.append(sc.StockInput(stock_id=i + 1, market="KOSPI", sector=("가" if i % 3 else "나"), metrics=metrics))
    return out


def _저장(results: list[sc.FactorResult], inputs: list[sc.StockInput]) -> list[dict]:
    """점수 작업이 `factors` 에 쓰고 다시 읽은 모양 — raw 는 JSON 을 한 번 거친다."""
    meta = {s.stock_id: s for s in inputs}
    return [{"stock_id": r.stock_id, "factor": r.factor, "market": meta[r.stock_id].market,
             "sector": meta[r.stock_id].sector, "raw": json.loads(json.dumps(r.raw))} for r in results]  # fmt: skip


@pytest.mark.parametrize("seed", [1, 2, 3, 7])
def test_저장된_원값으로_낸_참고_점수가_처음부터_낸_것과_같다(seed: int) -> None:
    universe = _종목들(60, seed)
    extra = _종목들(1, seed + 100)[0]
    extra.stock_id, extra.sector = 999, "가"
    처음부터 = {(r.stock_id, r.factor): r for r in sc.score_factors([*universe, extra])}
    저장 = _저장(sc.score_factors(universe), universe)  # 그날 점수 작업
    다시 = sc.inputs_from_stored(저장)
    assert 다시 is not None
    가볍게 = {(r.stock_id, r.factor): r for r in sc.score_factors([*다시, extra])}
    for f in sc.FACTORS:
        a, b = 처음부터[(999, f)], 가볍게[(999, f)]
        assert (a.zscore, a.score, a.peer_group, a.peer_size, a.missing_fields) == \
               (b.zscore, b.score, b.peer_group, b.peer_size, b.missing_fields), f  # fmt: skip


def test_회복_기간_하한이_원값에_실린다() -> None:
    universe = _종목들(40, 5)
    risk = [r for r in sc.score_factors(universe) if r.factor == "risk"]
    assert all(sc.UNRECOVERED_KEY in r.raw for r in risk)
    # 하한이 없는 옛 원값(25.1027 전)이면 다시 만들지 않는다 — 부르는 쪽이 처음부터 읽는다
    저장 = _저장(sc.score_factors(universe), universe)
    for r in 저장:
        if r["factor"] == "risk":
            r["raw"].pop(sc.UNRECOVERED_KEY)
    assert sc.inputs_from_stored(저장) is None


def test_참고_분석은_저장된_원값이_있으면_유니버스_재료를_읽지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    universe = _종목들(40, 9)
    for s in universe:
        c.execute("INSERT INTO stocks (id, ticker, market, country, sector, currency, source, fetched_at)"
                  " VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 't', 't')", [s.stock_id, f"{s.stock_id:06d}", s.sector])  # fmt: skip
    for r in sc.score_factors(universe):
        c.execute("INSERT INTO factors (stock_id, as_of_date, factor, raw_json, zscore, score, peer_group, peer_size,"
                  " calc_version, created_at) VALUES (?, '2026-10-07', ?, ?, ?, ?, ?, ?, ?, 't')",
                  [r.stock_id, r.factor, json.dumps(r.raw), r.zscore, r.score, r.peer_group, r.peer_size,
                   sc.CALC_VERSION])  # fmt: skip
    for s in universe:
        c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                  " weights_json, calc_version, created_at) VALUES (?, '2026-10-07', ?, '{}', 0, '{}', ?, 't')",
                  [s.stock_id, float(s.stock_id), sc.CALC_VERSION])  # 종합 점수 1~40
    monkeypatch.setattr(job, "full_inputs", lambda *a: pytest.fail("유니버스 재료를 처음부터 읽었다"))
    extra = _종목들(1, 77)[0]
    extra.stock_id, extra.sector = 999, "가"
    monkeypatch.setattr(job, "extra_inputs", lambda *a: [extra])
    monkeypatch.setattr(job.sj, "load_weights", lambda c: ({f: 20.0 for f in sc.FACTORS}, 0.0, []))
    감성_종목: list = []
    monkeypatch.setattr(job.sj, "load_sentiments", lambda *a, stock_ids=None: 감성_종목.append(stock_ids) or ({}, None))
    out = job.score_extra(mem, "KR", "2026-10-07", [{"stock_id": 999}])  # type: ignore[arg-type]
    assert out[999]["path"] == "stored" and out[999]["ranked"] == 40
    total = out[999]["total"]
    assert out[999]["rank"] == 1 + sum(1 for v in range(1, 41) if v > total)
    assert 감성_종목 == [[999]]  # 감성도 이 종목만 읽는다 (25.1028)
    # 점수 작업 전(그날 원값 없음)이면 처음부터 읽는 길로 간다
    읽음: list = []
    monkeypatch.setattr(job, "full_inputs", lambda *a: 읽음.append(1) or [])
    assert job.score_extra(mem, "KR", "2026-10-08", [{"stock_id": 999}]) == {}  # type: ignore[arg-type]
    assert 읽음 == [1]


def test_감성은_고른_종목만_읽고_나라_전체는_예전과_같다() -> None:
    """25.1028 — 사용자 "감성 점수도 줄여줘"."""
    from batch.jobs import scores as sj

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid in (1, 2, 3):
        c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                  " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 't', 't')", [sid, f"00000{sid}"])  # fmt: skip
        for d, v in (("2026-10-06", 10.0 * sid), ("2026-10-07", 20.0 * sid)):
            c.execute("INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count,"
                      " negative_count, negative_count_7d, decay_halflife_days, method, calc_version, created_at)"
                      " VALUES (?, ?, ?, 5, 3, 1, 0, 7, 'vader', 1, 't')", [sid, d, v])  # fmt: skip
    전체, _ = sj.load_sentiments(mem, "KR", "2026-10-07")  # type: ignore[arg-type]
    assert 전체 == {1: 20.0, 2: 40.0, 3: 60.0}
    골라, _ = sj.load_sentiments(mem, "KR", "2026-10-07", stock_ids=[2])  # type: ignore[arg-type]
    assert 골라 == {2: 40.0}
