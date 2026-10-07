"""유니버스 판정 테스트.

규칙이 틀리면 유니버스 전체가 틀어지고, 그 위에 쌓이는 점수와 신호가
전부 영향을 받는다. 고정 데이터로 경계를 못박아 둔다.
"""

from __future__ import annotations

from datetime import date

import pytest

from batch.services import universe as u

AS_OF = date(2026, 9, 16)


def candidate(**kwargs) -> u.Candidate:
    """기본값은 모든 조건을 통과하는 종목. 테스트마다 한 가지만 어긋나게 한다."""
    base = {
        "ticker": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "security_group": "주권",
        "section_type": "",
        "share_kind": "보통주",
        "listed_date": "1975-06-11",
        "market_cap": 500_000_000_000_000,
        "avg_turnover_20d": 1_000_000_000_000,
        "is_halted": False,
    }
    base.update(kwargs)
    return u.Candidate(**base)


KR = u.UniverseFilters.korea()


class Test기본:
    def test_모든_조건을_통과하면_편입된다(self) -> None:
        verdict = u.judge(candidate(), KR, AS_OF)

        assert verdict.included
        assert verdict.reason is None

    def test_편입이면_사유가_없다(self) -> None:
        assert u.judge(candidate(), KR, AS_OF).reason is None

    def test_상장_경과일을_함께_돌려준다(self) -> None:
        verdict = u.judge(candidate(listed_date="2025-09-16"), KR, AS_OF)
        assert verdict.listed_days == 365


class Test제외규칙:
    def test_거래정지(self) -> None:
        assert u.judge(candidate(is_halted=True), KR, AS_OF).reason == u.REASON_HALTED

    def test_소속부에_거래정지가_적혀도_잡는다(self) -> None:
        verdict = u.judge(candidate(section_type="거래정지"), KR, AS_OF)
        assert verdict.reason == u.REASON_HALTED

    def test_관리종목(self) -> None:
        verdict = u.judge(candidate(section_type="관리종목"), KR, AS_OF)
        assert verdict.reason == u.REASON_SUPERVISED

    def test_투자주의환기종목도_빼되_이름대로_적는다(self) -> None:
        """예전에는 "관리종목" 으로 적어 사유가 부정확했다 (docs/infra.md 25.425)."""
        verdict = u.judge(candidate(section_type="투자주의환기종목"), KR, AS_OF)
        assert not verdict.included and verdict.reason == u.REASON_CAUTION

    def test_정리매매도_빼되_이름대로_적는다(self) -> None:
        verdict = u.judge(candidate(section_type="정리매매"), KR, AS_OF)
        assert not verdict.included and verdict.reason == u.REASON_LIQUIDATION

    def test_관리와_투자주의환기가_함께면_관리종목(self) -> None:
        verdict = u.judge(candidate(section_type="관리종목(투자주의환기)"), KR, AS_OF)
        assert verdict.reason == u.REASON_SUPERVISED

    def test_스팩은_이름으로_잡는다(self) -> None:
        verdict = u.judge(candidate(name="교보14호스팩"), KR, AS_OF)
        assert verdict.reason == u.REASON_SPAC

    def test_주권이_아니면_뺀다(self) -> None:
        # 부동산투자회사는 재무 비교의 잣대가 다르다
        verdict = u.judge(candidate(security_group="부동산투자회사"), KR, AS_OF)
        assert verdict.reason == u.REASON_NOT_STOCK

    def test_우선주를_뺀다(self) -> None:
        verdict = u.judge(candidate(share_kind="우선주"), KR, AS_OF)
        assert verdict.reason == u.REASON_NOT_COMMON

    def test_우선주를_남기도록_설정할_수_있다(self) -> None:
        keep = u.UniverseFilters(
            min_market_cap=KR.min_market_cap,
            min_avg_turnover_20d=KR.min_avg_turnover_20d,
            exclude_preferred=False,
        )
        assert u.judge(candidate(share_kind="우선주"), keep, AS_OF).included

    def test_상장_1년_미만(self) -> None:
        verdict = u.judge(candidate(listed_date="2026-01-02"), KR, AS_OF)
        assert verdict.reason == u.REASON_NEWLY_LISTED

    def test_시총_미달(self) -> None:
        verdict = u.judge(candidate(market_cap=50_000_000_000), KR, AS_OF)
        assert verdict.reason == u.REASON_SMALL_CAP

    def test_거래대금_미달(self) -> None:
        verdict = u.judge(candidate(avg_turnover_20d=100_000_000), KR, AS_OF)
        assert verdict.reason == u.REASON_LOW_TURNOVER


