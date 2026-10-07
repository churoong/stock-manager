"""미국 종목 마스터 파싱 테스트.

네트워크를 타지 않는다. 아래 표본은 2026-09-16 에 실제로 받은 파일의
머리글과 행 형식을 그대로 옮긴 것이다.

두 파일의 열 순서가 다르다는 점이 이 모듈의 핵심 위험이라 그것부터 고정한다.
"""

from __future__ import annotations

import pytest

from batch.sources.nasdaq_symbols import FILES, UsSymbol, _parse

# nasdaqlisted.txt: Symbol|Security Name|Market Category|Test Issue|
#                   Financial Status|Round Lot Size|ETF|NextShares
NASDAQ_FILE = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust, Series 1|Q|Y|N|100|Y|N
ZTEST|Nasdaq Test Stock|G|Y|N|100|N|N
ABCDP|Some Bank Inc. - 6.5% Preferred Stock Series A|Q|N|N|100|N|N
File Creation Time: 0915202621:31|||||||
"""

# otherlisted.txt: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|
#                  Round Lot Size|Test Issue|NASDAQ Symbol
OTHER_FILE = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
A|Agilent Technologies, Inc. Common Stock|N|A|N|100|N|A
BRK.B|Berkshire Hathaway Inc. Class B Common Stock|N|BRK B|N|100|N|BRK.B
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
File Creation Time: 0915202621:31|||||||
"""


def parse_nasdaq() -> list[UsSymbol]:
    return _parse(NASDAQ_FILE, FILES["nasdaqlisted.txt"])


def parse_other() -> list[UsSymbol]:
    return _parse(OTHER_FILE, FILES["otherlisted.txt"])


class Test열순서:
    """두 파일에서 같은 뜻의 열이 다른 자리에 있다.

    위치로 읽으면 ETF 를 보통주로, 보통주를 테스트 종목으로 읽는다.
    """

    def test_나스닥_파일에서_ETF를_바르게_읽는다(self) -> None:
        by_symbol = {s.symbol: s for s in parse_nasdaq()}

        assert by_symbol["QQQ"].is_etf
        assert not by_symbol["AAPL"].is_etf

    def test_그_외_파일에서도_ETF를_바르게_읽는다(self) -> None:
        by_symbol = {s.symbol: s for s in parse_other()}

        assert by_symbol["SPY"].is_etf
        assert not by_symbol["A"].is_etf

    def test_테스트_종목을_바르게_읽는다(self) -> None:
        by_symbol = {s.symbol: s for s in parse_nasdaq()}

        assert by_symbol["ZTEST"].is_test
        assert not by_symbol["AAPL"].is_test

    def test_끝_표지가_없으면_잘린_파일로_본다(self) -> None:
        """잘린 파일을 받으면 뒤쪽 종목이 상장폐지로 적혔다 (docs/infra.md 25.527, 교차검증)."""
        잘림 = "\n".join(줄 for 줄 in NASDAQ_FILE.splitlines() if not 줄.startswith("File Creation Time"))
        with pytest.raises(ValueError, match="잘렸습니다"):
            _parse(잘림, FILES["nasdaqlisted.txt"])

    def test_열_이름이_바뀌면_바로_실패한다(self) -> None:
        # 조용히 틀린 값을 읽느니 실패하는 편이 낫다
        broken = "Ticker|Name\nAAPL|Apple\n"
        with pytest.raises(ValueError, match="기대한 열이 없습니다"):
            _parse(broken, FILES["nasdaqlisted.txt"])


class Test보통주판정:
    def test_보통주는_통과한다(self) -> None:
        by_symbol = {s.symbol: s for s in parse_nasdaq()}
        assert by_symbol["AAPL"].is_common_stock

    def test_ETF는_제외한다(self) -> None:
        by_symbol = {s.symbol: s for s in parse_nasdaq()}
        assert not by_symbol["QQQ"].is_common_stock

    def test_테스트_종목은_제외한다(self) -> None:
        by_symbol = {s.symbol: s for s in parse_nasdaq()}
        assert not by_symbol["ZTEST"].is_common_stock

    def test_우선주는_이름으로_제외한다(self) -> None:
        # 거래소가 증권 종류 열을 주지 않아 이름으로 판정할 수밖에 없다
        by_symbol = {s.symbol: s for s in parse_nasdaq()}
        assert not by_symbol["ABCDP"].is_common_stock

    @pytest.mark.parametrize(
        "name",
        [
            "Acme Corp Warrant",
            "Acme Corp Units",
            "Acme Corp Depositary Shares",
            "Acme 6.25% Notes due 2030",
        ],
    )
    def test_보통주가_아닌_증권들을_제외한다(self, name: str) -> None:
        symbol = UsSymbol(symbol="X", name=name, exchange="NYSE", is_etf=False, is_test=False)
        assert not symbol.is_common_stock


class Test거래소:
    def test_나스닥_파일은_전부_나스닥이다(self) -> None:
        rows = parse_nasdaq()

        # 파싱이 통째로 실패해 빈 목록이 와도 `all()` 은 참이다
        assert rows, "나스닥 파일에서 아무것도 읽지 못했다"
        assert all(s.exchange == "NASDAQ" for s in rows)

    def test_코드를_거래소_이름으로_바꾼다(self) -> None:
        by_symbol = {s.symbol: s for s in parse_other()}

        assert by_symbol["A"].exchange == "NYSE"
        assert by_symbol["SPY"].exchange == "NYSE Arca"


