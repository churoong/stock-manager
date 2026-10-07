"""DART 응답 파싱 테스트.

네트워크를 타지 않는다. 아래 표본은 2026-09-16 에 실제로 받은 응답에서
구조와 값을 그대로 옮긴 것이다.

접수번호에서 접수일을 뽑는 부분이 이 모듈의 핵심이다. 틀리면 백테스트가
미래를 본다.
"""

from __future__ import annotations

import pytest

from batch.sources import dart

# 실제 응답에서 옮긴 삼성전자 연결 주요계정 (2025 사업보고서)
SAMSUNG_ROWS = [
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "CFS", "sj_div": "BS",
     "account_nm": "유동자산", "thstrm_amount": "247,684,612,000,000",
     "rcept_no": "20260310002820", "currency": "KRW"},
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "CFS", "sj_div": "BS",
     "account_nm": "자산총계", "thstrm_amount": "566,942,110,000,000",
     "rcept_no": "20260310002820", "currency": "KRW"},
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "CFS", "sj_div": "BS",
     "account_nm": "부채총계", "thstrm_amount": "130,621,773,000,000",
     "rcept_no": "20260310002820", "currency": "KRW"},
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "CFS", "sj_div": "BS",
     "account_nm": "자본총계", "thstrm_amount": "436,320,337,000,000",
     "rcept_no": "20260310002820", "currency": "KRW"},
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "CFS", "sj_div": "IS",
     "account_nm": "매출액", "thstrm_amount": "333,605,938,000,000",
     "rcept_no": "20260310002820", "currency": "KRW"},
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "CFS", "sj_div": "IS",
     "account_nm": "영업이익", "thstrm_amount": "43,601,051,000,000",
     "rcept_no": "20260310002820", "currency": "KRW"},
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "CFS", "sj_div": "IS",
     "account_nm": "당기순이익(손실)", "thstrm_amount": "45,206,805,000,000",
     "rcept_no": "20260310002820", "currency": "KRW"},
    # 같은 이름이 다른 재무제표에도 나온다. 손익계산서 것만 읽어야 한다
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "CFS", "sj_div": "CIS",
     "account_nm": "당기순이익(손실)", "thstrm_amount": "999,999,999,999",
     "rcept_no": "20260310002820", "currency": "KRW"},
    # 별도 재무제표도 함께 온다
    {"corp_code": "00126380", "stock_code": "005930", "fs_div": "OFS", "sj_div": "IS",
     "account_nm": "매출액", "thstrm_amount": "200,000,000,000,000",
     "rcept_no": "20260310002820", "currency": "KRW"},
]


class Test접수일:
    """접수번호 앞 8자리가 접수일자다. 시점 스냅샷의 기준이다."""

    def test_접수번호에서_날짜를_뽑는다(self) -> None:
        assert dart.receipt_date("20260310002820") == "2026-03-10"

    def test_다른_보고서도_맞는다(self) -> None:
        assert dart.receipt_date("20250311001085") == "2025-03-11"

    @pytest.mark.parametrize(
        "bad", ["", None, "1234", "abcdefgh1234", "00000000123456"]
    )
    def test_이상한_접수번호는_None(self, bad) -> None:
        # 접수일을 모르면 시점 스냅샷을 만들 수 없다. 추측하지 않는다
        assert dart.receipt_date(bad) is None

    def test_말이_안_되는_월일을_거부한다(self) -> None:
        assert dart.receipt_date("20261332002820") is None