class Test경계값:
    def test_상장일이_정확히_365일_전이면_편입(self) -> None:
        assert u.judge(candidate(listed_date="2025-09-16"), KR, AS_OF).included

    def test_364일이면_제외(self) -> None:
        verdict = u.judge(candidate(listed_date="2025-09-17"), KR, AS_OF)
        assert verdict.reason == u.REASON_NEWLY_LISTED

    def test_시총이_하한과_같으면_편입(self) -> None:
        assert u.judge(candidate(market_cap=KR.min_market_cap), KR, AS_OF).included

    def test_시총이_하한보다_1원_적으면_제외(self) -> None:
        verdict = u.judge(candidate(market_cap=KR.min_market_cap - 1), KR, AS_OF)
        assert verdict.reason == u.REASON_SMALL_CAP

    def test_거래대금이_하한과_같으면_편입(self) -> None:
        assert u.judge(
            candidate(avg_turnover_20d=KR.min_avg_turnover_20d), KR, AS_OF
        ).included


class Test결측:
    def test_상장일이_없으면_데이터없음(self) -> None:
        verdict = u.judge(candidate(listed_date=None), KR, AS_OF)
        assert verdict.reason == u.REASON_NO_DATA
        assert verdict.listed_days is None

    def test_상장일_형식이_깨져도_예외를_내지_않는다(self) -> None:
        verdict = u.judge(candidate(listed_date="19750611"), KR, AS_OF)
        assert verdict.reason == u.REASON_NO_DATA

    def test_시총이_없으면_데이터없음(self) -> None:
        verdict = u.judge(candidate(market_cap=None), KR, AS_OF)
        assert verdict.reason == u.REASON_NO_DATA

    def test_거래대금이_없으면_데이터없음(self) -> None:
        # 신규 편입 종목은 20일치가 아직 없다. 편입하지 않는다
        verdict = u.judge(candidate(avg_turnover_20d=None), KR, AS_OF)
        assert verdict.reason == u.REASON_NO_DATA


class Test사유우선순위:
    def test_관리종목이면서_시총도_작으면_관리종목으로_기록한다(self) -> None:
        # 수치 미달보다 결격 사유가 더 본질적이다
        verdict = u.judge(
            candidate(section_type="관리종목", market_cap=1), KR, AS_OF
        )
        assert verdict.reason == u.REASON_SUPERVISED

    def test_거래정지가_관리종목보다_앞선다(self) -> None:
        verdict = u.judge(candidate(is_halted=True, section_type="관리종목"), KR, AS_OF)
        assert verdict.reason == u.REASON_HALTED

    def test_스팩이면_시총을_보지_않는다(self) -> None:
        verdict = u.judge(candidate(name="하나금융25호스팩", market_cap=1), KR, AS_OF)
        assert verdict.reason == u.REASON_SPAC


class Test스팩판정:
    @pytest.mark.parametrize(
        "name",
        ["교보14호스팩", "하나금융25호스팩", "NH스팩29호", "미래에셋비전스팩1호"],
    )
    def test_스팩으로_본다(self, name: str) -> None:
        assert u.is_spac(name)

    @pytest.mark.parametrize("name", ["삼성전자", "SK하이닉스", "현대차", "카카오"])
    def test_일반_종목은_아니다(self, name: str) -> None:
        assert not u.is_spac(name)

    def test_빈_이름도_예외를_내지_않는다(self) -> None:
        assert not u.is_spac("")


class Test집계:
    def test_편입과_사유별_제외를_센다(self) -> None:
        verdicts = [
            u.judge(candidate(), KR, AS_OF),
            u.judge(candidate(name="A스팩"), KR, AS_OF),
            u.judge(candidate(name="B스팩"), KR, AS_OF),
            u.judge(candidate(market_cap=1), KR, AS_OF),
        ]
        counts = u.summarize(verdicts)

        assert counts["편입"] == 1
        assert counts[u.REASON_SPAC] == 2
        assert counts[u.REASON_SMALL_CAP] == 1

    def test_빈_목록이면_편입_0(self) -> None:
        assert u.summarize([]) == {"편입": 0}


class Test기준값:
    def test_국내_시총_하한은_1000억이다(self) -> None:
        assert u.UniverseFilters.korea().min_market_cap == 100_000_000_000

    def test_미국_시총_하한은_10억달러다(self) -> None:
        assert u.UniverseFilters.usa().min_market_cap == 1_000_000_000

    def test_상장_경과_기준은_1년이다(self) -> None:
        assert u.UniverseFilters.korea().min_listed_days == 365


