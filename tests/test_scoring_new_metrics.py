"""2026-09-21 에 더한 지표들 (docs/factors.md 11장).

**손으로 검산한 값**으로 본다 (CLAUDE.md: "CAGR·MDD·샤프는 손으로 계산 가능한 고정
데이터로 검증 테스트 작성"). 계산식이 바뀌면 여기가 먼저 깨져야 한다.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from batch.services import scoring as s


class Test배당수익률:
    """docs/factors.md 11.1 — 현금배당총액 / 시가총액. 주당배당금이 아니다."""

    def test_손으로_검산(self) -> None:
        # 배당총액 50억, 시총 1,000억 → 5%
        assert s.dividend_yield(5_000_000_000, 100_000_000_000) == pytest.approx(0.05)

    def test_무배당은_0_이다(self) -> None:
        """**0 은 아는 값이다.** None(모름)과 다르다 — 부르는 쪽이 그 구별을 한다."""
        assert s.dividend_yield(0, 100_000_000_000) == 0.0

    def test_모르면_None(self) -> None:
        assert s.dividend_yield(None, 100_000_000_000) is None

    def test_시가총액이_없거나_0_이면_None(self) -> None:
        assert s.dividend_yield(5_000_000_000, None) is None
        assert s.dividend_yield(5_000_000_000, 0) is None

    def test_음수_배당은_None(self) -> None:
        """원자료가 이상한 것이다. 음수 수익률로 순위에 태우지 않는다."""
        assert s.dividend_yield(-1, 100_000_000_000) is None


class Test배당_연속성:
    """docs/factors.md 11.1 — 이익 안정성과 같은 모양, 같은 하한(3년)."""

    def test_다섯_해_모두_배당하면_1(self) -> None:
        assert s.dividend_years([100, 100, 100, 100, 100]) == 1.0

    def test_손으로_검산(self) -> None:
        # 다섯 해 중 셋만 배당 → 0.6
        assert s.dividend_years([100, 0, 100, 0, 100]) == pytest.approx(0.6)

    def test_한_번도_안_했으면_0(self) -> None:
        assert s.dividend_years([0, 0, 0]) == 0.0

    def test_관측하지_못한_해는_분모에서도_뺀다(self) -> None:
        """**None 은 0 원이 아니다.** 안 준 해와 모르는 해를 같게 보면 안 된다 (25.74)."""
        # 창은 앞 다섯 해다: [100, None, 0, 100, 0] → 관측 4해 중 배당 2해 = 0.5.
        # (처음에 여섯 개를 주고 0.5 를 기대했다가 이 테스트가 내 산수를 잡았다 —
        #  창 밖 한 해를 세고 있었다)
        assert s.dividend_years([100, None, 0, 100, 0]) == pytest.approx(0.5)

    def test_관측이_3년_미만이면_None(self) -> None:
        assert s.dividend_years([100, 100]) is None
        assert s.dividend_years([100, None, None, 100]) is None
        assert s.dividend_years([]) is None

    def test_다섯_해까지만_본다(self) -> None:
        """창이 커지면 옛 배당이 현재 상태를 가린다."""
        # 앞 다섯 해는 전부 배당, 그 뒤는 무배당 — 창 밖이라 1.0
        assert s.dividend_years([1, 1, 1, 1, 1, 0, 0, 0]) == 1.0


class TestMAX효과:
    """docs/factors.md 11.2 — 지난 한 달 최대 일간 수익률. Bali 외 (2011)."""

    @staticmethod
    def _계열(수익률: list[float], 시작: float = 100.0) -> list[float]:
        계열 = [시작]
        for r in 수익률:
            계열.append(계열[-1] * (1 + r))
        return 계열

    def test_손으로_검산(self) -> None:
        # 하루만 +10%, 나머지는 +1%
        계열 = self._계열([0.01] * 10 + [0.10] + [0.01] * 5)

        assert s.max_daily_return(계열) == pytest.approx(0.10)

    def test_내리기만_하면_가장_덜_나쁜_날이_답이다(self) -> None:
        계열 = self._계열([-0.01] * 12)

        assert s.max_daily_return(계열) == pytest.approx(-0.01)

    def test_창_밖의_급등은_안_본다(self) -> None:
        """**최근 21거래일**만이다. 반년 전 급등은 지금의 복권성이 아니다."""
        계열 = self._계열([0.50] + [0.001] * 30)

        assert s.max_daily_return(계열) == pytest.approx(0.001)

    def test_표본이_모자라면_None(self) -> None:
        assert s.max_daily_return(self._계열([0.01] * 8)) is None
        assert s.max_daily_return([100.0]) is None
        assert s.max_daily_return([]) is None

    def test_변동성과_다른_것을_본다(self) -> None:
        """같은 변동성이라도 한 번 크게 튄 쪽이 MAX 가 크다 — 그것이 이 지표의 요지다."""
        고르게 = self._계열([0.02, -0.02] * 8)
        한번튐 = self._계열([0.001] * 14 + [0.16] + [0.001])

        assert s.max_daily_return(한번튐) > s.max_daily_return(고르게)

    def test_창이_모멘텀_건너뛰기와_같은_숫자다(self) -> None:
        """새 숫자를 만들지 않았다 (docs/factors.md 11.2)."""
        assert s.MAX_RETURN_DAYS == s.MOMENTUM_SKIP_DAYS


class Test잔차_변동성:
    """docs/factors.md 11.3 — 시장모형 잔차의 연환산 변동성. Ang 외 (2006)."""

    def test_시장을_그대로_따라가면_잔차가_0_이다(self) -> None:
        """r_i = 2·r_m 이면 β=2, α=0, 잔차가 전부 0 이다. **손으로 아는 답.**"""
        시장 = [0.01, -0.01] * 40
        종목 = [2 * x for x in 시장]

        assert s.idio_volatility(종목, 시장) == pytest.approx(0.0, abs=1e-12)

    def test_시장과_무관한_흔들림을_잡는다(self) -> None:
        """시장이 움직이지 않는데 종목만 ±1% 로 흔들리면, 잔차 표준편차가 곧 그 흔들림이다."""
        시장 = [0.01, -0.01] * 40  # 분산이 0 이면 안 되므로 시장도 움직인다
        종목 = [0.01 if i % 2 == 0 else -0.01 for i in range(80)]
        종목 = [x + (0.02 if i % 4 == 0 else -0.02) for i, x in enumerate(종목)]

        결과 = s.idio_volatility(종목, 시장)

        assert 결과 is not None and 결과 > 0

    def test_연환산한다(self) -> None:
        """잔차 표준편차 × √252. 계수를 빠뜨리면 값이 16배 작아진다."""
        시장 = [0.01, -0.01] * 40
        # 종목 = 시장 + 주기 4 의 ±0.02 잔차. 그 잔차의 표본표준편차를 직접 센다
        잔차 = [0.02 if i % 4 == 0 else -0.02 / 3 for i in range(80)]
        종목 = [m + e for m, e in zip(시장, 잔차, strict=True)]

        결과 = s.idio_volatility(종목, 시장)
        손계산 = _표본표준편차(_잔차(종목, 시장)) * math.sqrt(252)

        assert 결과 == pytest.approx(손계산)

    def test_겹치는_날이_60개_미만이면_None(self) -> None:
        시장 = [0.01, -0.01] * 29  # 58개
        assert s.idio_volatility([0.01] * 58, 시장) is None

    def test_시장이_안_움직이면_None(self) -> None:
        """회귀가 성립하지 않는다. 0 으로 나누지 않는다."""
        assert s.idio_volatility([0.01] * 80, [0.0] * 80) is None

    def test_하한이_베타와_같다(self) -> None:
        from batch.jobs.metrics import MIN_BENCHMARK_POINTS

        assert s.IDIO_MIN_POINTS == MIN_BENCHMARK_POINTS


def _잔차(r_i: list[float], r_m: list[float]) -> list[float]:
    n = len(r_i)
    mi, mm = sum(r_i) / n, sum(r_m) / n
    var = sum((m - mm) ** 2 for m in r_m) / (n - 1)
    cov = sum((s_ - mi) * (m - mm) for s_, m in zip(r_i, r_m, strict=True)) / (n - 1)
    b = cov / var
    a = mi - b * mm
    return [s_ - (a + b * m) for s_, m in zip(r_i, r_m, strict=True)]


def _표본표준편차(xs: list[float]) -> float:
    n = len(xs)
    mean = sum(xs) / n
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1))


class Test팩터에_실제로_들어갔나:
    """정의만 하고 등록을 잊으면 계산은 되는데 점수에 안 들어간다 (25.65 가 그랬다)."""

    def test_밸류에_배당수익률이_있다(self) -> None:
        assert "dividend_yield" in {m.name for m in s.FACTOR_METRICS["value"]}

    def test_퀄리티에_배당_연속성은_아직_안_넣었다(self) -> None:
        """**절반 규칙의 분모가 늘기 때문이다** (docs/factors.md 11.1).

        퀄리티가 8개에서 9개가 되면 문턱이 4개에서 5개로 올라간다
        (`factor_from_metrics`: `len(present) * 2 < len(metrics)`).
        재무가 성긴 지금 그 한 칸에 걸려 **점수를 통째로 잃는 종목**이 생긴다.
        추천이 0건인 상황에서 문턱을 올릴 때가 아니다.

        계산 함수는 있고 테스트도 있다 — 넣기만 하면 된다.
        """
        assert "dividend_years" not in {m.name for m in s.FACTOR_METRICS["quality"]}
        assert callable(s.dividend_years), "계산 함수까지 지우면 다시 만들어야 한다"

    def test_문턱이_얼마나_올라가는지_세어_둔다(self) -> None:
        """지표를 더할 때 **이 표를 보고** 판단한다 (2026-09-21 실측)."""
        필요 = {이름: (len(ms) + 1) // 2 for 이름, ms in s.FACTOR_METRICS.items()}

        assert 필요 == {"value": 2, "quality": 4, "growth": 2, "momentum": 3, "risk": 5}

    def test_리스크에_둘_다_있고_방향이_반대다(self) -> None:
        리스크 = {m.name: m for m in s.FACTOR_METRICS["risk"]}

        assert 리스크["max_daily_return"].higher_is_better is False
        assert 리스크["idio_volatility"].higher_is_better is False

    def test_가격_계열_묶음이_MAX_를_낸다(self) -> None:
        계열 = [100.0 * (1.01**i) for i in range(30)]

        assert s.price_series_metrics(계열)["max_daily_return"] == pytest.approx(0.01)

    def test_계산_판이_올라갔다(self) -> None:
        """과거 행을 덮지 않고 새 판으로 쌓는다 (docs/factors.md 11.6)."""
        assert s.CALC_VERSION >= 4  # 5: 윈저라이즈 (docs/infra.md 25.245)


class Test문서의_개수표가_코드와_같은가:
    """**문서가 계산식의 단일 정의처다** (CLAUDE.md 기록 규칙).

    그런데 문서는 아무도 안 지킨다 — 2026-09-21 에 `docs/factors.md` 11.6 이 퀄리티를
    `8 → 9 (배당 연속성)` 이라고 적었는데 코드는 **8 그대로**였다. 넣지 않기로 하고
    표만 안 고친 것이다. 그대로 두면 다음 사람이 "왜 9개가 아니지" 를 버그로 읽고
    없는 것을 찾는다 (docs/infra.md 25.92 · 25.0 "문서가 약속하는데 지킬 주체가 없다").

    그래서 **표의 숫자를 기계가 읽어** `FACTOR_METRICS` 와 대조한다.
    """

    한글이름 = {
        "밸류": "value",
        "퀄리티": "quality",
        "성장": "growth",
        "모멘텀": "momentum",
        "리스크": "risk",
    }

    @staticmethod
    def _본문() -> str:
        return (Path(__file__).resolve().parents[1] / "docs" / "factors.md").read_text(
            encoding="utf-8"
        )

    @classmethod
    def _표의_숫자(cls, 시작: str, 끝열: int) -> dict[str, int]:
        """`시작` 제목 뒤 첫 표에서 {팩터: 숫자}. `끝열` 은 몇 번째 칸을 읽을지."""
        본문 = cls._본문()
        자리 = 본문.index(시작)
        나온것: dict[str, int] = {}
        for 줄 in 본문[자리:].splitlines():
            if 줄.startswith("#") and 시작 not in 줄:
                if 나온것:
                    break
                continue
            if not 줄.startswith("|"):
                continue
            칸 = [c.strip() for c in 줄.strip("|").split("|")]
            이름 = 칸[0].strip("*` ")
            if 이름 not in cls.한글이름 or len(칸) <= 끝열:
                continue
            숫자 = re.search(r"\d+", 칸[끝열].replace("**", ""))
            if 숫자:
                나온것[cls.한글이름[이름]] = int(숫자.group())
        return 나온것

    def test_11_6_의_후_열이_실제_지표_수다(self) -> None:
        적힌것 = self._표의_숫자("### 11.6 바뀐 것", 2)
        실제 = {이름: len(ms) for 이름, ms in s.FACTOR_METRICS.items()}

        assert len(적힌것) == 5, f"11.6 표에서 다섯 팩터를 다 읽지 못했다: {적힌것}"
        assert 적힌것 == 실제, (
            f"docs/factors.md 11.6 의 '후' 열이 {적힌것} 인데 코드는 {실제} 다.\n"
            "**문서를 고치거나 코드를 고치되, 어긋난 채로 두지 마라** (CLAUDE.md)"
        )

    def test_4장의_문턱표가_절반_규칙과_같다(self) -> None:
        """문턱은 손으로 적을 값이 아니다 — `factor_from_metrics` 가 정한다."""
        지표수 = self._표의_숫자("**팩터별 지표 수와 문턱**", 1)
        문턱 = self._표의_숫자("**팩터별 지표 수와 문턱**", 2)
        실제문턱 = {이름: (len(ms) + 1) // 2 for 이름, ms in s.FACTOR_METRICS.items()}

        assert len(지표수) == 5 and len(문턱) == 5, f"4장 표를 다 읽지 못했다: {지표수} {문턱}"
        assert 지표수 == {이름: len(ms) for 이름, ms in s.FACTOR_METRICS.items()}
        assert 문턱 == 실제문턱, (
            f"docs/factors.md 4장의 문턱이 {문턱} 인데 절반 규칙이 내는 값은 {실제문턱} 다.\n"
            "지표를 더하면 **문턱이 따라 오른다** — 표를 같이 고쳐라"
        )

    def test_3_1_과_3_5_의_지표_줄_수가_맞다(self) -> None:
        """개수표만 고치고 **지표 목록을 안 고치는** 일이 흔하다."""
        본문 = self._본문()
        for 제목, 팩터 in (("### 3.1 밸류", "value"), ("### 3.5 리스크", "risk")):
            자리 = 본문.index(제목)
            줄들: list[str] = []
            for 줄 in 본문[자리:].splitlines()[1:]:
                if 줄.startswith("|"):
                    if not re.match(r"^\|[\s\-:|]+\|$", 줄):
                        줄들.append(줄)
                elif 줄들:
                    break  # **첫 표에서 멈춘다** — 다음 절의 표까지 세면 안 된다
            항목 = 줄들[1:]  # 머리글 한 줄을 뺀다
            assert len(항목) == len(s.FACTOR_METRICS[팩터]), (
                f"{제목} 의 지표 표가 {len(항목)}줄인데 코드는 "
                f"{len(s.FACTOR_METRICS[팩터])}개다:\n  " + "\n  ".join(항목)
            )


class Test배당이_실제로_점수까지_간다:
    """**계산만 있고 안 불리면 없는 것과 같다** (docs/infra.md 25.65 가 그랬다).

    베타는 배선 하나가 빠져 한 번도 계산된 적이 없었다. 같은 일을 되풀이하지 않으려고
    **진짜 스키마를 올린 sqlite** 에 값을 넣고 `load_dividends` → `build_inputs` 를 끝까지 돌린다.
    """

    @staticmethod
    def _db():
        from batch.core import db as core_db
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        core_db.apply_migrations(mem)  # type: ignore[arg-type]
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (2, '000660', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        return mem

    #: 시험에서 쓰는 기준일. 접수일이 이보다 나중인 행은 **그날 알 수 없었다**
    기준일 = "2026-09-21"

    @staticmethod
    def _배당(mem, stock_id: int, fiscal_year: int, report_year: int, total, as_of="2026-03-10") -> None:
        mem.conn.execute(
            "INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no,"
            " cash_dividend_total, as_of_date, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, 't', 't')",
            [stock_id, fiscal_year, report_year, f"r{stock_id}{fiscal_year}{report_year}", total, as_of],
        )

    def test_최신_사업연도를_쓴다(self) -> None:
        from batch.jobs import scores

        mem = self._db()
        self._배당(mem, 1, 2024, 2025, 1_000)
        self._배당(mem, 1, 2025, 2026, 5_000)

        assert scores.load_dividends(mem, "KR", self.기준일) == {1: 5_000.0}

    def test_같은_해는_나중_보고서를_쓴다(self) -> None:
        """사업보고서가 정정되면 같은 사업연도가 두 번 들어온다."""
        from batch.jobs import scores

        mem = self._db()
        self._배당(mem, 1, 2025, 2026, 1_000)
        self._배당(mem, 1, 2025, 2027, 9_000)  # 나중 보고서

        assert scores.load_dividends(mem, "KR", self.기준일) == {1: 9_000.0}

    def test_무배당은_0_으로_들어오고_모르는_종목은_아예_없다(self) -> None:
        """**이 구별이 이 로더의 요지다** (docs/infra.md 25.74)."""
        from batch.jobs import scores

        mem = self._db()
        self._배당(mem, 1, 2025, 2026, 0)  # 살펴봤고 무배당이다

        읽은것 = scores.load_dividends(mem, "KR", self.기준일)

        assert 읽은것 == {1: 0.0}
        assert 2 not in 읽은것, "수집이 안 닿은 종목을 무배당으로 보면 안 된다"

    def test_나라를_가린다(self) -> None:
        """25.37·25.75 에서 되풀이된 모양."""
        from batch.jobs import scores

        mem = self._db()
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (9, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
        )
        self._배당(mem, 9, 2025, 2026, 7_000)

        assert scores.load_dividends(mem, "KR", self.기준일) == {}
        assert scores.load_dividends(mem, "US", self.기준일) == {9: 7_000.0}

    def test_표가_없어도_나머지_팩터를_막지_않는다(self) -> None:
        from batch.jobs import scores
        from tests.test_portfolio_job import MemClient

        assert scores.load_dividends(MemClient(), "KR", self.기준일) == {}

    def test_build_inputs_가_배당수익률을_넣는다(self) -> None:
        """끝까지 이어졌는지. 배당총액 50억 / 시총 1,000억 = 5%."""
        from batch.jobs import scores

        묶음 = scores.build_inputs(
            [{"stock_id": 1, "market": "KOSPI", "sector": None, "market_cap": 100_000_000_000}],
            {}, {}, None,
            dividends={1: 5_000_000_000},
        )

        assert 묶음[0].metrics["dividend_yield"] == pytest.approx(0.05)

    def test_배당을_안_넘기면_None_이다(self) -> None:
        """예전 호출 모양(인자 없이)도 그대로 돈다 — 백테스트 경로가 아직 그렇다."""
        from batch.jobs import scores

        묶음 = scores.build_inputs(
            [{"stock_id": 1, "market": "KOSPI", "sector": None, "market_cap": 100_000_000_000}],
            {}, {}, None,
        )

        assert 묶음[0].metrics["dividend_yield"] is None

    def test_run_이_로더를_부른다(self) -> None:
        """배선이 끊기면 위 테스트는 다 통과하면서 운영에서만 빈다 (25.65 가 그 모양이었다)."""
        from pathlib import Path

        본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "scores.py").read_text(
            encoding="utf-8"
        )
        # `)` 로 자르면 안쪽 괄호에서 먼저 끊긴다 — **블록의 끝**으로 자른다
        부르는곳 = 본문.split("inputs = build_inputs(", 1)[1].split("\n        )", 1)[0]

        # 읽기 실패를 모으는 네 번째 인자가 붙었다 (25.221) — 앞 셋이 이 순서로 가는지만 본다.
        # 25.557 부터 배당은 미리 읽어 미국 무배당을 채운 뒤 `dividends` 로 넘긴다 — 그 배선까지 본다
        앞 = 본문.split("inputs = build_inputs(", 1)[0]
        assert "dividends = load_dividends(client, country, as_of" in 앞 and "dividends," in 부르는곳, (
            "build_inputs 에 배당을 안 넘긴다 — 계산은 되는데 점수에는 안 들어간다"
        )


class Test배당은_그날_알_수_있었던_것만:
    """**live look-ahead 였다** (2026-09-21, docs/infra.md 25.92).

    `migrations/0013_stock_dividends.sql` 이 `as_of_date` 에 "접수일. **이 날부터 알 수
    있었다**" 라고 적어 두었는데 처음 쓸 때 그것을 빠뜨렸다.

    가정이 아니다 — `jobs/daily.py` 가 `scores.run(market, as_of=trade_date)` 를 부르고
    `trade_date` 는 **전 거래일**이라(batch/core/calendar.py), T일 아침 배치가 T-1 행을
    쓰면서 **T일에 접수된 배당**을 쓸 수 있었다. 수동 `--as-of` 만의 문제가 아니다.
    """

    @staticmethod
    def _db():
        from batch.core import db as core_db
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        core_db.apply_migrations(mem)  # type: ignore[arg-type]
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        return mem

    @staticmethod
    def _넣기(mem, fiscal_year, report_year, total, as_of):
        mem.conn.execute(
            "INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no,"
            " cash_dividend_total, as_of_date, source, fetched_at) VALUES (1, ?, ?, ?, ?, ?, 't', 't')",
            [fiscal_year, report_year, f"r{fiscal_year}{report_year}", total, as_of],
        )

    def test_아직_접수되지_않은_배당은_안_쓴다(self) -> None:
        from batch.jobs import scores

        mem = self._db()
        self._넣기(mem, 2025, 2026, 9_999, "2026-03-10")

        assert scores.load_dividends(mem, "KR", "2026-03-09") == {}, "하루 전에는 몰랐어야 한다"
        assert scores.load_dividends(mem, "KR", "2026-03-10") == {1: 9_999.0}, "접수일 당일부터 안다"

    def test_그날_알_수_있었던_최신_연도를_쓴다(self) -> None:
        """나중 사업연도가 **아직 접수 전**이면 그 전 연도를 써야 한다."""
        from batch.jobs import scores

        mem = self._db()
        self._넣기(mem, 2024, 2025, 1_000, "2025-03-10")
        self._넣기(mem, 2025, 2026, 5_000, "2026-03-10")

        assert scores.load_dividends(mem, "KR", "2026-01-01") == {1: 1_000.0}
        assert scores.load_dividends(mem, "KR", "2026-06-01") == {1: 5_000.0}

    def test_같은_해의_정정도_접수일을_본다(self) -> None:
        """정정 보고서가 나중에 접수되면, 그 전에는 **원래 값**을 봐야 한다."""
        from batch.jobs import scores

        mem = self._db()
        self._넣기(mem, 2025, 2026, 1_000, "2026-03-10")
        self._넣기(mem, 2025, 2027, 9_000, "2027-03-10")  # 정정

        assert scores.load_dividends(mem, "KR", "2026-06-01") == {1: 1_000.0}
        assert scores.load_dividends(mem, "KR", "2027-06-01") == {1: 9_000.0}

    def test_접수일을_모르는_행은_안_쓴다(self) -> None:
        """**비는 것이 틀린 것보다 낫다.** 언제부터 알 수 있었는지 모르는 값을
        과거 시점에 놓으면 그것이 곧 look-ahead 다 (CLAUDE.md: 없는 숫자를 만들지 않는다)."""
        from batch.jobs import scores

        mem = self._db()
        self._넣기(mem, 2025, 2026, 5_000, None)

        assert scores.load_dividends(mem, "KR", "2030-01-01") == {}

    def test_run_이_as_of_를_넘긴다(self) -> None:
        """넘기지 않으면 기본값이 오늘이 되어 옛 기준일 재계산이 미래를 본다."""
        from pathlib import Path

        본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "scores.py").read_text(
            encoding="utf-8"
        )

        import inspect

        from batch.jobs import scores as job

        assert "load_dividends(client, country, as_of" in 본문
        # 선언은 글자가 아니라 **서명**으로 본다 — 인자가 늘어도(25.221) as_of 가 필수인지가 요점이다
        as_of = inspect.signature(job.load_dividends).parameters["as_of"]
        assert as_of.default is inspect.Parameter.empty, "as_of 가 선택이면 빠뜨리기 쉽다 — 필수로 둔다"


class Test백테스트도_같은_배당을_본다:
    """**적재와 백테스트의 계산식은 하나다** (docs/factors.md 10.1·11.6, CLAUDE.md).

    2026-09-21 에 배당수익률을 적재에만 넣어 그 약속을 깼다(docs/infra.md 25.92).
    결과가 조용했다 — 밸류 문턱은 3지표일 때도 4지표일 때도 2라 점수가 사라지지 않고,
    운영은 z 4개 평균, 백테스트는 z 3개 평균으로 **다른 수**를 냈다.

    더 나쁜 것은 **검증이 막힌다**는 점이다. factors.md 11.0 이 "넣기 전·후를 백테스트의
    팩터별 단일 전략으로 비교" 하라고 정했는데, 백테스트가 배당을 본 적이 없으므로
    v3 과 v4 가 **완전히 같은 수**를 낸다 — "배당은 효과가 없다" 로 읽힌다.
    """

    @staticmethod
    def _이력():
        # (접수일, 사업연도, 현금배당총액)
        return [
            ("2025-03-10", 2024, 1_000.0),
            ("2026-03-10", 2025, 5_000.0),
            ("2027-03-10", 2025, 9_000.0),  # 같은 해 정정
        ]

    def test_그때_알_수_있었던_것만_쓴다(self) -> None:
        from batch.jobs import backtest as bj

        이력 = self._이력()

        assert bj.pit_dividend(이력, "2025-01-01") is None, "첫 접수 전에는 모른다"
        assert bj.pit_dividend(이력, "2025-06-01") == 1_000.0
        assert bj.pit_dividend(이력, "2026-06-01") == 5_000.0
        assert bj.pit_dividend(이력, "2027-06-01") == 9_000.0, "정정이 접수된 뒤에는 그것을 쓴다"

    def test_접수일_당일부터_안다(self) -> None:
        from batch.jobs import backtest as bj

        assert bj.pit_dividend([("2026-03-10", 2025, 5_000.0)], "2026-03-09") is None
        assert bj.pit_dividend([("2026-03-10", 2025, 5_000.0)], "2026-03-10") == 5_000.0

    def test_이력이_없으면_None(self) -> None:
        """**0(무배당)과 다르다.** 모르는 것을 0 으로 두면 무배당 회사로 보인다 (25.74)."""
        from batch.jobs import backtest as bj

        assert bj.pit_dividend([], "2026-06-01") is None

    def test_build_pit_inputs_가_배당수익률을_넣는다(self) -> None:
        from batch.jobs import backtest as bj
        from batch.services import backtest as bt

        view = bt.PriceView({1: {"2026-06-01": 100.0, "2026-06-02": 110.0}}, "2026-06-02")
        묶음 = bj.build_pit_inputs(
            [{"stock_id": 1, "market": "KOSPI", "sector": None, "listed_shares": 1_000}],
            {}, view, {1: self._이력()},
        )

        # 시총 = 마지막 종가 110 × 1,000주 = 110,000. 배당 5,000 → 4.545%
        assert 묶음[0].metrics["dividend_yield"] == pytest.approx(5_000 / 110_000)

    def test_배당을_안_넘기면_None_이다(self) -> None:
        """옛 호출 모양도 그대로 돈다 — 다만 그때는 밸류가 3지표다."""
        from batch.jobs import backtest as bj
        from batch.services import backtest as bt

        view = bt.PriceView({1: {"2026-06-01": 100.0}}, "2026-06-01")
        묶음 = bj.build_pit_inputs(
            [{"stock_id": 1, "market": "KOSPI", "sector": None, "listed_shares": 1_000}], {}, view
        )

        assert 묶음[0].metrics["dividend_yield"] is None

    def test_run_이_이력을_읽어_전략에_넘긴다(self) -> None:
        """배선이 끊기면 위가 다 통과하면서 운영에서만 빈다 (25.65 가 그 모양이었다)."""
        from pathlib import Path

        본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "backtest.py").read_text(
            encoding="utf-8"
        )

        assert "pit_divs = load_pit_dividends(client, country)" in 본문
        assert 본문.count("pit_divs") >= 4, "만들기만 하고 전략에 안 넘기면 소용없다"

    def test_접수일을_모르는_행은_싣지_않는다(self) -> None:
        """언제부터 알 수 있었는지 모르는 값을 과거에 놓으면 그것이 look-ahead 다."""
        from pathlib import Path

        본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "backtest.py").read_text(
            encoding="utf-8"
        )
        질의 = 본문.split("def load_pit_dividends", 1)[1].split("def pit_dividend", 1)[0]

        assert "d.as_of_date IS NOT NULL" in 질의


class TestSUE:
    """docs/factors.md 11.4 — 분기 실적 서프라이즈. Bernard & Thomas (1989).

    **오늘은 늘 None 이다.** 분기 재무를 받지 않는다. 계산식과 테스트를 먼저 둔다.
    """

    #: 12분기(3년). 전년동기 대비 변화가 정확히 8개 나온다
    열두분기 = [100.0, 110.0, 120.0, 130.0, 105.0, 116.0, 126.0, 137.0, 115.0, 120.0, 140.0, 160.0]

    def test_손으로_검산한_값(self) -> None:
        """변화 [5,6,6,7,10,4,14,23], 표본표준편차 6.368168…, 마지막 변화 23."""
        변화 = [5.0, 6.0, 6.0, 7.0, 10.0, 4.0, 14.0, 23.0]
        기대 = 23.0 / _표본표준편차(변화)

        assert 기대 == pytest.approx(3.6117134161, abs=1e-9)
        assert s.sue(self.열두분기, market_cap=1000.0) == pytest.approx(기대)

    def test_시가총액은_약분돼_사라진다(self) -> None:
        """**없는 효과를 있다고 적지 않는다** — 분자와 분모가 같은 값으로 나뉜다."""
        하나 = s.sue(self.열두분기, market_cap=1000.0)
        다른 = s.sue(self.열두분기, market_cap=7_777_777.0)

        assert 하나 == pytest.approx(다른)

    def test_시가총액이_0이하면_None(self) -> None:
        assert s.sue(self.열두분기, market_cap=0.0) is None
        assert s.sue(self.열두분기, market_cap=-1.0) is None
        assert s.sue(self.열두분기, market_cap=None) is None

    def test_열한분기면_None(self) -> None:
        """변화가 7개뿐이다. **표본이 모자라면 판단하지 않는다**."""
        assert s.sue(self.열두분기[1:], market_cap=1000.0) is None

    def test_중간에_빈_분기가_있으면_None(self) -> None:
        섞임 = list(self.열두분기)
        섞임[2] = None

        assert s.sue(섞임, market_cap=1000.0) is None, "0 으로 채우면 없는 안정성을 지어낸다"

    def test_당분기를_모르면_None(self) -> None:
        섞임 = list(self.열두분기)
        섞임[-1] = None

        assert s.sue(섞임, market_cap=1000.0) is None, "분자가 없다"

    def test_한_번도_안_변했으면_None(self) -> None:
        """표준편차 0 에서 '몇 표준편차' 는 뜻이 없다."""
        한결같이 = [100.0, 110.0, 120.0, 130.0] * 3

        assert s.sue(한결같이, market_cap=1000.0) is None

    def test_계절성을_본다(self) -> None:
        """**직전 분기가 아니라 4분기 전**과 견준다.

        분기마다 모양이 뚜렷한(1분기 비수기) 회사다. 직전 분기와 견주면 해마다
        같은 계절 변동이 실적 변화로 읽혀 서프라이즈가 요동친다.
        """
        계절 = [10.0, 80.0, 90.0, 100.0] * 3

        assert s.yoy_changes(계절) == [0.0] * 8, "해마다 똑같으면 변화가 0 이다"
        assert s.sue(계절, market_cap=1000.0) is None, "변화가 전부 0 이라 표준편차도 0"

    def test_변화의_자리가_유지된다(self) -> None:
        """**빠진 것을 솎아 내면 뒤가 앞으로 당겨진다** — '최근 8개' 가 3년 전을 섞는다."""
        섞임 = list(self.열두분기)
        섞임[5] = None
        변화 = s.yoy_changes(섞임)

        assert len(변화) == 8
        assert 변화[1] is None, "5번 분기가 비면 1번 변화가 빈다"
        assert 변화[5] is None, "그리고 4분기 뒤의 변화도 빈다"

    def test_짧으면_빈_목록(self) -> None:
        assert s.yoy_changes([1.0, 2.0, 3.0, 4.0]) == []
        assert s.yoy_changes([]) == []

    def test_수축도_음수로_나온다(self) -> None:
        """실적이 꺾이면 **음수 서프라이즈**다. 부호를 잃지 않는다."""
        꺾임 = [100.0, 110.0, 120.0, 130.0, 105.0, 116.0, 126.0, 137.0, 115.0, 120.0, 140.0, 100.0]

        값 = s.sue(꺾임, market_cap=1000.0)

        assert 값 is not None and 값 < 0


class TestSUE_는_아직_안_쓴다:
    """**값을 만들 수 없는 것을 잇지 않는다** (docs/infra.md 25.92).

    이것이 "정의만 하고 안 이었다" 와 다른 점은 **일부러 그랬고 그 사실이 테스트로
    고정돼 있다**는 것이다.

    2026-09-28: 분기 수집은 이미 켜져 있었다(25.434). 이을지는 "종목선정 기법 발굴 루프"(CLAUDE.md)의
    검증을 거쳐 **백테스트 전략으로 먼저** 넣는다. 그때까지 팩터에는 등록하지 않는다.
    """

    def test_팩터에_등록하지_않았다(self) -> None:
        등록 = {m.name for ms in s.FACTOR_METRICS.values() for m in ms}

        assert "sue" not in 등록, (
            "분기 재무가 없어 값이 늘 None 이다. 등록하면 **분모만 커져** 절반 규칙의"
            " 문턱을 올리고 모든 종목의 missing_fields 에 영구히 들어간다"
        )

    def test_올해_분기는_기한이_지난_것만_먼저_받는다(self) -> None:
        """글자가 아니라 **수집 계획 함수의 결과**를 본다 (docs/infra.md 25.434·25.438·25.455).

        25.438 까지는 기본 계획이 작년부터라 올해 분기를 받지 않았다 — SUE 입력이 세 분기 묵었다.
        이제 올해의 분기·반기 중 **법정 기한(분기 말 + 45일)이 지난 것**을 먼저 받는다."""
        from datetime import date

        from batch.jobs import financials

        plan = financials.collection_plan(None, 5, None, date(2026, 9, 28))
        assert plan[:2] == [(2026, "11012"), (2026, "11013")]  # 반기(8/14)·1분기(5/15) 지남, 3분기(11/14) 전
        assert (2026, "11014") not in plan
        # 7월부터는 올해 사업보고서(3·6월 결산 회사 몫)도 받는다 (25.665, 교차검증)
        assert (2026, "11011") in plan
        assert (2026, "11011") not in financials.collection_plan(None, 5, None, date(2026, 6, 30))
        assert {r for y, r in plan if y == 2025} == {"11011", "11012", "11013", "11014"}
        assert min(y for y, _ in plan) == 2021  # 작년부터 5해

    def test_기한_당일은_아직_안_받는다(self) -> None:
        from datetime import date

        from batch.jobs import financials

        assert (2026, "11012") not in financials.collection_plan(None, 1, None, date(2026, 8, 14))
        assert (2026, "11012") in financials.collection_plan(None, 1, None, date(2026, 8, 15))
        assert not [p for p in financials.collection_plan(None, 1, None, date(2026, 3, 1)) if p[0] == 2026]

    def test_기한이_주말이면_다음_영업일까지_기다린다(self) -> None:
        """2026-11-14 는 토요일 — 실제 기한은 11/16(월)이다 (docs/infra.md 25.457, 교차검증)."""
        from datetime import date

        from batch.jobs import financials

        평일만 = lambda d: d.weekday() < 5  # noqa: E731
        assert financials.deadline_of(2026, "11014", 평일만) == date(2026, 11, 16)
        assert (2026, "11014") not in financials.collection_plan(None, 1, None, date(2026, 11, 16), 평일만)
        assert (2026, "11014") in financials.collection_plan(None, 1, None, date(2026, 11, 17), 평일만)
        # 공휴일도 영업일이 아니다(거래소 달력): 2027-05-15 토 → 5/17 월
        assert financials.deadline_of(2027, "11013", financials._krx_business_day) >= date(2027, 5, 17)

    def test_보고서만_줘도_올해는_끼지_않는다(self) -> None:
        """25.455 첫 판은 `--report 11012` 에 올해 반기를 더했다 — 문서의 "그 곱만" 과 달랐다(교차검증)."""
        from datetime import date

        from batch.jobs import financials

        assert financials.collection_plan(None, 2, "11012", date(2026, 9, 28)) == [(2025, "11012"), (2024, "11012")]

    def test_연도나_보고서를_주면_그_곱만(self) -> None:
        """따라잡기(`--report 11011`)는 올해 분기를 부르지 않는다 — 쓰기 예산이 그대로다."""
        from datetime import date

        from batch.jobs import financials

        오늘 = date(2026, 9, 28)
        assert financials.collection_plan(None, 3, "11011", 오늘) == [(2025, "11011"), (2024, "11011"), (2023, "11011")]
        assert financials.collection_plan(2024, 5, None, 오늘) == [(2024, r) for r in financials.dart.REPORT_CODES]

    def test_계산_함수는_남아_있다(self) -> None:
        assert callable(s.sue) and callable(s.yoy_changes)


class Test잔차_변동성의_창:
    """**같은 이름의 지표가 두 경로에서 다른 추정량이었다** (docs/infra.md 25.99).

    문서(11.3)가 하한(60)만 적고 **위를 안 적어서**, 적재는 274일(모멘텀 창을 얻어 쓴 값)·
    백테스트는 **전 구간**(최대 5년)으로 쟀다. 문서가 안 정한 것을 코드 두 벌이 각자 정했다.

    창을 자르는 자리를 **계산 함수 안**에 둬서, 부르는 쪽이 무엇을 넘기든 같은 창이 되게 했다.
    """

    @staticmethod
    def _계열(n: int) -> tuple[list[float], list[float]]:
        """앞부분만 흔들리는 종목. 창이 길면 그 흔들림이 섞여 값이 커진다."""
        시장 = [0.01 if i % 2 == 0 else -0.01 for i in range(n)]
        종목 = [
            m + (0.05 if i % 3 == 0 else -0.025)   # 옛 구간: 크게 흔들린다
            if i < n - s.IDIO_WINDOW_DAYS
            else m + (0.001 if i % 3 == 0 else -0.0005)  # 최근 창: 잔잔하다
            for i, m in enumerate(시장)
        ]
        return 종목, 시장

    def test_넘긴_길이와_무관하게_같은_값이_나온다(self) -> None:
        """**이것이 요지다.** 적재가 274일, 백테스트가 5년을 넘겨도 답이 같아야 한다."""
        긴종목, 긴시장 = self._계열(s.IDIO_WINDOW_DAYS + 900)
        짧은종목, 짧은시장 = 긴종목[-s.IDIO_WINDOW_DAYS :], 긴시장[-s.IDIO_WINDOW_DAYS :]

        assert s.idio_volatility(긴종목, 긴시장) == pytest.approx(
            s.idio_volatility(짧은종목, 짧은시장)
        )

    def test_창_밖의_흔들림을_안_본다(self) -> None:
        """자르지 않으면 옛 구간의 큰 흔들림이 섞여 값이 훨씬 커진다."""
        종목, 시장 = self._계열(s.IDIO_WINDOW_DAYS + 900)

        잘라서 = s.idio_volatility(종목, 시장)
        통째로 = _잔차변동성_창없이(종목, 시장)

        assert 잘라서 is not None and 통째로 is not None
        assert 통째로 > 잘라서 * 2, "시험 자료가 두 값을 갈라 놓지 못했다"

    def test_창보다_짧아도_하한만_넘으면_낸다(self) -> None:
        """창은 **상한**이다. 하한은 `IDIO_MIN_POINTS` 가 따로 본다."""
        n = s.IDIO_MIN_POINTS + 5
        시장 = [0.01 if i % 2 == 0 else -0.01 for i in range(n)]
        종목 = [m * 2 + (0.002 if i % 3 == 0 else -0.001) for i, m in enumerate(시장)]

        assert s.idio_volatility(종목, 시장) is not None

    def test_창이_1년이다(self) -> None:
        """이 저장소의 1년 창 관례와 같은 숫자여야 한다 (docs/factors.md 11.3)."""
        from batch.services import metrics as mt

        assert s.IDIO_WINDOW_DAYS == mt.TRADING_DAYS_PER_YEAR
        assert s.IDIO_WINDOW_DAYS > s.IDIO_MIN_POINTS, "창이 하한보다 짧으면 늘 NULL 이다"

    def test_자르는_자리가_계산_함수_안이다(self) -> None:
        """**부르는 쪽마다 자르게 두면 갈라진다** — 실제로 그랬다."""
        import inspect

        원본 = inspect.getsource(s.idio_volatility)
        assert "IDIO_WINDOW_DAYS" in 원본, (
            "창 자르기가 계산 함수 밖으로 나갔다. 적재와 백테스트가 또 갈라진다"
        )

    def test_적재도_백테스트도_따로_안_자른다(self) -> None:
        import inspect

        from batch.jobs import backtest as bt_job
        from batch.jobs import scores as sc_job

        for 모듈 in (sc_job, bt_job):
            원본 = inspect.getsource(모듈)
            assert "IDIO_WINDOW_DAYS" not in 원본, (
                f"{모듈.__name__} 이 창을 따로 자르고 있다 — 자르는 곳은 한 군데여야 한다"
            )


def _잔차변동성_창없이(종목: list[float], 시장: list[float]) -> float | None:
    """창을 안 자른 옛 계산. **고치기 전 상태를 고정해 둔다.**"""
    n = min(len(종목), len(시장))
    if n < s.IDIO_MIN_POINTS:
        return None
    잔차 = _잔차(종목[:n], 시장[:n])
    return _표본표준편차(잔차) * math.sqrt(252)


class Test계열_지표도_구멍을_안_센다:
    """`services/metrics` 는 9/22 에 고쳤는데 `services/scoring` 은 남아 있었다
    (docs/infra.md 25.102 → 25.103).

    그쪽 계열 함수들이 **날짜를 안 받아서** 같은 일을 못 했다. 그래서 같은 날 같은 종목의
    "하루치 수익률" 이 두 모듈에서 다른 뜻이었다 — `volatility_ann`(metrics)은 구멍을 빼고,
    `momentum_vol_adjusted`(scoring)은 넣어서 셌다.
    """

    @staticmethod
    def _자료(구멍: bool) -> tuple[list[str], list[float]]:
        """274일치. `구멍` 이면 창 한가운데를 20일 비우고 그만큼 튄 값을 놓는다."""
        from datetime import date, timedelta

        시작 = date(2026, 1, 1)
        날짜, 종가 = [], []
        하루 = 0
        for i in range(300):
            if 구멍 and i == 150:
                하루 += 20          # 20일 쉰다
                날짜.append((시작 + timedelta(days=하루)).isoformat())
                종가.append(종가[-1] * 1.40)   # 재개하며 40% 뛴다
                continue
            하루 += 1
            날짜.append((시작 + timedelta(days=하루)).isoformat())
            종가.append(100.0 * (1.0005**i) + (0.3 if i % 2 else 0.0))
        return 날짜, 종가

    def test_MAX_가_구멍의_한_점에_안_끌려간다(self) -> None:
        """**9/21 에 더한 지표가 하필 이 왜곡에 가장 민감하다** — 그 한 점이 그대로 최댓값이다."""
        날짜, 종가 = self._자료(구멍=True)
        # 구멍을 MAX 창(최근 21일) 안으로 옮긴다
        날짜, 종가 = 날짜[-25:], 종가[-25:]
        날짜[-3] = "2026-11-01"
        날짜[-2] = "2026-11-25"   # 24일 구멍
        종가[-2] = 종가[-3] * 1.5

        assert s.max_daily_return(종가) == pytest.approx(0.5), "날짜를 안 주면 옛 모습 그대로"
        끌린것 = s.max_daily_return(종가, 날짜)
        assert 끌린것 is not None and 끌린것 < 0.1, "날짜를 주면 그 한 점을 안 센다"

    def test_변동성_조정_모멘텀도_안_끌려간다(self) -> None:
        날짜, 종가 = self._자료(구멍=True)

        날짜없이 = s.momentum_vol_adjusted(종가)
        날짜주고 = s.momentum_vol_adjusted(종가, 날짜)

        assert 날짜없이 is not None and 날짜주고 is not None
        assert 날짜주고 > 날짜없이, "구멍의 한 점이 분모(변동성)를 부풀리고 있었다"

    def test_꾸준함도_그_한_점을_안_센다(self) -> None:
        날짜, 종가 = self._자료(구멍=True)

        assert s.momentum_consistency(종가, 날짜) != s.momentum_consistency(종가)

    def test_구멍이_없으면_값이_그대로다(self) -> None:
        """**정상 종목은 아무것도 안 바뀐다.** 이 변경이 조용한지 본다."""
        날짜, 종가 = self._자료(구멍=False)

        for 함수 in (s.momentum_consistency, s.momentum_vol_adjusted, s.max_daily_return):
            assert 함수(종가, 날짜) == pytest.approx(함수(종가)), 함수.__name__

    def test_주말은_안_걸린다(self) -> None:
        """금→월은 3일이다. **정상 휴장은 절대 안 걸린다.**"""
        날짜 = ["2026-09-18", "2026-09-21"]
        종가 = [100.0, 110.0]

        assert s._daily_returns(종가, 날짜) == [pytest.approx(0.1)]

    def test_문턱을_metrics_에서_가져온다(self) -> None:
        """**새 숫자를 만들지 않았다** (docs/infra.md 25.0)."""
        import inspect

        from batch.services import metrics as mt

        원본 = inspect.getsource(s._daily_returns)
        assert "mt.MAX_SESSION_GAP_DAYS" in 원본
        assert mt.MAX_SESSION_GAP_DAYS == 11

    def test_날짜와_종가의_길이가_다르면_막는다(self) -> None:
        """조용히 어긋나면 **엉뚱한 쌍**을 구멍으로 본다. 터지는 편이 낫다."""
        with pytest.raises(ValueError):
            s._daily_returns([100.0, 110.0, 120.0], ["2026-09-18", "2026-09-21"])

    def test_읽을_수_없는_날짜는_막지_않는다(self) -> None:
        """날짜가 이상해도 **수익률을 잃지 않는다** — 모르면 그냥 센다."""
        assert s._daily_returns([100.0, 110.0], ["없음", "이상함"]) == [pytest.approx(0.1)]

    def test_두_호출부가_날짜를_넘긴다(self) -> None:
        """**계산만 있고 안 불리면 없는 것과 같다** (25.65·25.92)."""
        import inspect

        from batch.jobs import backtest as bt_job
        from batch.jobs import scores as sc_job

        assert "price_series_metrics(closes_series, values_series or None, dates_series)" in (
            inspect.getsource(sc_job.build_inputs)
        )
        assert "[d for d, _c in closes]" in inspect.getsource(bt_job.build_pit_inputs)


def test_잔차_변동성의_수익률도_11일_넘게_벌어진_쌍을_버린다() -> None:
    """거래정지 뒤 재개 하루는 하루치가 아니다 (docs/infra.md 25.228). metrics.paired_returns 와 같은 규칙."""
    from batch.services import scoring as sc

    종목 = {"2026-01-02": 100.0, "2026-01-05": 101.0, "2026-02-20": 150.0, "2026-02-23": 151.0}
    지수 = {d: 1000.0 + i for i, d in enumerate(sorted(종목))}

    s_ret, m_ret = sc.aligned_returns(종목, 지수)

    assert len(s_ret) == len(m_ret) == 2  # 1/5→2/20(46일) 쌍은 빠진다
    assert max(s_ret) < 0.02
