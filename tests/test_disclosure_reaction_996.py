"""공시 반응 통계 (docs/disclosure_reaction.md, docs/infra.md 25.996) — 손으로 셀 수 있는 고정 데이터."""

from __future__ import annotations

import pytest

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


def test_시장_전체_공시는_키를_환경에서_읽고_쪽을_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """운영 첫 실행이 `config.DART_API_KEY`(없는 이름)로 AttributeError 를 냈다 — 함수를 끝까지 돌려 본다 (25.1000)."""
    from batch.sources import dart_disclosures as dd

    monkeypatch.delenv("DART_API_KEY", raising=False)
    assert dd.fetch_market_day("20261006", "B") == ([], 0, "DART_API_KEY 없음")

    monkeypatch.setenv("DART_API_KEY", "k")
    쪽들 = {
        1: {"status": "000", "total_page": 2, "list": [
            {"rcept_no": "20261006000001", "report_nm": "자기주식취득결정", "stock_code": "005930", "corp_code": "1"},
            {"rcept_no": "20261006000002", "report_nm": "비상장", "stock_code": "", "corp_code": "2"},
        ]},
        2: {"status": "000", "total_page": 2, "list": [
            {"rcept_no": "20261006000003", "report_nm": "유상증자결정", "stock_code": "000660", "corp_code": "3"},
        ]},
    }  # fmt: skip
    받은: list[dict] = []

    class 응답:
        status_code = 200

        def __init__(self, body: dict) -> None:
            self.body = body

        def json(self) -> dict:
            return self.body

    def 가짜(url: str, params: dict, timeout: float) -> 응답:
        받은.append(params)
        return 응답(쪽들[params["page_no"]])

    monkeypatch.setattr(dd.requests, "get", 가짜)
    got, calls, err = dd.fetch_market_day("20261006", "B")
    assert err is None and calls == 2
    assert [d.stock_code for d in got] == ["005930", "000660"]
    assert 받은[0]["crtfc_key"] == "k" and 받은[0]["pblntf_ty"] == "B"


def test_시장_전체_공시가_쪽_상한에서_잘리면_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """교차검증(25.1006): 예전에는 20쪽에서 조용히 성공으로 끝났다."""
    from batch.sources import dart_disclosures as dd

    monkeypatch.setenv("DART_API_KEY", "k")
    monkeypatch.setattr(dd, "MARKET_MAX_PAGES", 2)

    class 응답:
        status_code = 200

        def json(self) -> dict:
            return {"status": "000", "total_page": 9, "list": []}

    monkeypatch.setattr(dd.requests, "get", lambda *a, **k: 응답())
    got, calls, err = dd.fetch_market_day("20261006", "I")
    assert calls == 2 and err == "잘림: 20261006 I 9쪽 중 2쪽"


def test_1년_모으기가_멈추면_계산하지_않아_다음에_다시_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """교차검증(25.1006): 멈춘 채 계산하면 표가 차서 다음 실행이 7일만 받았다."""
    from batch.core import db
    from batch.jobs import disclosure_reaction as job
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    받은날수: list[int] = []

    def 가짜_모으기(client, today, days):  # noqa: ANN001, ANN202
        받은날수.append(days)
        return {"days": days, "calls": 1, "rows": 0, "error": "DART 일일 한도 — 나머지 날은 다음 실행에"}

    계산: list[int] = []
    monkeypatch.setattr(job, "collect", 가짜_모으기)
    monkeypatch.setattr(job, "compute", lambda client, today: 계산.append(1) or {"events": 1})
    assert job.run() == 0
    assert job.run() == 0
    assert 받은날수 == [job.BACKFILL_DAYS, job.BACKFILL_DAYS] and 계산 == []


def test_시장_전체_공시는_시간_초과를_다시_묻는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1010: 10-07 첫 1년 모으기가 읽기 시간 초과 한 번으로 3월에서 멈춰 4~9월이 비었다."""
    import requests

    from batch.sources import dart_disclosures as dd

    monkeypatch.setenv("DART_API_KEY", "k")
    monkeypatch.setattr(dd.time, "sleep", lambda s: None)
    차례 = [requests.ReadTimeout("read timed out"), requests.ReadTimeout("read timed out")]

    class 응답:
        status_code = 200

        def json(self) -> dict:
            return {"status": "000", "total_page": 1, "list": [
                {"rcept_no": "20260415000001", "report_nm": "유상증자결정", "stock_code": "000660", "corp_code": "3"}]}

    def 가짜(*a, **k):  # noqa: ANN002, ANN003, ANN202
        if 차례:
            raise 차례.pop(0)
        return 응답()

    monkeypatch.setattr(dd.requests, "get", 가짜)
    got, calls, err = dd.fetch_market_day("20260415", "B")
    assert err is None and calls == 3 and len(got) == 1

    차례[:] = [requests.ReadTimeout("x")] * 3
    got, calls, err = dd.fetch_market_day("20260415", "B")
    assert got == [] and calls == 3 and err is not None and err.startswith("호출 실패")
