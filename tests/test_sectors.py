"""업종 분류와 업종 상한 테스트 (batch/services/sectors.py, docs/data-sources.md 15절). 네트워크를 타지 않는다."""

from __future__ import annotations

from batch.jobs import sectors as job
from batch.notify import report_sections as rs
from batch.services import report_picks as rp
from batch.services import sectors as sc
from batch.sources import sec_edgar
from tests.test_report_picks import row


class Test분류:
    def test_국내는_KSIC_앞_두_자리(self) -> None:
        # 삼성전자 DART 기업개황 induty_code 264 (2026-09-17 실측)
        assert sc.kr_sector("264") == ("전자부품·컴퓨터·통신장비", "KSIC 26")
        assert sc.kr_sector("64992") == ("금융", "KSIC 64")

    def test_미국은_SIC_앞_두_자리(self) -> None:
        # 애플 3571, NVIDIA 3674, JPM 6021 (2026-09-17 실측)
        assert sc.us_sector("3571") == ("산업기계·컴퓨터", "SIC 35")
        assert sc.us_sector("3674") == ("전자·전기장비", "SIC 36")
        assert sc.us_sector("6021") == ("예금 금융기관", "SIC 60")

    def test_표에_없는_코드는_코드를_이름으로(self) -> None:
        assert sc.kr_sector("999") == ("KSIC 99", "KSIC 99")

    def test_비었거나_0000_이면_없음(self) -> None:
        for raw in (None, "", "0", "0000", "ab1"):
            assert sc.middle_code(raw) is None
        assert sc.us_sector("0000") == (None, None)

    def test_SEC_응답의_sic(self) -> None:
        assert sec_edgar.parse_sic({"sic": "3571", "ownerOrg": "06 Technology"}) == "3571"
        assert sec_edgar.parse_sic({"sic": ""}) is None

    def test_갱신_문장에_출처와_원래_코드(self) -> None:
        sql, args = job.update_statement(7, "금융", "KSIC 64992", "dart_company", "now")
        assert "sector_code" in sql and args == ["금융", "KSIC 64992", "dart_company", "now", 7]


class Test업종상한:
    def test_같은_업종이_30퍼센트를_넘으면_뺀다(self) -> None:
        rows = [
            row(1, suggested_amount=2_000_000.0, sector="반도체"),
            row(2, suggested_amount=1_500_000.0, sector="반도체"),  # 2+1.5 = 35% > 30%
            row(3, suggested_amount=1_500_000.0, sector="은행"),
        ]
        # 종목 상한(10%)을 풀어 업종 상한만 본다 — 종목 상한은 보유와 무관하게 걸린다 (docs/infra.md 25.290)
        view = rp.build_portfolio(rows, total_investable=10_000_000, max_stock_pct=100)
        assert [a.ticker for a in view.allocations] == ["000001", "000003"]
        assert view.excluded[0].reason == rs.EXCLUDED_SECTOR_CAP
        assert "반도체 이미 20.0%" in view.excluded[0].detail
        assert view.sector_concentration == {"반도체": 20.0, "은행": 15.0}

    def test_업종을_모르면_상한을_적용하지_않는다(self) -> None:
        rows = [row(1, suggested_amount=2_000_000.0), row(2, suggested_amount=2_000_000.0)]
        view = rp.build_portfolio(rows, total_investable=10_000_000)
        assert len(view.allocations) == 2

    def test_설정한_상한을_쓴다(self) -> None:
        rows = [row(1, suggested_amount=2_000_000.0, sector="A"), row(2, suggested_amount=2_000_000.0, sector="A")]
        view = rp.build_portfolio(rows, total_investable=10_000_000, max_sector_pct=50)
        assert len(view.allocations) == 2
