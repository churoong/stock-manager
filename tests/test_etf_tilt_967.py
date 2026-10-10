"""우리 점수를 많이 담은 ETF (docs/etf.md 11.2·11.3, docs/infra.md 25.967) — N-PORT 보유 × 종합 점수 가중평균."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from batch.services import etf_tilt as tilt
from batch.sources import sec_nport as sn

DOC = b"""<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData><genInfo><repPdDate>2026-06-30</repPdDate></genInfo>
  <invstOrSecs>
    <invstOrSec><name>Apple Inc</name><cusip>037833100</cusip>
      <identifiers><isin value="US0378331005"/></identifiers><pctVal>7.5</pctVal><assetCat>EC</assetCat></invstOrSec>
    <invstOrSec><name>Cash Fund</name><cusip>N/A</cusip><pctVal>0.4</pctVal></invstOrSec>
    <invstOrSec><name>Swap</name><cusip>N/A</cusip><pctVal>-0.01</pctVal></invstOrSec>
  </invstOrSecs></formData>
</edgarSubmission>"""

FEED = """<feed><entry><content type="text/xml"><accession-nunber>0002071691-26-019760</accession-nunber>
<filing-date>2026-08-28</filing-date><filing-type>NPORT-P</filing-type></content></entry>
<entry><content><accession-nunber>0002071691-26-000001</accession-nunber><filing-date>2026-08-29</filing-date>
<filing-type>NPORT-P/A</filing-type></content></entry></feed>"""


class Test읽기:
    def test_펀드_티커(self) -> None:
        payload = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[36405, "S000002839", "C1", "voo"]]}
        assert sn.parse_fund_tickers(payload) == {"VOO": ("0000036405", "S000002839")}

    def test_시리즈_목록은_정정을_빼고_오타_태그도_읽는다(self) -> None:
        assert sn.parse_series_feed(FEED) == [("0002071691-26-019760", "2026-08-28")]

    def test_본문(self) -> None:
        doc = sn.parse_doc(DOC, "A", "2026-08-28")
        assert doc.report_date == "2026-06-30" and len(doc.holdings) == 3
        assert doc.holdings[0] == sn.Holding("Apple Inc", "037833100", "US0378331005", 7.5)

    def test_깨진_본문은_못_받음이다(self) -> None:
        with pytest.raises(sn.SecFailed):
            sn.parse_doc(b"<x", "A", "d")

    def test_결제실패_파일(self) -> None:
        text = "SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE\n20260901|037833100|AAPL|10|APPLE|1\n"
        assert sn.parse_ftd(text) == {"037833100": "AAPL"}

    def test_달_거슬러_세기(self) -> None:
        from datetime import date

        assert sn._months_back(date(2026, 2, 3), 3) == ["202601", "202512", "202511"]


def stocks() -> dict[str, tuple[int, str, str]]:
    return {"AAPL": (1, "AAPL", "Apple"), "MSFT": (2, "MSFT", "Microsoft"), "BRKB": (3, "BRK.B", "Berkshire")}


CUSIPS = {"C1": "AAPL", "C2": "MSFT", "C3": "BRK/B", "C4": "ZZZZ"}


class Test가중평균:
    def test_덮은_비중으로_나눈다(self) -> None:
        scores = {1: 80.0, 2: 60.0, 3: 50.0}
        t = tilt.compute([("C1", 50.0), ("C2", 30.0), ("C3", 10.0), ("C4", 9.0), ("X", 1.0), ("C1", -0.5)],
                         CUSIPS, stocks(), scores.get, {2})  # fmt: skip
        # (50×80 + 30×60 + 10×50) / 90 = 6300/90 = 70
        assert t.avg_score == pytest.approx(70.0)
        assert (t.coverage_pct, t.matched_pct, t.total_pct, t.rec_pct) == (90.0, 90.0, 100.0, 30.0)
        assert [c.symbol for c in t.top] == ["AAPL", "MSFT", "BRK.B"]  # w×S 순
        assert t.holdings == 6

    def test_점수_없는_몫이_크면_평균을_내지_않는다(self) -> None:
        t = tilt.compute([("C1", 60.0), ("C4", 40.0)], CUSIPS, stocks(), {1: 80.0}.get, None)
        assert t.coverage_pct == 60.0 < tilt.COVERAGE_MIN_PCT and t.avg_score is None and t.rec_pct is None

    def test_점수가_없는_종목은_매칭엔_들고_덮음엔_안_든다(self) -> None:
        t = tilt.compute([("C1", 80.0), ("C2", 20.0)], CUSIPS, stocks(), {1: 80.0}.get, set())
        assert (t.matched_pct, t.coverage_pct, t.avg_score, t.rec_pct) == (100.0, 80.0, 80.0, 0.0)

    def test_순위(self) -> None:
        assert tilt.rank_within({"Large Blend": [(1, 60.0), (2, 70.0), (3, None)], "Small": [(4, 50.0)]}) == {
            2: (1, 2), 1: (2, 2), 4: (1, 1)
        }


def _zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("f.txt", text)
    return buf.getvalue()


class Test작업:
    """가장 새 핵심 판정 행에 tilt 를 붙인다. 판정·점수 칸은 그대로다."""

    def test_미국과_국내_대리_보유와_넓힌_풀(self, monkeypatch) -> None:
        from batch.core import db
        from batch.jobs import etf_tilt as job
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        db.apply_migrations(mem)  # type: ignore[arg-type]
        c = mem.conn
        for sid, t, n in ((1, "AAPL", "Apple"), (2, "MSFT", "Microsoft")):
            c.execute(
                "INSERT INTO stocks (id, ticker, yahoo_symbol, market, country, name_en, currency, status, source,"
                " fetched_at) VALUES (?, ?, ?, 'NASDAQ', 'US', ?, 'USD', 'active', 't', 't')",
                [sid, t, t, n],
            )
        for sid, code, n in ((11, "005930", "삼성전자"), (12, "000660", "SK하이닉스")):
            c.execute(
                "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 'active', 't', 't')",
                [sid, code, n],
            )
        for sid, sc in ((1, 80.0), (2, 60.0), (11, 80.0), (12, 50.0)):
            c.execute(
                "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                " weights_json, calc_version, created_at)"
                " VALUES (?, '2026-10-02', ?, ?, 0, '{}', 10, 't')",
                # 순서는 장기 점수(밸류·퀄리티 평균)로 낸다 (25.1108) — 종합 점수와 같은 값을 두 팩터에 둔다
                [sid, sc, json.dumps({"value": sc, "quality": sc, "momentum": 0.0})],
            )
        etfs = [(1, "VTI", "US", "미국 주식", "Large Blend", 1), (2, "IVV", "US", "미국 주식", "Large Blend", 1),
                (3, "360750", "KR", "해외 주식", "S&P 500", 1), (4, "069500", "KR", "국내 주식", "코스피 200", 1),
                # 넓은 지수가 아니라 핵심에서 빠진 업종 ETF — 후보 풀(11.5)에는 든다
                (5, "SOXX", "US", None, "Technology", 0),
                # 레버리지 — 풀에서도 빠진다
                (6, "SOXL", "US", None, "Trading--Leveraged Equity", 0),
                # 국내 상장 미국 지수 ETF (25.970) — 핵심 밖이어도 대리 보유(SOXX)로 국내 순위에 든다
                (7, "381180", "KR", None, "PHLX Semiconductor Sector Index", 0),
                # 같은 지수라도 커버드콜은 뺀다
                (8, "999999", "KR", None, "PHLX Semiconductor Sector Index", 0),
                # 국내 지수 (25.974) — KODEX 200 의 구성종목으로, 같은 지수의 다른 운용사 상품도 그것으로
                (9, "102110", "KR", "국내 주식", "코스피 200", 0)]  # fmt: skip
        옛 = '{"criteria": [1], "tilt": {"rank_all": 1}}'  # 지난 실행이 남긴 순위 — 이번에 풀 밖이면 지워져야 한다
        for eid, sym, country, bucket, cat, passed in etfs:
            c.execute(
                "INSERT INTO etfs (id, symbol, country, name, source, fetched_at) VALUES (?, ?, ?, ?, 't', 't')",
                [eid, sym, country, "TIGER 미국반도체 커버드콜" if sym == "999999" else sym],
            )
            c.execute(
                "INSERT INTO etf_profiles (etf_id, as_of_date, category, currency, stock_position, total_assets,"
                " source, fetched_at) VALUES (?, '2026-10-02', ?, 'USD', 0.99, 1e10, 't', 't')",
                [eid, cat],
            )
            c.execute(
                "INSERT INTO etf_picks (etf_id, as_of_date, bucket, passed, score, rationale_text, rationale_data,"
                " calc_version, created_at) VALUES (?, '2026-10-02', ?, ?, 100, 'x', ?, 5, 't')",
                [eid, bucket, passed, 옛 if sym == "SOXL" else '{"criteria": [1]}'],
            )

        holdings = {"VTI": [("C1", 50.0), ("C2", 50.0)], "IVV": [("C1", 90.0), ("C2", 10.0)], "SOXX": [("C1", 100.0)]}
        series = {"S1": "VTI", "S2": "IVV", "S3": "SOXX", "S4": "SOXL"}  # 국내 반도체는 SOXX 대리

        series_of = series

        class FakeNport:
            def __init__(self, sec) -> None:
                pass

            def fund_tickers(self):
                return {v: ("0000000001", k) for k, v in series.items()}

            def ftd_cusips(self, today):
                return {"C1": "AAPL", "C2": "MSFT"}

            def latest(self, cik, series):
                sym = series_of[series]
                return sn.NportDoc(f"acc-{sym}", "2026-08-28", "2026-06-30",
                                   [sn.Holding("", cu, "", w) for cu, w in holdings[sym]])  # fmt: skip

        class FakeSec:
            calls = 7

        monkeypatch.setattr(job.sec_edgar, "SecClient", lambda: FakeSec())
        monkeypatch.setattr(job.sec_nport, "NportClient", FakeNport)

        from batch.sources import kodex_pdf as kp

        class FakeKodex:
            calls = 2

            def products(self):
                return [kp.Product("2ETF01", "069500", "KODEX 200")]

            def pdf(self, product, base):
                return kp.PdfDoc(product.fid, product.ticker, "2026-10-02",
                                 [kp.Holding("005930", "삼성전자", 60.0), kp.Holding("000660", "SK하이닉스", 40.0)])  # fmt: skip

        monkeypatch.setattr(job.kodex_pdf, "KodexClient", FakeKodex)
        assert job.run() == 0

        got = {
            sym: json.loads(data)
            for sym, data in c.execute(
                "SELECT e.symbol, pk.rationale_data FROM etf_picks pk JOIN etfs e ON e.id = pk.etf_id"
            ).fetchall()
        }
        assert got["VTI"]["tilt"]["avg_score"] == pytest.approx(70.0) and got["VTI"]["tilt"]["vs_market"] == 0
        assert got["IVV"]["tilt"]["avg_score"] == pytest.approx(78.0)  # 90×80 + 10×60
        assert got["IVV"]["tilt"]["vs_market"] == pytest.approx(8.0) and got["IVV"]["tilt"]["rank"] == 1
        # 국내 S&P 500 은 IVV 의 보유로 — 순위는 내지 않는다
        kr = got["360750"]["tilt"]
        assert kr["proxy"] is True and kr["source_symbol"] == "IVV" and kr["avg_score"] == pytest.approx(78.0)
        assert kr["rank"] is None and kr["accession"] == "acc-IVV" and kr["score_as_of"] == "2026-10-02"
        # 국내 지수 (25.974): KODEX 200 구성종목 · 국내 점수 · 국내 상위 10% = 삼성전자(2종목 중 하나)
        k200, tiger200 = got["069500"]["tilt"], got["102110"]["tilt"]
        assert k200["source"] == "samsungfund_kodex" and k200["proxy"] is False and k200["group"] == "kr_kr"
        assert k200["top_pct"] == 60.0 and k200["avg_score"] == pytest.approx(68.0)  # 60×80 + 40×50
        assert k200["benchmark"] == "KODEX 200" and k200["vs_market"] == 0 and k200["accession"] == "KODEX 2ETF01 2026-10-02"
        assert tiger200["proxy"] is True and tiger200["source_symbol"] == "069500" and tiger200["rank_all_size"] == 2
        assert got["360750"]["tilt"]["group"] == "kr_us"  # 국내 상장 미국 지수는 따로 줄 세운다
        assert got["IVV"]["criteria"] == [1]  # 판정 칸은 그대로
        run = c.execute("SELECT status, step_log FROM batch_runs WHERE job_name = 'etf_tilt'").fetchone()
        assert run[0] == "success" and json.loads(run[1])["updated"] == 7
        # 넓힌 풀 (25.968): 상위 비중 T 로 전체 순위. 상위 10% = AAPL 한 종목(2종목 중 하나)
        assert [got[s]["tilt"]["rank_all"] for s in ("SOXX", "IVV", "VTI")] == [1, 2, 3]
        assert got["SOXX"]["tilt"]["top_pct"] == 100.0 and got["SOXX"]["tilt"]["pool"] == "wide"
        assert got["SOXX"]["tilt"]["market_top_pct"] == 50.0 and got["IVV"]["tilt"]["pool"] == "core"
        assert "tilt" not in got["SOXL"]  # 레버리지는 풀 밖 — 지난 실행의 순위도 지운다 (25.969)
        # 국내 상장은 국내끼리 순위 (25.970): 반도체(SOXX 대리, T 100) 1위 · S&P 500(IVV 대리, T 90) 2위
        assert got["381180"]["tilt"]["source_symbol"] == "SOXX" and got["381180"]["tilt"]["rank_all"] == 1
        assert got["360750"]["tilt"]["rank_all"] == 2 and got["360750"]["tilt"]["rank_all_size"] == 2
        assert "tilt" not in got["999999"]


class Test넓힌_풀:
    """25.968 — 넓은 지수 조건 없이 주식형 ETF 전체를 상위 비중으로 줄 세운다."""

    def test_상위_종목과_문턱(self) -> None:
        ids, cut = tilt.top_ids_of({i: float(i) for i in range(1, 101)})
        assert cut == 91.0 and ids == set(range(91, 101))
        assert tilt.top_ids_of({}) == (set(), None)
        assert tilt.top_ids_of({1: 5.0, 2: 5.0, 3: 1.0})[0] == {1, 2}  # 동점은 함께

    def test_상위_비중(self) -> None:
        t = tilt.compute([("C1", 40.0), ("C2", 60.0)], CUSIPS, stocks(), {1: 80.0, 2: 60.0}.get, None, {1})
        assert t.top_pct == 40.0

    def test_풀(self) -> None:
        from batch.jobs import etf_tilt as job

        base = {"country": "US", "passed": 0, "name": "iShares Semiconductor ETF", "category": "Technology",
                "stock_position": 0.99, "total_assets": 1e10}  # fmt: skip
        assert job.in_pool(base)
        assert not job.in_pool({**base, "name": "Direxion Daily Semiconductor Bull 3X"})
        assert not job.in_pool({**base, "category": "Trading--Leveraged Equity"})
        # 옵션 전략 상품 (25.969 — AIPI 가 17위에 올랐다)
        assert not job.in_pool({**base, "name": "REX AI Equity Premium Income ETF", "category": "Derivative Income"})
        assert not job.in_pool({**base, "category": "Defined Outcome"})
        assert not job.in_pool({**base, "stock_position": 0.1})  # 채권
        assert not job.in_pool({**base, "total_assets": 5e7})
        assert job.in_pool({**base, "passed": 1, "stock_position": 0.0})  # 핵심 통과는 그대로
        assert not job.in_pool({"country": "KR", "passed": 1, "bucket": "국내 주식"})


class Test국내_상장:
    """25.970 — 미국 지수를 따르는 국내 상장 ETF 를 대리 보유로 국내끼리 줄 세운다."""

    def test_풀(self) -> None:
        from batch.jobs import etf_tilt as job

        base = {"country": "KR", "passed": 0, "name": "TIGER 미국필라델피아반도체나스닥",
                "category": "PHLX Semiconductor Sector Index", "total_assets": 2e12}  # fmt: skip
        assert job.in_pool(base)
        assert not job.in_pool({**base, "name": "TIGER 미국필라델피아반도체레버리지(합성)"})
        assert not job.in_pool({**base, "name": "KODEX 미국반도체 2X"})
        assert not job.in_pool({**base, "total_assets": 5e9})  # 100억원 미만
        assert not job.in_pool({**base, "category": "FnGuide 반도체TOP10 지수"})  # 대리 보유 없음
        # 액티브·선물형은 보유가 지수와 달라 대리 보유를 쓰지 않는다 (25.971)
        assert not job.in_pool({**base, "name": "TIGER 글로벌이노베이션액티브", "category": "NASDAQ 100"})
        assert not job.in_pool({**base, "name": "KODEX 미국나스닥100선물(H)", "category": "NASDAQ 100"})
        assert not job.in_pool({**base, "name": "TIGER 미국나스닥100액티브", "category": "NASDAQ 100", "passed": 1})

    def test_같은_값은_순자산_큰_순(self) -> None:
        assert tilt.rank_by([(1, 30.0, 1e9), (2, 30.0, 5e9), (3, 40.0, 1.0), (4, None, 9e9)]) == {
            3: (1, 3), 2: (2, 3), 1: (3, 3)
        }


def test_순서는_모멘텀이_아니라_장기_점수로_낸다() -> None:
    """종합 점수에 모멘텀·CAGR 이 들어 있어 첫 화면 순서를 최근 수익률이 정했다 (docs/infra.md 25.1108, 사용자 확인)."""
    from batch.jobs import etf_tilt as job

    assert job.long_score('{"value": 80, "quality": 60, "momentum": 100, "risk": 100}') == 70.0
    assert job.long_score('{"value": 80, "momentum": 100}') == 80.0  # 하나만 있으면 그것
    assert job.long_score('{"momentum": 100, "risk": 90}') is None  # 장기 팩터가 없으면 점수 없음
    assert job.long_score('{"value": NaN}') is None and job.long_score("깨짐") is None
