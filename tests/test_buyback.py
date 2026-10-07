"""자기주식 취득 결정 공시 IC (docs/factors.md 12.2, docs/infra.md 25.482)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.services import buyback as bb


@pytest.mark.parametrize(
    ("title", "want"),
    [("주요사항보고서(자기주식취득결정)", True), ("주요사항보고서(자기주식 취득 결정)", True),
     ("[기재정정]주요사항보고서(자기주식취득결정)", False), ("주요사항보고서(자기주식처분결정)", False),
     ("자기주식취득신탁계약체결결정", False), ("분기보고서", False),
     ("자기주식취득결정(자회사의 주요경영사항)", False)],
)  # fmt: skip
def test_제목_거르기(title: str, want: bool) -> None:
    assert bb.is_buyback(title) is want


def test_창_안이면_1_밖이면_0_수집이_못_덮으면_없다() -> None:
    구간 = [("2021-01-04", "2026-10-01")]
    assert bb.flag(["2026-03-01"], "2026-09-01", 구간) == 1
    assert bb.flag(["2025-08-31"], "2026-09-01", 구간) == 0  # 365일 밖
    assert bb.flag(["2026-09-02"], "2026-09-01", 구간) == 0  # 기준일 뒤는 없다
    assert bb.flag([], "2026-09-01", [("2026-06-01", "2026-10-01")]) is None  # 수집이 3개월뿐 — 모른다
    assert bb.flag([], "2026-09-01", []) is None


def test_한_번_전_종목_수집_뒤로는_모른다() -> None:
    """--all 을 10월에 한 번 돌린 뒤에는 지켜보는 종목만 쌓인다 — 그 뒤 창은 0 이 아니라 None (25.484, 교차검증)."""
    구간 = [("2021-10-01", "2026-10-01")]
    assert bb.flag([], "2027-02-01", 구간) is None
    assert bb.flag([], "2026-09-30", 구간) == 0


def test_이어지는_구간은_합친다() -> None:
    assert bb.covered([("2025-01-01", "2025-06-30"), ("2025-07-01", "2026-01-31")], "2025-02-01", "2026-01-15")
    assert not bb.covered([("2025-01-01", "2025-06-29"), ("2025-07-01", "2026-01-31")], "2025-02-01", "2026-01-15")


def test_백테스트_IC_는_국내만(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = ["2026-03-30", "2026-03-31", "2026-04-29", "2026-04-30"]
    산 = set(range(31, 41))
    prices = {sid: {"2026-03-30": 100.0, "2026-03-31": 100.0,
                    "2026-04-30": 120.0 + sid if sid in 산 else 100.0 + sid / 100} for sid in ids}  # fmt: skip
    사건 = ({sid: ["2026-01-15"] for sid in 산}, {sid: [("2021-01-04", "2026-09-28")] for sid in ids})

    def 돌림(market: str):  # noqa: ANN202
        monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
            SimpleNamespace(stock_id=r["stock_id"], market=market, sector=None, metrics={}) for r in rows])
        monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
            SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=float(i.stock_id)) for i in inputs])
        return job.factor_ics([{"stock_id": s} for s in ids], {}, prices, dates, ["2026-03-31", "2026-04-30"], {},
                              buybacks=사건)["buyback"]  # fmt: skip

    국내 = 돌림("KOSPI")
    assert 국내.months == 1 and 국내.mean is not None and 국내.mean > 0.5
    assert 돌림("NASDAQ").months == 0


def test_전_종목_수집에서_다_받은_종목만_안다() -> None:
    """수집 당시 대상이 아니었거나 잘린 종목은 모른다 (docs/infra.md 25.487, 교차검증)."""
    import json

    from batch.jobs import backtest as job
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    for sid in (1, 2):
        db.종목(sid, "KR")
    db.conn.execute(
        "INSERT INTO batch_runs (job_name, market, trade_date, status, started_at, step_log)"
        " VALUES ('disclosures_kr', 'KR', '2026-09-28', 'partial', 't', ?)",
        [json.dumps({"all": True, "days": 400, "covered_ids": [1]})],
    )
    _, 구간 = job.load_buyback_events(db, "KR")  # type: ignore[arg-type]
    assert 구간 == {1: [("2025-08-24", "2026-09-28")]}
    assert bb.flag([], "2026-08-31", 구간.get(2, [])) is None  # 2번은 수집되지 않았다 — 0 이 아니다
    assert bb.flag([], "2026-08-31", 구간[1]) == 0