class Test금액변환:
    def test_쉼표를_떼어낸다(self) -> None:
        # 다중회사 API 는 쉼표를 넣어 준다
        assert dart._to_int("333,605,938,000,000") == 333_605_938_000_000

    def test_쉼표가_없어도_된다(self) -> None:
        # 단일회사 API 는 쉼표를 넣지 않는다
        assert dart._to_int("333605938000000") == 333_605_938_000_000

    def test_음수_표기를_읽는다(self) -> None:
        assert dart._to_int("-1,234") == -1234

    def test_괄호는_음수다(self) -> None:
        # 회계 표기에서 괄호는 마이너스다. 양수로 읽으면 적자가 흑자가 된다
        assert dart._to_int("(1,234)") == -1234

    @pytest.mark.parametrize("empty", ["", " ", "-", ".", None])
    def test_결측은_None(self, empty) -> None:
        assert dart._to_int(empty) is None

    def test_숫자가_아니면_None_이고_예외를_내지_않는다(self) -> None:
        assert dart._to_int("해당사항없음") is None

    def test_큰_수의_정밀도가_보존된다(self) -> None:
        # 566조를 부동소수로 다루면 값이 망가진다
        assert dart._to_int("566,942,110,000,000") == 566942110000000


class Test응답파싱:
    def parsed(self):
        return dart.parse_multi_response(
            {"list": SAMSUNG_ROWS}, fiscal_year=2025, report_code="11011"
        )

    def test_연결과_별도를_따로_만든다(self) -> None:
        results = self.parsed()
        assert len(results) == 2
        assert {r.consolidated for r in results} == {True, False}

    def test_연결_재무를_바르게_읽는다(self) -> None:
        cfs = next(r for r in self.parsed() if r.consolidated)

        assert cfs.values["total_assets"] == 566_942_110_000_000
        assert cfs.values["revenue"] == 333_605_938_000_000
        assert cfs.values["operating_income"] == 43_601_051_000_000

    def test_같은_이름이_여러_재무제표에_있어도_손익계산서만_읽는다(self) -> None:
        # 포괄손익계산서의 당기순이익을 읽으면 값이 뒤바뀐다
        cfs = next(r for r in self.parsed() if r.consolidated)
        assert cfs.values["net_income"] == 45_206_805_000_000
        assert cfs.values["net_income"] != 999_999_999_999

    def test_별도_재무는_다른_값을_갖는다(self) -> None:
        ofs = next(r for r in self.parsed() if not r.consolidated)
        assert ofs.values["revenue"] == 200_000_000_000_000

    def test_접수일을_함께_담는다(self) -> None:
        for result in self.parsed():
            assert result.report_date == "2026-03-10"
            assert result.receipt_no == "20260310002820"

    def test_종목코드를_담는다(self) -> None:
        assert all(r.stock_code == "005930" for r in self.parsed())

    def test_없는_계정은_None(self) -> None:
        cfs = next(r for r in self.parsed() if r.consolidated)
        # 표본에 이익잉여금이 없다
        assert cfs.values["retained_earnings"] is None

    def test_접수번호가_이상하면_그_회사를_버린다(self) -> None:
        # 접수일을 모르면 시점 스냅샷을 만들 수 없다
        bad = [{**row, "rcept_no": "깨진값"} for row in SAMSUNG_ROWS]
        assert dart.parse_multi_response({"list": bad}, 2025, "11011") == []

    def test_빈_응답이어도_무너지지_않는다(self) -> None:
        assert dart.parse_multi_response({}, 2025, "11011") == []
        assert dart.parse_multi_response({"list": []}, 2025, "11011") == []


