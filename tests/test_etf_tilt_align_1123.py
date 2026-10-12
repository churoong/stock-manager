"""국내 ETF 순위가 KODEX 전 종목과 KIS 상위 30 을 절대 합으로 섞어 견줬다 (docs/infra.md 25.1123, ETF 감사)."""

from __future__ import annotations

from batch.jobs import etf_tilt as job
from batch.services import etf_tilt as tilt
from batch.sources import kis, kodex_pdf


def _doc(n: int, source: str, w: float) -> job.Fetched:
    return job.Fetched([(f"{i:06d}", w) for i in range(n)], "문서", "2026-10-01", "2026-10-01", source)


def test_섞이면_KODEX_도_비중_상위_N_만_본다() -> None:
    # KODEX 전 종목: 머리 3종목 각 20%, 꼬리 40종목 각 1% (꼬리에 우리 상위 종목이 몰렸다)
    full = job.Fetched(
        [("H0", 20.0), ("H1", 20.0), ("H2", 20.0)] + [(f"T{i:02d}", 1.0) for i in range(40)],
        "KODEX 1 20261001", "2026-10-01", "2026-10-01", kodex_pdf.SOURCE,
    )
    부분 = _doc(30, kis.SOURCE, 2.8)
    out, n = job.align_kr_sources({"A": full, "B": 부분})
    assert n == 30 and out["B"] is 부분
    assert len(out["A"].holdings) == 30 and out["A"].holdings[:3] == [("H0", 20.0), ("H1", 20.0), ("H2", 20.0)]
    assert "상위 30개" in out["A"].accession and out["A"].source == kodex_pdf.SOURCE
    # 순위 기준 T 도 같은 모양에서 — 꼬리 40종목 중 27종목만 남는다
    stocks = {f"T{i:02d}": (i + 1, f"T{i:02d}", "") for i in range(40)}
    t = tilt.compute(out["A"].holdings, {k: k for k, _ in out["A"].holdings}, stocks, lambda _sid: 50.0, None,
                     {sid for sid, _s, _n in stocks.values()})  # fmt: skip
    assert round(t.top_pct, 6) == 27.0


def test_종목이_적어_다_온_KIS_목록은_자르는_잣대가_아니다() -> None:
    """10종목 업종 ETF(KIS 로 전부, 합 100%) 하나 때문에 KODEX 200 이 상위 10 으로 잘려 덮은 비중 하한에 걸렸다 (25.1199)."""
    kodex200 = _doc(200, kodex_pdf.SOURCE, 0.5)
    업종 = _doc(10, kis.SOURCE, 10.0)
    out, n = job.align_kr_sources({"A": kodex200, "S": 업종})
    assert n is None and out["A"] is kodex200
    # 잘린 KIS(상위 30, 합 84%)가 함께 있으면 그 길이(30)로 맞춘다 — 다 온 10종목이 잣대가 되지 않는다
    잘린 = _doc(30, kis.SOURCE, 2.8)
    out2, n2 = job.align_kr_sources({"A": kodex200, "S": 업종, "B": 잘린})
    assert n2 == 30 and len(out2["A"].holdings) == 30 and out2["S"] is 업종


def test_섞이지_않으면_그대로() -> None:
    a, b = _doc(200, kodex_pdf.SOURCE, 0.5), _doc(30, kis.SOURCE, 2.8)
    assert job.align_kr_sources({"A": a}) == ({"A": a}, None)
    assert job.align_kr_sources({"B": b}) == ({"B": b}, None)
    assert job.align_kr_sources({}) == ({}, None)


def test_순위는_자른_보유로_내고_계좌_노출은_전_종목을_쓴다() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "etf_tilt.py").read_text(encoding="utf-8")
    assert "for key, d in tilt_docs.items()" in src
    assert "store_lookthrough(client, picks, sources, docs, markets)" in src