class Test야후심볼:
    def test_점을_하이픈으로_바꾼다(self) -> None:
        # 거래소는 BRK.B, 야후는 BRK-B 를 쓴다.
        # 맞추지 않으면 그 종목만 조용히 빠진다
        by_symbol = {s.symbol: s for s in parse_other()}
        assert by_symbol["BRK.B"].yahoo_symbol == "BRK-B"

    def test_점이_없으면_그대로다(self) -> None:
        by_symbol = {s.symbol: s for s in parse_other()}
        assert by_symbol["A"].yahoo_symbol == "A"


class Test꼬리줄:
    def test_파일_생성시각_줄을_종목으로_읽지_않는다(self) -> None:
        for rows in (parse_nasdaq(), parse_other()):
            assert rows, "파일에서 아무것도 읽지 못했다"
            assert all("File Creation Time" not in s.symbol for s in rows)

    def test_행_수가_맞는다(self) -> None:
        assert len(parse_nasdaq()) == 4
        assert len(parse_other()) == 3


class TestHTTP헤더:
    def test_UserAgent에_한글이_없다(self) -> None:
        """HTTP 헤더는 latin-1 로만 인코딩된다.

        한글을 넣으면 요청을 보내기도 전에 인코딩 오류가 난다.
        실제로 겪었고, 배포된 뒤에야 드러났다.
        """
        from batch.sources.nasdaq_symbols import USER_AGENT

        USER_AGENT.encode("latin-1")  # 실패하면 여기서 예외가 난다
        assert USER_AGENT.isascii()


# ----------------------------------------------------------------------
# 심볼 형태로 거르기 (2026-09-16 추가)
# ----------------------------------------------------------------------


def symbol(sym: str, name: str = "Something Inc. Common Stock"):
    from batch.sources.nasdaq_symbols import UsSymbol

    return UsSymbol(
        symbol=sym, name=name, exchange="NYSE", is_etf=False, is_test=False
    )


class Test심볼형태로거르기:
    """이름만으로 거르다가 43종목을 놓쳤다.

    미국 전종목 수집을 처음 운영에서 돌렸을 때 야후가 "No data found" 로
    답한 종목들이고, 전부 우선주($)와 워런트(-W)였다. 실패 목록이 그대로
    근거이므로 그 형태를 여기서 고정한다.
    """

    def test_우선주_표기를_거른다(self) -> None:
        # 실제로 실패한 심볼들이다
        for sym in ("TRTN$C", "TRTN$A", "MS$F", "BAC$L", "SCE$N", "VNO$L"):
            assert not symbol(sym).is_common_stock, sym

    def test_워런트를_거른다(self) -> None:
        for sym in ("AAC-W", "BIII-W", "BEBE-W", "IONQ-W", "NPWR-W"):
            assert not symbol(sym).is_common_stock, sym

    def test_유닛과_신주인수권도_거른다(self) -> None:
        for sym in ("ABCD-U", "ABCD-R", "ABCD-RT", "ABCD-WS", "ABCD-WT"):
            assert not symbol(sym).is_common_stock, sym

    def test_클래스_주식은_살린다(self) -> None:
        """BRK.B 와 BF.B 는 어엿한 보통주다. 함께 버리면 안 된다."""
        for sym in ("BRK.B", "BF.B", "BRK-A", "HEI-A", "LEN-B"):
            assert symbol(sym).is_common_stock, sym

    def test_평범한_심볼은_살린다(self) -> None:
        for sym in ("AAPL", "MSFT", "T", "F"):
            assert symbol(sym).is_common_stock, sym

    def test_구분자_뒤_소문자도_같게_본다(self) -> None:
        assert not symbol("abcd-w").is_common_stock

    def test_이름_판정은_그대로_동작한다(self) -> None:
        """형태를 더했다고 이름 판정이 느슨해지면 안 된다."""
        assert not symbol("ABCD", "Some Corp 7% Preferred Series A").is_common_stock
        assert not symbol("ABCD", "Some Corp Warrant").is_common_stock

    def test_야후_표기는_그대로_점을_바꾼다(self) -> None:
        assert symbol("BRK.B").yahoo_symbol == "BRK-B"


def test_표지는_낱말로_찾는다() -> None:
    """" Unit" 이 First United·Community Unity 에 걸려 멀쩡한 보통주가 빠졌다 (docs/infra.md 25.528, 감사 재현)."""
    from batch.sources.nasdaq_symbols import UsSymbol

    def 보통주(name: str) -> bool:
        return UsSymbol("X", name, "NASDAQ", False, False).is_common_stock

    assert 보통주("First United Corporation - Common Stock")
    assert 보통주("Community Unity Bancorp Common Stock")
    assert 보통주("Rightside Group Common Stock")
    assert not 보통주("Acme Acquisition Corp - Units")
    assert not 보통주("Acme Acquisition Corp - Rights")
    assert not 보통주("ABC Corp 5.25% Notes due 2030")
    assert not 보통주("XYZ Corp - Warrant")
    # 복수형도 뺀다, 이름이 표지 낱말로 시작하면 옛 판정대로 통과 (25.536, 교차검증 재현)
    assert not 보통주("XYZ Acquisition Corp - Warrants")
    assert not 보통주("Foo Corp 6% Debentures due 2040")
    assert not 보통주("ABC Preferreds")
    assert 보통주("Unit Corporation - Common Stock")
    assert 보통주("Right On Brands Common Stock")