class Test못_보는_검사에는_사유가_있다:
    """**"0 건" 과 "안 봄" 은 다르다** (docs/infra.md 25.129).

    CLAUDE.md 의 유니버스 제외 규칙은 시장을 가리지 않는다 — "관리종목, 거래정지, 스팩,
    상장 1년 미만, 시총 하한, 20일 평균 거래대금 하한". 그런데 앞의 셋은 **국내 거래소가
    주는 열**에 기대고 있고 미국 마스터(나스닥 심볼 디렉터리)에는 그런 열이 없다.

    그래서 미국 스냅샷의 제외 사유에 "관리종목" 이 한 건도 안 나온다. 없어서가 아니라
    **안 봐서**다. 파산 절차 중인 회사가 유니버스에 남아 추천까지 갈 수 있다.

    여기서 지키는 것은 **사유 없는 예외를 두지 않는 것**이다. 못 보면 못 본다고 적고,
    그 사실이 운영자에게 한 줄로 간다.
    """

    def test_미국은_못_보는_검사를_적어_둔다(self) -> None:
        빠진 = dict(u.not_checked("US"))
        assert u.REASON_SUPERVISED in 빠진
        assert u.REASON_HALTED in 빠진
        assert u.REASON_SPAC in 빠진

    def test_국내는_다_본다(self) -> None:
        assert u.not_checked("KR") == ()
        assert u.not_checked_warning("KR") is None

    def test_사유가_비어_있지_않다(self) -> None:
        """빈 문자열로 검사를 통과시키는 것을 막는다. 사유는 사람이 읽을 문장이어야 한다."""
        for 나라, 빠진 in u.NOT_CHECKED.items():
            for 이름, 사유 in 빠진:
                assert len(사유) > 20, f"{나라} 의 {이름} 사유가 너무 짧다"

    def test_못_보는_이름은_실제로_쓰는_사유_이름이다(self) -> None:
        """오타로 적어 두면 표만 있고 아무것도 안 가리킨다."""
        실제 = {
            u.REASON_SUPERVISED, u.REASON_HALTED, u.REASON_SPAC, u.REASON_NEWLY_LISTED,
            u.REASON_SMALL_CAP, u.REASON_LOW_TURNOVER, u.REASON_NOT_COMMON,
            u.REASON_NOT_STOCK, u.REASON_NO_DATA,
        }
        for 빠진 in u.NOT_CHECKED.values():
            for 이름, _ in 빠진:
                assert 이름 in 실제, f"{이름} 은 실제 제외 사유가 아니다"

    def test_운영자가_읽을_한_줄이_나온다(self) -> None:
        글 = u.not_checked_warning("US") or ""
        assert "관리종목" in 글 and "안 본다" in 글

    def test_거래정지_플래그를_채우는_수집기가_아직_없다(self) -> None:
        """`Candidate.is_halted` 는 **테스트만 지나는 가지**다 (2026-09-22 확인).

        국내 거래정지는 `section_type` 글자로 잡는다. 이 필드가 있다고 "거래정지를 보고
        있다" 고 여기면 안 된다 — 그래서 사실을 코드 주석과 여기에 함께 남긴다.
        이 검사가 깨지는 날은 수집기가 생긴 날이고, 그때 25.129 를 다시 봐야 한다.
        """
        import ast
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        채우는곳 = []
        for 길 in sorted((뿌리 / "batch").rglob("*.py")):
            for 마디 in ast.walk(ast.parse(길.read_text(encoding="utf-8"))):
                if (
                    isinstance(마디, ast.Call)
                    and any(kw.arg == "is_halted" for kw in 마디.keywords)
                ):
                    채우는곳.append(f"{길.relative_to(뿌리)}:{마디.lineno}")
        assert not 채우는곳, (
            "`is_halted` 를 채우는 곳이 생겼다. docs/infra.md 25.129 의 '못 보는 검사'"
            f" 목록을 다시 보라: {채우는곳}"
        )


