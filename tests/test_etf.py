"""ETF 장기 적립 판정 테스트.

픽스처(tests/fixtures/us_fund_profiles.json)는 2026-09-17 야후 실제 응답에서
읽는 필드만 남긴 것이다. 추측한 구조가 아니라 본 구조로 검증한다.
네트워크를 타지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from batch.jobs import etf as job
from batch.services import etf as svc
from batch.sources import krx, yahoo_fund
from batch.sources.yfinance_src import DailyBar

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "us_fund_profiles.json").read_text(encoding="utf-8"))
AS_OF = date(2026, 9, 17)
FETCHED = "2026-09-17"


def profile(symbol: str) -> yahoo_fund.FundProfile:
    parsed = yahoo_fund.parse_quote_summary(symbol, FIXTURE[symbol])
    assert parsed is not None
    return parsed


def evaluate(symbol: str, name: str = "Some Index ETF") -> svc.Evaluation:
    return svc.evaluate(symbol, name, profile(symbol), AS_OF, FETCHED)


class Test응답해석:
    def test_VOO_의_값을_그대로_읽는다(self) -> None:
        p = profile("VOO")
        assert p.category == "Large Blend"
        assert p.family == "Vanguard"
        assert p.expense_ratio == pytest.approx(0.0003, abs=1e-6)
        assert p.total_assets == pytest.approx(1_756_880_437_248)
        assert p.inception_date == "2010-09-07"
        assert len(p.holdings) == 10
        assert p.holdings[0].symbol == "NVDA"

    def test_채권_ETF_는_보유종목이_비어도_읽힌다(self) -> None:
        p = profile("BND")
        assert p.category == "Intermediate Core Bond"
        assert p.holdings == []

    def test_결과가_없으면_None(self) -> None:
        assert yahoo_fund.parse_quote_summary("NOPE", FIXTURE["NOPE"]) is None

    def test_없는_값은_0_이_아니라_None(self) -> None:
        # 0% 보수와 "보수를 모른다" 는 다른 말이다
        p = yahoo_fund.parse_quote_summary("X", {"quoteSummary": {"result": [{"fundProfile": {}}]}})
        assert p is not None
        assert p.expense_ratio is None
        assert p.total_assets is None
        assert p.turnover_est is None

    def test_비중을_모르는_보유는_0_으로_채우지_않고_뺀다(self) -> None:
        """"추천 종목 NVDA 0.00%" 처럼 모르는 것을 0 으로 보였다 (docs/infra.md 25.391)."""
        payload = {"quoteSummary": {"result": [{"fundProfile": {}, "topHoldings": {"holdings": [
            {"symbol": "AAPL", "holdingName": "Apple", "holdingPercent": {"raw": 0.07}},
            {"symbol": "NVDA", "holdingName": "NVIDIA", "holdingPercent": {}},
        ]}}]}}
        p = yahoo_fund.parse_quote_summary("X", payload)
        assert p is not None
        assert [(h.symbol, h.pct) for h in p.holdings] == [("AAPL", 0.07)]


class Test필수조건:
    def test_넓은_지수_대형_ETF_는_통과한다(self) -> None:
        ev = evaluate("VOO", "Vanguard S&P 500 ETF")
        assert ev.passed, ev.excluded_reason
        assert ev.bucket == "미국 주식"
        assert all(row["passed"] for row in ev.criteria)

    def test_채권_ETF_는_채권_묶음이다(self) -> None:
        ev = evaluate("BND", "Vanguard Total Bond Market ETF")
        assert ev.passed, ev.excluded_reason
        assert ev.bucket == "채권"

    def test_레버리지는_분류로_뺀다(self) -> None:
        ev = evaluate("TQQQ", "ProShares Trust")  # 이름에 단서가 없어도 분류로 걸린다
        assert not ev.passed
        assert "레버리지" in (ev.excluded_reason or "")

    def test_업종_ETF_는_좁은_지수로_뺀다(self) -> None:
        ev = evaluate("XLK", "Technology Select Sector SPDR Fund")
        assert not ev.passed
        assert "Technology" in (ev.excluded_reason or "")

    def test_대형_성장주는_넓은_지수로_본다(self) -> None:
        # 2026-09-17 사용자 결정. 국내의 NASDAQ 100 과 기준을 맞췄다
        ev = evaluate("QQQ", "Invesco QQQ Trust, Series 1")
        assert ev.passed, ev.excluded_reason
        assert ev.bucket == "미국 주식"

    def test_대형_가치주도_넓은_지수로_본다(self) -> None:
        # 2026-09-17 사용자 결정. 성장과 가치를 같게 본다
        p = profile("QQQ")
        p.category = "Large Value"
        ev = svc.evaluate("SCHD", "Schwab US Dividend Equity ETF", p, AS_OF, FETCHED)
        assert ev.passed, ev.excluded_reason

    def test_중형_가치는_여전히_뺀다(self) -> None:
        p = profile("QQQ")
        p.category = "Mid-Cap Value"
        ev = svc.evaluate("X", "x", p, AS_OF, FETCHED)
        assert not ev.passed

    def test_운용_3년_미만은_뺀다(self) -> None:
        p = profile("VOO")
        p.inception_date = "2025-01-02"
        ev = svc.evaluate("VOO", "x", p, AS_OF, FETCHED)
        assert not ev.passed
        assert "3년" in (ev.excluded_reason or "")

    def test_설정일을_못_읽으면_미만이_아니라_확인_불가(self) -> None:
        """모르는 값을 "3년 미만" 으로 적었다 (docs/infra.md 25.759, ETF 감사)."""
        p = profile("VOO")
        p.inception_date = "알수없음"
        ev = svc.evaluate("VOO", "x", p, AS_OF, FETCHED)
        assert not ev.passed and "확인할 수 없는 값: 설정일" in (ev.excluded_reason or "")

    def test_순자산_10억달러_미만은_뺀다(self) -> None:
        p = profile("VOO")
        p.total_assets = 999_000_000
        ev = svc.evaluate("VOO", "x", p, AS_OF, FETCHED)
        assert not ev.passed
        assert "순자산" in (ev.excluded_reason or "")

    def test_보수를_모르면_추천하지_않는다(self) -> None:
        p = profile("VOO")
        p.expense_ratio = None
        ev = svc.evaluate("VOO", "x", p, AS_OF, FETCHED)
        assert not ev.passed
        assert "총보수" in (ev.excluded_reason or "")

    def test_거래대금이_작으면_뺀다(self) -> None:
        p = profile("VOO")
        p.avg_volume = 10
        ev = svc.evaluate("VOO", "x", p, AS_OF, FETCHED)
        assert not ev.passed
        assert "거래대금" in (ev.excluded_reason or "")

    def test_프로필이_없으면_뺀다(self) -> None:
        ev = svc.evaluate("VOO", "x", None, AS_OF, FETCHED)
        assert not ev.passed


class Test이름으로_레버리지_찾기:
    @pytest.mark.parametrize(
        "name",
        [
            "Direxion Daily S&P 500 Bull 3X Shares",
            "ProShares UltraPro QQQ",
            "ProShares UltraShort S&P500",
            "Tradr 2X Long SPY",
            "AXS 1.25X NVDA Bear Daily ETF",
            "GraniteShares -1x Short TSLA",
            "MicroSectors Leveraged ETN",
        ],
    )
    def test_레버리지_인버스_이름(self, name: str) -> None:
        assert svc.name_looks_leveraged(name)

    @pytest.mark.parametrize(
        "name",
        [
            "Vanguard S&P 500 ETF",
            "iShares Core MSCI Total International Stock ETF",
            "Bearing Point Growth Fund",  # 단어 일부에 bear 가 들어 있을 뿐이다
            "SPDR Portfolio S&P 500 ETF",
        ],
    )
    def test_일반_이름은_걸리지_않는다(self, name: str) -> None:
        assert not svc.name_looks_leveraged(name)


class Test점수:
    def test_백분위는_좋은_것이_100_나쁜_것이_0(self) -> None:
        assert svc.percentile_scores([0.1, 0.3, 0.2], higher_is_better=False) == [100.0, 0.0, 50.0]

    def test_동률은_평균_순위(self) -> None:
        assert svc.percentile_scores([1.0, 1.0, 3.0], higher_is_better=True) == [25.0, 25.0, 100.0]

    def test_하나뿐이면_100(self) -> None:
        assert svc.percentile_scores([5.0], higher_is_better=True) == [100.0]

    def test_빠진_기준의_가중치는_나머지에_비례해_나눈다(self) -> None:
        weights = svc.renormalized_weights()
        assert weights["expense"] == pytest.approx(2 / 3)
        assert weights["assets"] == pytest.approx(1 / 3)

    def test_같은_분류_안에서만_순위를_낸다(self) -> None:
        # VOO(0.03%, 1.76T) 와 SPLG(0.02%, 97B) 는 같은 Large Blend, BND 는 채권이라 따로다
        evs = [evaluate("VOO"), evaluate("SPLG"), evaluate("BND")]
        svc.score_within_categories(evs)
        voo, splg, bnd = evs

        # SPLG: 보수 1등(100×2/3) + 규모 꼴찌(0) = 66.7 / VOO: 0 + 100×1/3 = 33.3
        assert splg.score == pytest.approx(66.7)
        assert voo.score == pytest.approx(33.3)
        assert splg.rank_in_category == 1
        assert voo.category_size == 2

        assert bnd.score == pytest.approx(100.0)
        assert bnd.rank_in_category == 1
        assert bnd.category_size == 1

    def test_점수를_매기면_총보수_행이_근거표에_붙는다(self) -> None:
        evs = [evaluate("VOO"), evaluate("SPLG")]
        svc.score_within_categories(evs)
        labels = [row["label"] for row in evs[1].criteria]
        assert "총보수" in labels
        assert "1번째로 낮음" in evs[1].criteria[-1]["display"]

    def test_근거_문장에_실제_수치가_들어간다(self) -> None:
        evs = [evaluate("VOO"), evaluate("SPLG")]
        svc.score_within_categories(evs)
        text = svc.rationale_text(evs[1])
        assert "0.02%" in text
        assert "1위" in text


class Test근거문장:
    def test_혼자면_순위를_말하지_않는다(self) -> None:
        evs = [evaluate("BND")]
        svc.score_within_categories(evs)
        text = svc.rationale_text(evs[0])
        assert "유일한" in text
        assert "1위" not in text


class Test추천종목_겹침:
    def test_추천이_없으면_모른다로_남긴다(self) -> None:
        result = svc.overlap(profile("VOO"), {}, None)
        assert result["available"] is False
        assert "total_pct" not in result  # 0% 로 적지 않는다

    def test_상위_보유종목_중_추천된_것의_비중을_더한다(self) -> None:
        recommended = svc.normalize_recommended({"NVDA": "short", "AAPL": "long", "XYZ": "mid"})
        result = svc.overlap(profile("VOO"), recommended, "2026-09-16")
        assert result["available"] is True
        symbols = {m["symbol"] for m in result["matched"]}
        assert symbols == {"NVDA", "AAPL"}
        assert result["total_pct"] == pytest.approx(sum(m["pct"] for m in result["matched"]))
        assert result["basis"] == "상위 10개 보유종목"

    def test_점과_하이픈_표기를_같게_본다(self) -> None:
        p = profile("VOO")
        p.holdings = [yahoo_fund.Holding("BRK.B", "Berkshire", 0.02)]
        result = svc.overlap(p, svc.normalize_recommended({"BRK-B": "long"}), "2026-09-16")
        assert len(result["matched"]) == 1

    def test_보유종목이_없으면_모른다(self) -> None:
        result = svc.overlap(profile("BND"), svc.normalize_recommended({"NVDA": "short"}), "2026-09-16")
        assert result["available"] is False


class Test배치_도우미:
    def test_평균_거래대금은_최근_20일만_본다(self) -> None:
        bars = [DailyBar("AAA", f"2026-08-{d:02d}", close=10.0, volume=100) for d in range(1, 31)]
        bars[0] = DailyBar("AAA", "2026-08-01", close=10.0, volume=1_000_000)  # 오래된 날의 큰 값
        turnover = job.avg_turnover_by_symbol(bars)
        assert turnover["AAA"] == pytest.approx(1000.0)

    def test_문턱_이상만_큰_순으로_자른다(self) -> None:
        picked = job.select_for_profile({"A": 1e6, "B": 9e6, "C": 6e6, "D": 7e6}, minimum=5e6, limit=2)
        assert picked == ["B", "D"]

    def test_행과_열_개수가_맞는다(self) -> None:
        from batch.core import db

        p = profile("VOO")
        ev = evaluate("VOO")
        assert len(job.profile_row(1, "2026-09-17", p, FETCHED)) == db.column_count(job._PROFILE_COLS)
        assert len(job.pick_row(1, "2026-09-17", ev, {}, FETCHED)) == db.column_count(job._PICK_COLS)


class Test실제_스키마:
    """마이그레이션을 적용한 SQLite 에 행이 실제로 들어가는지."""

    def test_프로필과_판정을_저장할_수_있다(self) -> None:
        conn = sqlite3.connect(":memory:")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))

        conn.execute(
            f"INSERT INTO etfs ({job._ETF_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("VOO", "US", "Vanguard S&P 500 ETF", "NYSE Arca", "VOO", "active", "test", FETCHED),
        )
        p = profile("VOO")
        evs = [svc.evaluate("VOO", "Vanguard S&P 500 ETF", p, AS_OF, FETCHED)]
        svc.score_within_categories(evs)

        row = job.profile_row(1, "2026-09-17", p, FETCHED)
        conn.execute(f"INSERT INTO etf_profiles ({job._PROFILE_COLS}) VALUES ({', '.join(['?'] * len(row))})", row)
        pick = job.pick_row(1, "2026-09-17", evs[0], svc.overlap(p, {}, None), FETCHED)
        conn.execute(f"INSERT INTO etf_picks ({job._PICK_COLS}) VALUES ({', '.join(['?'] * len(pick))})", pick)

        saved = conn.execute("SELECT passed, bucket, score, rationale_data FROM etf_picks").fetchone()
        assert saved[0] == 1
        assert saved[1] == "미국 주식"
        assert saved[2] == pytest.approx(100.0)
        assert json.loads(saved[3])["criteria"]
        # 계좌별 칸 (25.966) — 미국 상장은 일반계좌만
        accounts = json.loads(saved[3])["accounts"]
        assert accounts["pension"]["eligible"] is False and accounts["taxable"]["priority"] == 2


# ----------------------------------------------------------------------
# 국내 (etf.md 9장)
# ----------------------------------------------------------------------


def kr_row(code: str, name: str, index: str, day: str = "20260916", **kw) -> krx.KrxEtfRow:
    raw = {
        "BAS_DD": day,
        "ISU_CD": code,
        "ISU_NM": name,
        "TDD_CLSPRC": "10000",
        "NAV": "10010",
        "ACC_TRDVAL": "5000000000",
        "INVSTASST_NETASST_TOTAMT": "500000000000",
        "IDX_IND_NM": index,
        "OBJ_STKPRC_IDX": "1000",
    }
    raw.update(kw)
    return krx.KrxEtfRow.from_raw(raw)


def kr_input(**kw) -> svc.KrEtfInput:
    base = {
        "symbol": "069500",
        "name": "KODEX 200",
        "index_name": "코스피 200",
        "net_assets": 24_715_731_480_706.0,
        "avg_turnover": 2_410_735_400_342.0,
        "premium_abs_avg": 0.0004,
        "days_observed": 20,
        "listed_3y_ago": True,
    }
    base.update(kw)
    return svc.KrEtfInput(**base)


class Test한국거래소_ETF_행:
    def test_실제_응답_필드를_읽는다(self) -> None:
        # 2026-09-16 KODEX 200 실제 응답 값
        row = kr_row("069500", "KODEX 200", "코스피 200", INVSTASST_NETASST_TOTAMT="24715731480706")
        assert row.net_assets == 24_715_731_480_706
        assert row.index_name == "코스피 200"
        assert row.premium == pytest.approx((10000 - 10010) / 10010)

    def test_NAV_가_없으면_괴리율도_없다(self) -> None:
        assert kr_row("1", "x", "코스피 200", NAV="").premium is None
        # 종가 0 인 날(거래정지·무거래)은 괴리율 −100% 가 아니라 값 없음 (25.713)
        assert kr_row("1", "x", "코스피 200", TDD_CLSPRC="0").premium is None


class Test국내_필수조건:
    def test_대표_ETF_는_통과한다(self) -> None:
        ev = svc.evaluate_kr(kr_input(), FETCHED)
        assert ev.passed, ev.excluded_reason
        assert ev.bucket == "국내 주식"
        assert ev.country == "KR"

    @pytest.mark.parametrize(
        "name",
        ["KODEX 레버리지", "KODEX 인버스", "KODEX 200선물인버스2X", "TIGER 미국S&P500(합성)",
         "KODEX 200타겟위클리커버드콜",
         # 소문자·다른 배수·영문 표기 (docs/infra.md 25.633)
         "KODEX 200선물인버스2x", "ACE 미국S&P500 1.5X", "HANARO 200 Leveraged", "SOL 코스피200 Inverse"],
    )
    def test_이름으로_거른다(self, name: str) -> None:
        # 기초지수가 코스피 200 이어도 이름으로 빠져야 한다
        ev = svc.evaluate_kr(kr_input(name=name), FETCHED)
        assert not ev.passed
        assert ev.criteria[0]["label"] == "상품 유형"

    @pytest.mark.parametrize("name", ["KODEX 200", "TIGER 미국S&P500", "ACE 미국나스닥100", "RISE 200TR", "KODEX 미국S&P500TR"])
    def test_보통_이름은_배수로_잘못_잡지_않는다(self, name: str) -> None:
        assert svc.evaluate_kr(kr_input(name=name), FETCHED).passed

    def test_액티브는_빼지_않고_표시한다(self) -> None:
        # 2026-09-17 사용자 결정. 국내 종합채권 ETF 가 전부 액티브라 채권이 0개가 됐었다
        ev = svc.evaluate_kr(
            kr_input(name="KODEX 종합채권(AA-이상)액티브", index_name="KAP 한국종합채권지수"), FETCHED
        )
        assert ev.passed, ev.excluded_reason
        assert ev.bucket == "채권"
        assert "액티브" in ev.criteria[0]["display"]

    def test_좁은_지수는_뺀다(self) -> None:
        ev = svc.evaluate_kr(kr_input(name="KODEX 반도체", index_name="KRX 반도체"), FETCHED)
        assert not ev.passed
        assert "KRX 반도체" in (ev.excluded_reason or "")

    def test_나스닥100과_코스닥150은_넓은_지수다(self) -> None:
        # 2026-09-17 사용자 결정
        assert svc.evaluate_kr(kr_input(name="TIGER 미국나스닥100", index_name="NASDAQ 100"), FETCHED).passed
        assert svc.evaluate_kr(kr_input(name="KODEX 코스닥150", index_name="코스닥 150"), FETCHED).passed

    def test_3년_전에_없었으면_뺀다(self) -> None:
        ev = svc.evaluate_kr(kr_input(listed_3y_ago=False), FETCHED)
        assert not ev.passed
        assert "3년" in (ev.excluded_reason or "")

    def test_순자산_1000억_미만은_뺀다(self) -> None:
        ev = svc.evaluate_kr(kr_input(net_assets=99_000_000_000.0), FETCHED)
        assert not ev.passed
        assert "순자산" in (ev.excluded_reason or "")

    def test_관측일이_모자라면_거래대금을_판정하지_않는다(self) -> None:
        ev = svc.evaluate_kr(kr_input(days_observed=5), FETCHED)
        assert not ev.passed
        assert "관측 5일" in (ev.excluded_reason or "")

    def test_괴리율을_모르면_추천하지_않는다(self) -> None:
        ev = svc.evaluate_kr(kr_input(premium_abs_avg=None), FETCHED)
        assert not ev.passed


class Test국내_점수:
    def test_같은_기초지수_안에서_순자산_2_3_괴리율_1_3(self) -> None:
        big = svc.evaluate_kr(kr_input(symbol="A", net_assets=2e13, premium_abs_avg=0.0010), FETCHED)
        small = svc.evaluate_kr(kr_input(symbol="B", net_assets=1e12, premium_abs_avg=0.0002), FETCHED)
        other = svc.evaluate_kr(kr_input(symbol="C", index_name="S&P 500"), FETCHED)
        svc.score_within_categories([big, small, other])

        assert big.score == pytest.approx(66.7)  # 순자산 1등(100×2/3) + 괴리율 꼴찌(0)
        assert small.score == pytest.approx(33.3)
        assert big.rank_in_category == 1
        assert other.category_size == 1  # 기초지수가 다르면 따로 센다

    def test_근거_문장이_보수_미반영을_숨기지_않는다(self) -> None:
        evs = [svc.evaluate_kr(kr_input(), FETCHED)]
        svc.score_within_categories(evs)
        text = svc.rationale_text(evs[0])
        assert "보수" in text and "반영하지 않음" in text
        assert "조원" in text

    def test_국내는_겹침을_모른다고_남긴다(self) -> None:
        assert svc.overlap_kr()["available"] is False

    def test_미국과_국내가_섞여도_나라별로_따로_센다(self) -> None:
        us = evaluate("VOO")
        kr = svc.evaluate_kr(kr_input(), FETCHED)
        svc.score_within_categories([us, kr])
        assert us.category_size == 1 and kr.category_size == 1


class Test국내_모으기:
    def test_평균은_있는_날만으로_내고_최근_값을_쓴다(self) -> None:
        day1 = [kr_row("A", "옛 이름", "코스피 200", "20260915", ACC_TRDVAL="1000")]
        day2 = [
            kr_row("A", "새 이름", "코스피 200", "20260916", ACC_TRDVAL="3000", INVSTASST_NETASST_TOTAMT="777"),
            kr_row("B", "신규", "코스피 200", "20260916", ACC_TRDVAL="10"),
        ]
        inputs = {i.symbol: i for i in job.aggregate_kr([day1, day2], old_codes={"A"})}

        assert inputs["A"].name == "새 이름"
        assert inputs["A"].net_assets == 777
        assert inputs["A"].avg_turnover == pytest.approx(2000)
        assert inputs["A"].days_observed == 2
        assert inputs["A"].listed_3y_ago is True
        assert inputs["B"].days_observed == 1  # 없던 날을 0 으로 채우지 않는다
        assert inputs["B"].listed_3y_ago is False

    def test_거래일_20개와_3년_전_거래일(self) -> None:
        recent, three = job.kr_sessions(date(2026, 9, 16))
        assert len(recent) == 20
        assert recent[-1] == date(2026, 9, 16)
        assert recent == sorted(recent)
        assert date(2023, 9, 1) <= three <= date(2023, 9, 16)

    def test_국내_프로필_행과_열_개수가_맞는다(self) -> None:
        from batch.core import db

        row = job.kr_profile_row(1, "2026-09-16", kr_input(), 10000.0, FETCHED)
        assert len(row) == db.column_count(job._PROFILE_COLS_KR)


class Test재판정:
    def test_저장된_행으로_같은_판정을_재현한다(self) -> None:
        p = profile("VOO")
        stored = dict(zip(
            [c.strip() for c in job._PROFILE_COLS.split(",")],
            job.profile_row(1, "2026-09-17", p, FETCHED),
            strict=True,
        ))
        stored["yahoo_symbol"] = "VOO"
        restored = job.profile_from_row(stored)

        original = svc.evaluate("VOO", "Vanguard S&P 500 ETF", p, AS_OF, FETCHED)
        again = svc.evaluate("VOO", "Vanguard S&P 500 ETF", restored, AS_OF, FETCHED)
        assert again.passed == original.passed
        assert again.metrics == original.metrics
        assert [h.symbol for h in restored.holdings] == [h.symbol for h in p.holdings]


# ----------------------------------------------------------------------
# 국내 ETF 판정이 **받기 전에 기록을 연다** (2026-09-22, docs/infra.md 25.114)
# ----------------------------------------------------------------------


class Test받다가_죽어도_기록이_남는다:
    """월 1회 작업이 아무 기록도 없이 죽으면 **한 달 뒤에나 안다.**

    25.95 에서 "한 날도 못 받은" 경우만 기록을 남기게 고쳤는데, 받는 도중에 **예외로**
    죽으면(응답 모양이 바뀌거나 D1 한도에 걸리거나) 여전히 행이 하나도 안 남았다.
    그러면 `/status` 와 무응답 감시가 "예약이 안 불렸다" 와 똑같이 본다.
    미국 경로(`run_us`)는 처음부터 받기 전에 열고 있었다 — 국내만 안 그랬다.
    """

    @staticmethod
    def _client(monkeypatch):
        import sqlite3
        from typing import Any

        from batch.core import db as core_db
        from batch.core.turso import ResultSet
        from batch.jobs import etf as job

        class Mem:
            def __init__(self) -> None:
                self.conn = sqlite3.connect(":memory:")

            def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
                cur = self.conn.execute(sql, args or [])
                cols = [d[0] for d in cur.description or []]
                return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

            def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
                return [self.execute(sql, args) for sql, args in statements]

            def close(self) -> None:
                pass

        mem = Mem()
        core_db.apply_migrations(mem)  # type: ignore[arg-type]
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        core_db._열린_실행.clear()
        return mem

    @staticmethod
    def _기록(mem) -> list[tuple]:
        return mem.conn.execute(
            "SELECT job_name, market, trade_date, status FROM batch_runs ORDER BY id"
        ).fetchall()

    def test_받는_중_예외에도_행이_남고_guard_가_실패로_닫는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from batch.core.entry import guard
        from batch.jobs import etf as job
        from batch.sources import krx

        mem = self._client(monkeypatch)

        def 터진다(bas_dd: str):
            raise RuntimeError("KRX 응답 모양이 바뀌었다")

        monkeypatch.setattr(krx, "fetch_etf_daily", 터진다)

        with pytest.raises(RuntimeError):
            guard(lambda: job.run_kr("2026-09-18"))

        기록 = self._기록(mem)
        assert len(기록) == 1, "행이 하나도 안 남으면 '예약이 안 불렸다' 와 구별이 안 된다"
        assert 기록[0][:2] == ("etf", "KR")
        assert 기록[0][3] == "failed"

    def test_한_날도_못_받으면_실패로_남는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """25.95 가 고친 경로. 기록을 위에서 열게 바꾼 뒤에도 그대로여야 한다."""
        from batch.jobs import etf as job
        from batch.sources import krx
        from batch.sources.yfinance_src import FetchResult

        mem = self._client(monkeypatch)
        monkeypatch.setattr(
            krx, "fetch_etf_daily", lambda bas_dd: FetchResult(ok=False, source="krx_openapi", error="HTTP 500")
        )

        assert job.run_kr("2026-09-18") == 1

        기록 = self._기록(mem)
        assert len(기록) == 1
        assert 기록[0][3] == "failed"
        assert 기록[0][2] == "2026-09-18", "겨눈 날이 남아야 언제 것을 못 받았는지 안다"


class Test시험_실행은_저장하지_않는다:
    """`--only` 로 몇 종목만 판정해 저장하면 웹의 미국 탭 전체가 그 부분집합으로 바뀐다 (docs/infra.md 25.352)."""

    def test_저장하지_않으면_판정_표가_그대로다(self, monkeypatch) -> None:
        from batch.jobs import etf as job

        mem = Test받다가_죽어도_기록이_남는다._client(monkeypatch)
        from batch.core import db as core_db

        run_id = core_db.start_batch_run(mem, job_name="etf", market="US", trade_date="2026-09-27")  # type: ignore[arg-type]
        notes: list[str] = []
        job._store_and_report(mem, run_id, "US", "2026-09-27", [], [("x",)], notes, {}, True, store=False)  # type: ignore[arg-type]
        assert mem.conn.execute("SELECT COUNT(*) FROM etf_picks").fetchone()[0] == 0
        assert any("저장하지 않았습니다" in n for n in notes)

    def test_미국_경로가_only_면_저장을_끈다(self) -> None:
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "etf.py").read_text(encoding="utf-8")
        assert "store=not only and not 부분판" in src
        assert "if not only and not 부분판:\n            _bulk(client, \"etf_profiles\"" in src

    def test_입력이_모자란_판정은_저장하지_않는다(self) -> None:
        # 국내 3년 전 목록 없음 → 모두 "운용 3년 미만", 미국 야후 차단 → 받은 일부로 순위. 한 달 동안 탭을 덮었다
        # (25.580)
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "etf.py").read_text(encoding="utf-8")
        assert "믿을_판 = bool(old_codes) and len(days) >= svc.MIN_KR_DAYS" in src
        assert "store=믿을_판" in src
        # 프로필도 — 위성 판정이 가장 새 프로필을 읽는다 (25.583)
        assert "if 믿을_판:\n            _bulk(client, \"etf_profiles\", _PROFILE_COLS_KR" in src

    def test_이유가_있으면_시험_실행_문구를_붙이지_않는다(self, monkeypatch) -> None:
        from batch.core import db as core_db
        from batch.jobs import etf as job

        mem = Test받다가_죽어도_기록이_남는다._client(monkeypatch)
        run_id = core_db.start_batch_run(mem, job_name="etf", market="KR", trade_date="2026-09-27")  # type: ignore[arg-type]
        notes = ["3년 전 목록이 없어 이번 판정을 저장하지 않았습니다 — 화면은 지난 판정 그대로입니다"]
        job._store_and_report(mem, run_id, "KR", "2026-09-27", [], [("x",)], notes, {}, True, store=False)  # type: ignore[arg-type]
        assert not any("--only" in n for n in notes)
        assert mem.conn.execute("SELECT COUNT(*) FROM etf_picks").fetchone()[0] == 0


class Test감사_25_544:
    """ETF 감사 확정 셋 (docs/infra.md 25.544)."""

    def test_버퍼·옵션_전략은_넓은_지수여도_뺀다(self) -> None:
        for 이름 in ("KODEX 미국S&P500버퍼3월액티브", "TIGER 미국S&P500데일리옵션액티브"):
            평가 = svc.evaluate_kr(kr_input(name=이름, index_name="S&P 500"), "t")
            assert not 평가.passed, 이름
            assert "버퍼·옵션" in str(평가.criteria[0]), 평가.criteria[0]  # 근거표 기준 칸이 뺀 이유를 담는다 (25.551)

    def test_영문_채권·원자재_지수는_테마가_아니다(self) -> None:
        from batch.services import etf_satellite as sat

        for 지수 in ("Bloomberg US Corporate Bond Index", "ICE BofA US High Yield Index",
                    "Markit iBoxx USD Liquid Investment Grade", "S&P GSCI Silver Index(TR)", "Bloomberg Copper Subindex"):  # fmt: skip
            assert sat.assign_kr(지수) is None, 지수
        assert sat.assign_kr("Pacer US Cash Cows 100 Index") is not None  # 주식형은 그대로
        # 채굴·생산 기업·고령화 지수는 주식형 (25.548, 교차검증)
        for 지수 in ("Solactive Global Silver Miners Index", "Solactive Global Copper Miners Index", "KRX 구리채굴 지수",
                    "S&P Commodity Producers Index", "Indxx Global Aging Silver Economy Index",
                    "Solactive Global Silver Mining Index", "NYSE Arca Gold Miners Index"):  # fmt: skip
            assert sat.assign_kr(지수) is not None, 지수

    def test_가장_최근_날에_없는_ETF는_뺀다(self) -> None:
        from batch.jobs import etf as job

        앞날 = [[kr_row("111111", "사라진 200", "코스피 200", day=f"202609{d:02d}"),
                 kr_row("069500", "KODEX 200", "코스피 200", day=f"202609{d:02d}")] for d in range(1, 17)]  # fmt: skip
        뒷날 = [[kr_row("069500", "KODEX 200", "코스피 200", day=f"202609{d:02d}")] for d in range(17, 21)]
        모은것 = job.aggregate_kr(앞날 + 뒷날, set())
        assert [i.symbol for i in 모은것] == ["069500"]


class Test부분판은_저장하지_않는다:
    """일봉이 막혔거나 프로필 수를 줄인 시험 실행도 부분집합이다 (docs/infra.md 25.608, 감사)."""

    @staticmethod
    def _돌림(monkeypatch, *, 일봉상태: str = "ok", max_profiles: int | None = None) -> tuple[list[str], dict]:
        import types

        from batch.jobs import etf as job
        from batch.sources.yfinance_src import FetchResult

        Test받다가_죽어도_기록이_남는다._client(monkeypatch)
        종목 = types.SimpleNamespace(yahoo_symbol="AAA", name="AAA ETF", is_etf=True, is_test=False)
        monkeypatch.setattr(job.nasdaq_symbols, "fetch_symbols", lambda: FetchResult(ok=True, data=[종목]))
        monkeypatch.setattr(job, "store_master", lambda c, etfs, now: {"AAA": 1})
        monkeypatch.setattr(job, "fetch_daily_bars",
                            lambda *a, **k: FetchResult(ok=True, data=[], limit_state=일봉상태, error=""))  # fmt: skip
        monkeypatch.setattr(job, "avg_turnover_by_symbol", lambda bars: {"AAA": 1e9})
        monkeypatch.setattr(job, "select_for_profile", lambda t, limit: ["AAA"] * min(limit, 10))  # 문턱 넘은 것 10개
        프로필_부름: list[int] = []
        monkeypatch.setattr(job.yahoo_fund, "fetch_profiles",
                            lambda targets: 프로필_부름.append(1) or ({"AAA": object()}, [], False))  # fmt: skip
        monkeypatch.setattr(job, "profile_row", lambda *a: ("row",))
        monkeypatch.setattr(job.svc, "evaluate", lambda *a: object())
        monkeypatch.setattr(job, "_judge_us", lambda *a: [])
        저장한표: list[str] = []
        monkeypatch.setattr(job, "_bulk", lambda c, table, *a, **k: 저장한표.append(table))
        받은: dict = {}

        def 보고(*a, **k):
            받은.update(k, notes=a[6])
            return 0

        monkeypatch.setattr(job, "_store_and_report", 보고)
        job.run_us(max_profiles or job.MAX_PROFILES, as_of="2026-09-27")
        job.db._열린_실행.clear()  # 기록을 닫는 `_store_and_report` 를 가짜로 바꿨다
        받은["프로필_부름"] = len(프로필_부름)
        return 저장한표, 받은

    def test_평소에는_저장한다(self, monkeypatch) -> None:
        표, 받은 = self._돌림(monkeypatch)
        assert "etf_profiles" in 표 and 받은["store"] is True

    def test_일봉이_처음부터_전부_막히면_실패다(self, monkeypatch) -> None:
        """25.611 의 조기 종료가 이 달을 partial·종료 0 으로 바꿨다 (docs/infra.md 25.614, 교차검증)."""
        from batch.jobs import etf as job

        monkeypatch.setattr(job, "avg_turnover_by_symbol", lambda bars: {})
        mem = Test받다가_죽어도_기록이_남는다._client(monkeypatch)
        import types

        from batch.sources.yfinance_src import FetchResult

        종목 = types.SimpleNamespace(yahoo_symbol="AAA", name="AAA ETF", is_etf=True, is_test=False)
        monkeypatch.setattr(job.nasdaq_symbols, "fetch_symbols", lambda: FetchResult(ok=True, data=[종목]))
        monkeypatch.setattr(job, "store_master", lambda c, etfs, now: {"AAA": 1})
        monkeypatch.setattr(job, "fetch_daily_bars",
                            lambda *a, **k: FetchResult(ok=False, data=[], limit_state="blocked", error="막힘"))  # fmt: skip
        assert job.run_us(as_of="2026-09-27") == 1
        assert mem.conn.execute("SELECT status FROM batch_runs WHERE job_name = 'etf'").fetchone()[0] == "failed"

    def test_일봉이_막히면_프로필도_판정도_저장하지_않는다(self, monkeypatch) -> None:
        표, 받은 = self._돌림(monkeypatch, 일봉상태="blocked")
        assert "etf_profiles" not in 표 and 받은["store"] is False
        assert "저장하지 않았습니다" in 받은["notes"][0]
        assert 받은["프로필_부름"] == 0, "버릴 프로필을 부르지 않는다 (25.611)"

    def test_줄인_입력이_아무것도_자르지_않으면_저장한다(self, monkeypatch) -> None:
        표, 받은 = self._돌림(monkeypatch, max_profiles=50)  # 문턱 넘은 것이 10개뿐
        assert "etf_profiles" in 표 and 받은["store"] is True

    def test_프로필_수를_줄인_시험도_저장하지_않는다(self, monkeypatch) -> None:
        표, 받은 = self._돌림(monkeypatch, max_profiles=10)
        assert "etf_profiles" not in 표 and 받은["store"] is False
        assert "10개로 줄인" in 받은["notes"][0]


class Test위성_기록:
    """위성 판정이 기록 없이 넘어가거나, 근거표를 못 만든 것을 뺀 날도 success 였다 (docs/infra.md 25.608, 감사)."""

    @staticmethod
    def _돌림(monkeypatch, 프로필: dict) -> list[tuple]:
        from batch.jobs import etf_satellite as sj

        mem = Test받다가_죽어도_기록이_남는다._client(monkeypatch)
        monkeypatch.setattr(sj, "TursoClient", lambda: mem)
        monkeypatch.setattr(sj, "_latest_profiles", lambda c, country: 프로필.get(country, (None, [])))
        monkeypatch.setattr(sj, "_bulk", lambda *a, **k: None)
        monkeypatch.setattr(sj.sat, "evaluate_us", lambda *a: None)
        monkeypatch.setattr(sj.sat, "score", lambda evs: ["BAD"])
        monkeypatch.setattr(sj, "profile_from_row", lambda row: None)
        sj.run()
        return mem.conn.execute(
            "SELECT market, status, error_text FROM batch_runs WHERE job_name = 'etf_satellite' ORDER BY market"
        ).fetchall()

    def test_프로필이_없어도_건너뜀_기록을_남긴다(self, monkeypatch) -> None:
        기록 = self._돌림(monkeypatch, {})
        assert [(m, s) for m, s, _e in 기록] == [("KR", "skipped"), ("US", "skipped")]

    def test_근거표를_못_만든_것이_있으면_partial(self, monkeypatch) -> None:
        기록 = self._돌림(monkeypatch, {"US": ("2026-09-27", [{"yahoo_symbol": "A", "etf_name": "A", "etf_id": 1}])})
        us = [r for r in 기록 if r[0] == "US"][0]
        assert us[1] == "partial" and "근거표" in us[2]


def test_날짜가_아닌_상장일은_None_이다() -> None:
    """"20231399" 가 그대로 저장돼 멀쩡한 상장일을 덮었다 (docs/infra.md 25.713, 감사)."""
    행 = krx.KrxMasterRow.from_raw({"ISU_SRT_CD": "005930", "LIST_DD": "20231399"})
    assert 행.listed_date_iso is None
    assert krx.KrxMasterRow.from_raw({"ISU_SRT_CD": "005930", "LIST_DD": "19750611"}).listed_date_iso == "1975-06-11"


def test_괴리율_근거는_실제로_평균_낸_날_수를_적는다() -> None:
    """20일 중 NAV 가 있는 10일 평균을 "20일 평균" 이라 적었다 (docs/infra.md 25.714, 감사 재현)."""
    ev = svc.evaluate_kr(kr_input(days_observed=20, premium_days=10), FETCHED)
    행 = [c for c in ev.criteria if c["label"] == "괴리율"][0]
    assert "10일 평균" in str(행)


def test_미국_이름으로_뺀_ETF_를_실행_기록에_남긴다() -> None:
    """이름으로 뺀 ETF 는 판정·저장이 없어 "왜 없나" 에 답이 없었다 (docs/infra.md 25.718)."""
    import inspect

    from batch.jobs import etf as etf_job

    assert '"name_excluded": sorted(s.yahoo_symbol for s in etfs if svc.name_looks_leveraged(s.name)),' in inspect.getsource(
        etf_job
    )


class Test25_758:
    """보수 부동소수 동률, 같은 기준일 재실행의 옛 판정 (docs/infra.md 25.758, ETF 감사)."""

    def test_보수는_여섯째_자리로_반올림해_동률(self) -> None:
        from batch.services import etf as svc

        a = svc.percentile_scores([round(0.00029999999, 4), round(0.00031, 4), 0.0003], higher_is_better=False)
        assert a[0] == a[1] == a[2]  # 화면엔 셋 다 0.03% (25.760)
        import inspect

        assert 'ev.metrics["expense"] = float(fmt_pct(ev.metrics["expense"])[:-1]) / 100' in inspect.getsource(
            svc.score_within_categories
        )
        # 반 bp: 표시 "0.07%" 둘은 같은 순위, 표시가 다르면 다른 순위 (25.762)
        보인값 = [float(svc.fmt_pct(x)[:-1]) / 100 for x in (0.00065, 0.0007, 0.0006)]
        assert svc.fmt_pct(0.00065) == svc.fmt_pct(0.0007) and 보인값[0] == 보인값[1] != 보인값[2]

    def test_온전한_실행은_옛_판정을_지우고_쓴다(self) -> None:
        import inspect

        from batch.jobs import etf as job
        from batch.jobs import etf_satellite as sj

        src = inspect.getsource(job._store_and_report)
        assert "지우기 = [] if partial else" in src and "before=지우기" in src
        assert "DELETE FROM etf_satellite_picks WHERE as_of_date = ?" in inspect.getsource(sj.run)

    def test_bulk_는_지우기를_같은_묶음_앞에_붙인다(self) -> None:
        from batch.jobs import etf as job

        묶음: list = []

        class C:
            def batch(self, statements):  # noqa: ANN001, ANN202
                묶음.extend(statements)

        job._bulk(C(), "t", "a, b", [(1, 2)], "", before=[("DELETE FROM t", [])])  # type: ignore[arg-type]
        assert 묶음[0] == ("DELETE FROM t", []) and 묶음[1][0].startswith("INSERT INTO t")


def test_쓸_행이_없어도_지우기는_보낸다() -> None:
    """범위가 0개가 된 날 옛 행이 남았다 (docs/infra.md 25.760, 교차검증)."""
    from batch.jobs import etf as job

    묶음: list = []

    class C:
        def batch(self, statements):  # noqa: ANN001, ANN202
            묶음.extend(statements)

    job._bulk(C(), "t", "a, b", [], "", before=[("DELETE FROM t", [])])  # type: ignore[arg-type]
    assert 묶음 == [("DELETE FROM t", [])]
