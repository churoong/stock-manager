"""공시 반응 통계 (docs/disclosure_reaction.md, docs/infra.md 25.996) — 손으로 셀 수 있는 고정 데이터."""

from __future__ import annotations

from batch.services import disclosure_reaction as dr


def test_제목으로_가른다_정정은_뺀다() -> None:
    assert dr.classify("주요사항보고서(자기주식 취득 결정)") == "buyback"
    assert dr.classify("주요사항보고서(유상증자결정)") == "rights"
    assert dr.classify("단일판매ㆍ공급계약체결") == "supply"
    assert dr.classify("영업(잠정)실적(공정공시)") == "earnings"
    assert dr.classify("[기재정정]주요사항보고서(유상증자결정)") is None
    assert dr.classify("임원ㆍ주요주주특정증권등소유상황보고서") is None


DATES = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09", "2026-03-10"]


def test_전_거래일_종가부터_5거래일째까지_지수를_뺀다() -> None:
    # 공시일 03-03 → 전 거래일 03-02 종가 100, 5거래일째(03-03·04·05·06·09) 03-09 종가 110 → +10%. 지수 +2% → +8%p
    closes = [100.0, 101.0, 102.0, 104.0, 106.0, 110.0, 111.0]
    index = dict(zip(DATES, [1000.0, 1000.0, 1005.0, 1010.0, 1015.0, 1020.0, 1030.0], strict=True))
    assert round(dr.car(DATES, closes, [1.0] * 7, index, "2026-03-03"), 6) == 0.08
    # 주말 공시(03-07)는 다음 거래일(03-09)부터 센다 — 5거래일이 모자라면 재지 않는다
    assert dr.car(DATES, closes, [1.0] * 7, index, "2026-03-07") is None
    # 기간 안 분할(수정계수가 바뀜)이면 뺀다
    assert dr.car(DATES, closes, [1.0, 1.0, 0.5, 0.5, 0.5, 0.5, 0.5], index, "2026-03-03") is None


def test_요약과_한_줄은_웹과_같은_문장() -> None:
    rows = {r["type"]: r for r in dr.summarize({"buyback": [0.01] * 60 + [-0.005] * 60})}
    b = rows["buyback"]
    assert b["n"] == 120 and b["pos_pct"] == 50.0 and b["mean_pct"] == 0.25
    b = {**b, "mean_pct": 1.234, "median_pct": 0.81, "pos_pct": 56.2}
    # web/__tests__/disclosureReaction996.test.ts 의 기대 문장과 같다
    assert dr.line(b) == "자기주식 취득 공시 뒤 5거래일 지수 대비 중앙값 +0.8% · 평균 +1.2% · 오른 비율 56% (120건)"
    assert dr.line({**b, "n": dr.MIN_N - 1}) is None