class Test계정이름표기:
    """회사마다 매출 계정 이름이 다르다."""

    @pytest.mark.parametrize("name", ["매출액", "수익(매출액)", "영업수익"])
    def test_여러_매출_표기를_읽는다(self, name: str) -> None:
        rows = [
            {"corp_code": "X", "stock_code": "000001", "fs_div": "CFS", "sj_div": "IS",
             "account_nm": name, "thstrm_amount": "1,000", "rcept_no": "20260310000001",
             "currency": "KRW"}
        ]
        result = dart.parse_multi_response({"list": rows}, 2025, "11011")[0]
        assert result.values["revenue"] == 1000

    def test_손익_항목은_손익계산서에서만_읽는다(self) -> None:
        for name in ("revenue", "operating_income", "net_income"):
            assert dart.STATEMENT_FOR[name] == "IS"

    def test_손익계산서가_없으면_포괄손익계산서로_물러난다(self) -> None:
        """단일 포괄손익계산서 회사의 매출·이익이 전부 비었다 (docs/infra.md 25.659, 감사)."""
        기본 = {"corp_code": "X", "stock_code": "000001", "fs_div": "CFS", "rcept_no": "20260310000001", "currency": "KRW"}
        rows = [
            {**기본, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "9,000"},
            {**기본, "sj_div": "CIS", "account_nm": "매출액", "thstrm_amount": "1,000"},
            {**기본, "sj_div": "CIS", "account_nm": "당기순이익(손실)", "thstrm_amount": "100"},
        ]
        values = dart.parse_multi_response({"list": rows}, 2025, "11011")[0].values
        assert values["revenue"] == 1000 and values["net_income"] == 100
        # 둘 다 있으면 예전처럼 IS 가 이긴다 — SAMSUNG_ROWS 의 CIS 999,999… 가 읽히지 않는다
        cfs = next(r for r in dart.parse_multi_response({"list": SAMSUNG_ROWS}, 2025, "11011") if r.consolidated)
        assert cfs.values["net_income"] == 45_206_805_000_000

    def test_재무상태표_항목은_BS_에서만_읽는다(self) -> None:
        for name in ("total_assets", "total_equity", "total_liabilities"):
            assert dart.STATEMENT_FOR[name] == "BS"


class Test고유번호파싱:
    XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
    <list>
        <corp_code>00126380</corp_code>
        <corp_name>삼성전자</corp_name>
        <stock_code>005930</stock_code>
        <modify_date>20251201</modify_date>
    </list>
    <list>
        <corp_code>00434003</corp_code>
        <corp_name>다코</corp_name>
        <stock_code> </stock_code>
        <modify_date>20170630</modify_date>
    </list>
</result>"""

    def test_상장사만_뽑는다(self) -> None:
        # 비상장사는 종목코드 자리가 공백이다. 11만 건 중 4천 건쯤만 상장사다
        codes = dart.parse_corp_codes(self.XML)

        assert len(codes) == 1
        assert codes[0].stock_code == "005930"
        assert codes[0].corp_code == "00126380"
        assert codes[0].corp_name == "삼성전자"

    def test_빈_XML_이어도_무너지지_않는다(self) -> None:
        assert dart.parse_corp_codes("<result></result>") == []


class Test묶음크기:
    def test_상한을_넘기면_거부한다(self) -> None:
        result = dart.fetch_multi_financials(["0" * 8] * 101, 2025, "11011")

        assert not result.ok
        assert "100개까지" in result.error

    def test_상한은_100이다(self) -> None:
        # 실제 호출로 확인한 값
        assert dart.MAX_CORPS_PER_CALL == 100

    def test_일일_한도가_기록돼_있다(self) -> None:
        assert dart.DAILY_LIMIT == 20_000


class Test보고서코드:
    def test_사업보고서는_연간이다(self) -> None:
        assert dart.REPORT_CODES["11011"][1] == "A"

    def test_나머지는_분기다(self) -> None:
        for code in ("11012", "11013", "11014"):
            assert dart.REPORT_CODES[code][1] == "Q"

    def test_빈_목록도_거부한다(self) -> None:
        result = dart.fetch_multi_financials([], 2025, "11011")

        assert not result.ok
        assert "비어 있습니다" in result.error

    def test_인자_검증이_실행_모드보다_먼저다(self) -> None:
        """잘못된 인자는 오프라인이든 아니든 잘못된 인자다.

        모드에 따라 검증이 건너뛰어지면 호출부의 버그가 조용히 넘어간다.
        실제로 이 순서가 뒤바뀌어 있었고 테스트가 잡았다.
        """
        result = dart.fetch_multi_financials(["0" * 8] * 101, 2025, "11011")
        assert "100개까지" in result.error