class Test국내_표시_검사가_정말_걸리나:
    """관리종목·거래정지는 **소속부 글자 하나**에 매달려 있다 (docs/infra.md 25.157).

    그 열(`SECT_TP_NM`)에 "관리"·"거래정지" 가 실제로 오는지는 확인된 적이 없다.
    안 오면 두 검사는 늘 거짓이고, 제외 0건이 "그런 종목이 없다" 로 읽힌다 —
    미국에서 `not_checked_warning` 이 막으려는 오해가 국내에도 생긴다(25.131).
    """

    def _판정(self, reason: str | None) -> u.Verdict:
        return u.Verdict(included=reason is None, reason=reason, listed_days=400)

    def test_둘_다_걸리면_조용하다(self) -> None:
        verdicts = [self._판정(u.REASON_SUPERVISED), self._판정(u.REASON_HALTED), self._판정(None)]

        assert u.marker_warning("KR", verdicts, ["관리종목", "거래정지", "우량기업부"]) is None

    def test_관리종목만_걸리면_거래정지를_말한다(self) -> None:
        """예전에는 합쳐 세어 관리종목 한 건이 거래정지 공백을 가렸다 (docs/infra.md 25.612, 감사)."""
        말 = u.marker_warning("KR", [self._판정(u.REASON_SUPERVISED)], ["관리종목(소속부없음)"]) or ""

        assert u.REASON_HALTED in 말 and f"{u.REASON_SUPERVISED}·" not in 말

    def test_형제_사유는_공백을_가리지_않는다(self) -> None:
        """정리매매 한 건이 거래정지 공백을 가렸다 (docs/infra.md 25.614, 교차검증)."""
        말 = u.marker_warning("KR", [self._판정(u.REASON_SUPERVISED), self._판정(u.REASON_LIQUIDATION)], ["관리종목"]) or ""

        assert u.REASON_HALTED in 말

    def test_거래정지만_걸리면_관리종목을_말한다(self) -> None:
        말 = u.marker_warning("KR", [self._판정(u.REASON_HALTED)], ["거래정지"]) or ""

        assert u.REASON_SUPERVISED in 말 and "'거래정지'" not in 말

    def test_둘_다_0건이면_본_값을_함께_말한다(self) -> None:
        말 = u.marker_warning(
            "KR", [self._판정(None), self._판정(u.REASON_SMALL_CAP)],
            ["우량기업부", "중견기업부", "우량기업부", ""],
        )  # fmt: skip

        assert 말 and "0건" in 말
        assert "우량기업부" in 말 and "중견기업부" in 말
        assert "우량기업부, 우량기업부" not in 말, "같은 값을 두 번 적었다"
        assert "[확인필요]" in 말

    def test_소속부가_전부_비면_그렇게_말한다(self) -> None:
        """**빈 열과 '그런 종목이 없다' 는 다른 사실이다.**"""
        말 = u.marker_warning("KR", [self._판정(None)], ["", "  ", ""])

        assert 말 and "전부 비어 있음" in 말

    def test_미국에는_안_붙인다(self) -> None:
        """미국은 `not_checked_warning` 이 이미 "안 본다" 고 말한다 — 두 번 말하지 않는다."""
        assert u.marker_warning("US", [self._판정(None)], ["x"]) is None

    def test_고장이라_말하지_않는다(self) -> None:
        """정말 없는 날도 있다. 모른다고 말할 뿐이다."""
        말 = u.marker_warning("KR", [self._판정(None)], ["우량기업부"]) or ""

        assert "실패" not in 말 and "고장" not in 말

    def test_배치가_실제로_부른다(self) -> None:
        """**부르는 곳이 없으면 함수를 하나 더 만든 것일 뿐이다** (25.123 이 고친 그 모양)."""
        import ast
        from pathlib import Path

        나무 = ast.parse(
            (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "universe.py").read_text(
                encoding="utf-8"
            )
        )
        부름 = [
            n
            for n in ast.walk(나무)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "marker_warning"
        ]
        assert 부름, "아무도 marker_warning 을 부르지 않는다"

        # **결과가 `warnings` 에 담기는지까지 본다** (25.142 에서 배운 것).
        # 부르기만 하고 안 담으면 리포트에 안 실린다 — 부르는 곳이 없는 것과 같다
        받은이름 = {
            t.id
            for n in ast.walk(나무)
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "marker_warning"
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        assert 받은이름, "marker_warning 의 결과를 아무 데도 담지 않았다"

        담는다 = [
            n
            for n in ast.walk(나무)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "append"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "warnings"
            and any(isinstance(a, ast.Name) and a.id in 받은이름 for a in n.args)
        ]
        assert 담는다, "경고를 만들어 놓고 warnings 에 담지 않으면 아무도 못 본다"


class Test아는_미달이_먼저다:
    """상장일을 모르면 거래대금이 명백히 미달이어도 "데이터없음" 이었다 (docs/infra.md 25.612, 감사)."""

    def test_상장일을_몰라도_거래대금_미달이면_그_사유(self) -> None:
        v = u.judge(candidate(listed_date=None, market_cap=None, avg_turnover_20d=50_000), u.UniverseFilters.usa(), AS_OF)
        assert v.reason == u.REASON_LOW_TURNOVER and not v.included

    def test_모르는_값만_있으면_데이터없음(self) -> None:
        v = u.judge(candidate(listed_date=None), u.UniverseFilters.korea(), AS_OF)
        assert v.reason == u.REASON_NO_DATA and not v.included

    def test_아는_값이_모두_통과해도_모르는_값이_있으면_제외(self) -> None:
        v = u.judge(candidate(market_cap=None), u.UniverseFilters.korea(), AS_OF)
        assert v.reason == u.REASON_NO_DATA and not v.included
