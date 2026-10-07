"""위성 ETF 판정 테스트 (docs/etf.md 10장).

미국은 야후 실제 응답 픽스처(tests/fixtures/us_fund_profiles.json)를 조금씩 바꿔 경계를 본다.
국내는 2026-09-16 한국거래소 응답에서 확인한 실제 기초지수 이름을 쓴다. 네트워크를 타지 않는다.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import etf_satellite as job
from batch.services import etf as core
from batch.services import etf_satellite as sat
from batch.sources import yahoo_fund

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "us_fund_profiles.json").read_text(encoding="utf-8"))
AS_OF = date(2026, 9, 17)
FETCHED = "2026-09-17"


def profile(symbol: str) -> yahoo_fund.FundProfile:
    parsed = yahoo_fund.parse_quote_summary(symbol, FIXTURE[symbol])
    assert parsed is not None
    return copy.deepcopy(parsed)


def kr_input(**kw) -> core.KrEtfInput:
    base = {
        "symbol": "139260",
        "name": "TIGER 200 IT",
        "index_name": "코스피 200 정보기술",
        "net_assets": 2_620_000_000_000.0,
        "avg_turnover": 37_360_000_000.0,
        "premium_abs_avg": 0.0015,
        "days_observed": 20,
        "listed_3y_ago": True,
    }
    base.update(kw)
    return core.KrEtfInput(**base)


class Test미국_묶음_배정:
    def test_넓은_지수는_위성이_아니다(self) -> None:
        assert sat.assign_us("VOO", "Vanguard S&P 500 ETF", profile("VOO")) is None

    def test_섹터_전체_목록은_업종(self) -> None:
        a = sat.assign_us("XLK", "Technology Select Sector SPDR Fund", profile("XLK"))
        assert a is not None and (a.group, a.sub_group) == ("업종", "정보기술")

    def test_같은_분류라도_목록_밖이면_테마(self) -> None:
        p = profile("XLK")
        a = sat.assign_us("SMH", "VanEck Semiconductor ETF", p)
        assert a is not None and (a.group, a.sub_group) == ("테마", "Technology")

    def test_해외_배당(self) -> None:
        p = profile("XLK")
        p.category = "Foreign Large Value"
        a = sat.assign_us("VYMI", "Vanguard International High Dividend Yield ETF", p)
        assert a is not None and (a.group, a.sub_group) == ("배당", "해외 배당")

    def test_원자재는_범위_밖(self) -> None:
        p = profile("XLK")
        p.category = "Commodities Focused"
        assert sat.assign_us("GLD", "SPDR Gold Shares", p) is None


class Test미국_판정:
    def test_XLK_는_업종으로_통과한다(self) -> None:
        ev = sat.evaluate_us("XLK", "Technology Select Sector SPDR Fund", profile("XLK"), AS_OF, FETCHED)
        assert ev is not None and ev.passed, ev and ev.excluded_reason
        labels = [row["label"] for row in ev.criteria]
        for label in ("위성 묶음", "옵션형 아님", "운용 이력", "규모", "유동성", "보유 비중 합", "상위 보유 비중"):
            assert label in labels
        info = next(row for row in ev.criteria if row["label"] == "상위 보유 비중")
        assert info["passed"] is None  # 참고 행은 판정에 쓰지 않는다

    def test_업종은_10년이_필요하다(self) -> None:
        p = profile("XLK")
        p.inception_date = "2018-06-18"  # XLC 설정일
        ev = sat.evaluate_us("XLC", "Communication Services Select Sector SPDR Fund", p, AS_OF, FETCHED)
        assert ev is not None and not ev.passed
        assert "10년" in (ev.excluded_reason or "")

    def test_테마는_3년이면_된다(self) -> None:
        p = profile("XLK")
        p.inception_date = "2020-01-02"
        ev = sat.evaluate_us("SMH", "VanEck Semiconductor ETF", p, AS_OF, FETCHED)
        assert ev is not None and ev.passed, ev and ev.excluded_reason
        assert ev.bucket == "테마"

    def test_옵션형은_뺀다(self) -> None:
        ev = sat.evaluate_us("QYLG", "Global X Nasdaq 100 Covered Call & Growth ETF", profile("XLK"), AS_OF, FETCHED)
        assert ev is not None and not ev.passed
        assert "옵션형" in (ev.excluded_reason or "")

    def test_보유_비중이_부풀려지면_뺀다(self) -> None:
        p = profile("XLK")
        p.stock_position = 1.9881  # BOXX 실측
        ev = sat.evaluate_us("BOXX", "Alpha Architect 1-3 Month Box ETF", p, AS_OF, FETCHED)
        assert ev is not None and not ev.passed
        assert "보유 비중" in (ev.excluded_reason or "")

    def test_하위_묶음_안에서만_순위를_낸다(self) -> None:
        cheap = profile("XLK")
        cheap.expense_ratio = 0.0008
        pricey = profile("XLK")
        pricey.expense_ratio = 0.0039
        theme = profile("XLK")
        evs = [
            sat.evaluate_us("XLK", "Technology Select Sector SPDR Fund", cheap, AS_OF, FETCHED),
            sat.evaluate_us("IYW", "iShares U.S. Technology ETF", pricey, AS_OF, FETCHED),
            sat.evaluate_us("SMH", "VanEck Semiconductor ETF", theme, AS_OF, FETCHED),
        ]
        sat.score([ev for ev in evs if ev])
        xlk, iyw, smh = evs
        assert xlk.rank_in_category == 1 and iyw.rank_in_category == 2 and xlk.category_size == 2
        assert smh.category_size == 1  # 테마는 따로 센다
        assert "정보기술 2개 중 1위" in sat.rationale_text(xlk)


class Test국내_묶음_배정:
    @pytest.mark.parametrize(
        ("index_name", "expected"),
        [
            ("코스피 200", None),
            ("코스피 200 정보기술", ("업종", "코스피 200 정보기술")),
            ("FnGuide 배당주 지수", ("배당", "FnGuide 배당주 지수")),
            ("Dow Jones U.S. Dividend 100 Price Return Index", ("배당", "Dow Jones U.S. Dividend 100 Price Return Index")),
            ("KRX 반도체", ("테마", "KRX 반도체")),
            ("코리아 밸류업 지수", ("테마", "코리아 밸류업 지수")),
            ("KAP MMF 지수(TR)", None),
            ("KRX 삼성전자 지수", None),
            ("코스피 200 선물지수", None),
            ("코스피 200 타겟 15% 위클리 커버드콜 지수", None),
            ("KRX 금현물지수", None),
            ("KAP 양도성예금증서(CD)금리지수(총수익지수)", None),
        ],
    )
    def test_실제_기초지수_이름(self, index_name: str, expected) -> None:
        a = sat.assign_kr(index_name)
        assert (None if a is None else (a.group, a.sub_group)) == expected


class Test국내_판정:
    def test_업종_통과(self) -> None:
        ev = sat.evaluate_kr(kr_input(), FETCHED)
        assert ev is not None and ev.passed, ev and ev.excluded_reason
        labels = [row["label"] for row in ev.criteria]
        for label in ("상품 유형", "위성 묶음", "운용 이력", "규모", "유동성", "괴리율", "분배금 처리", "환헤지"):
            assert label in labels

    def test_3년_미만이면_뺀다(self) -> None:
        ev = sat.evaluate_kr(kr_input(index_name="코리아 밸류업 지수", name="KODEX 코리아밸류업", listed_3y_ago=False), FETCHED)
        assert ev is not None and not ev.passed
        assert "3년" in (ev.excluded_reason or "")

    def test_레버리지는_이름으로_먼저_뺀다(self) -> None:
        ev = sat.evaluate_kr(kr_input(name="TIGER 200IT레버리지"), FETCHED)
        assert ev is not None and not ev.passed
        assert "레버리지" in (ev.excluded_reason or "")

    def test_분배금_표기(self) -> None:
        assert sat.distribution_type("WISE 대형고배당10 TR 지수").startswith("TR")
        assert sat.distribution_type("FnGuide 코리아 고배당 지수(PR)").startswith("PR")
        assert sat.distribution_type("FnGuide 배당주 지수") == "표기 없음"

    def test_경고에_보수_미반영이_들어간다(self) -> None:
        ev = sat.evaluate_kr(kr_input(), FETCHED)
        assert ev is not None
        assert sat.WARNINGS["국내"] in sat.warnings_for(ev)
        theme = sat.evaluate_kr(kr_input(index_name="KRX 반도체", name="KODEX 반도체"), FETCHED)
        assert theme is not None and sat.WARNINGS["테마"] in sat.warnings_for(theme)


class Test저장:
    def test_행과_열_개수_그리고_실제_스키마(self) -> None:
        ev = sat.evaluate_kr(kr_input(), FETCHED)
        assert ev is not None
        sat.score([ev])
        row = job.to_row(1, "2026-09-16", ev, FETCHED)
        assert len(row) == db.column_count(job._COLS)

        conn = sqlite3.connect(":memory:")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO etfs (id, symbol, country, name, source, fetched_at) VALUES (1, '139260', 'KR', 'x', 't', 't')"
        )
        conn.execute(f"INSERT INTO etf_satellite_picks ({job._COLS}) VALUES ({', '.join(['?'] * len(row))})", row)
        saved = conn.execute("SELECT sat_group, sub_group, passed, rationale_data FROM etf_satellite_picks").fetchone()
        assert saved[:3] == ("업종", "코스피 200 정보기술", 1)
        assert json.loads(saved[3])["warnings"]
        # 위성은 계좌 가능 여부만, 순위 없음 (25.966)
        accounts = json.loads(saved[3])["accounts"]
        assert accounts["pension"]["eligible"] is True and accounts["pension"]["priority"] is None


def test_거래량이_없으면_미만이_아니라_확인_불가() -> None:
    """거래량 없음을 "거래대금 $5M 미만" 으로 적었다 (docs/infra.md 25.759, ETF 감사)."""
    p = profile("XLK")
    p.avg_volume = None
    ev = sat.evaluate_us("XLK", "Technology Select Sector SPDR Fund", p, AS_OF, FETCHED)
    assert ev is not None and not ev.passed
    assert ev.excluded_reason == "확인할 수 없는 값: 거래량"


def test_업종_목록_기호도_분류가_없으면_확인_불가() -> None:
    """분류 없이 통과해 분류로 거르는 레버리지 검사를 건너뛰었다 (docs/infra.md 25.761, ETF 감사)."""
    p = profile("XLK")
    p.category = None
    ev = sat.evaluate_us("XLK", "Technology Select Sector SPDR Fund", p, AS_OF, FETCHED)
    assert ev is not None and not ev.passed and "분류" in (ev.excluded_reason or "")


@pytest.mark.parametrize(
    "index_name",
    ["S&P 500 Futures Index(ER)", "NASDAQ 100 Futures Index (ER)", "S&P 500 VIX Short-Term Futures Index ER"],
)
def test_영문_선물_지수는_테마로_통과하지_않는다(index_name: str) -> None:
    """한글 `선물` 만 알아 외국 선물형 ETF 가 국내 테마 위성으로 통과했다 (docs/infra.md 25.933, 감사 재현)."""
    assert sat.assign_kr(index_name) is None


def test_선물_낱말이_없는_주식형_테마는_그대로다() -> None:
    assert sat.assign_kr("Solactive Global Robotics Index") is not None
