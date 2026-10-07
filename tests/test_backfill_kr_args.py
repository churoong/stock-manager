"""국내 백필의 날짜 구간 해석 (batch/jobs/backfill_kr.py).

**왜 이 테스트가 생겼나.** 2026-09-19, D1 따라잡기가 만들어진 뒤 단 한 번도 2단계를
통과하지 못한 것을 발견했다. `d1-catchup.yml` 이 `--from` 만 주고 부르는데 `main()` 은
`--from` 과 `--to` 가 둘 다 있어야 해서 `parser.error` 로 **종료코드 2** 로 죽었다.
뒤의 재무·업종·성과·점수·신호가 전부 건너뛰어졌고, 텔레그램에는 "멈췄다" 만 갔다.
`turso-return.yml` 도 같은 모양으로 부르고 있었다 — Turso 가 풀렸어도 같은 자리에서
죽었을 것이다. 명령줄 해석은 로직이므로 테스트가 있어야 한다 (CLAUDE.md 절대 규칙).

로그를 못 읽어도 재현되는 종류의 결함이다. 오프라인에서 DB 없이 돈다.
"""

from __future__ import annotations

from datetime import date

from batch.jobs import backfill_kr as bk
from batch.jobs.backfill_kr import resolve_range

#: 고정 기준일. "오늘" 에 기대는 테스트는 날짜가 바뀌면 깨진다
오늘 = date(2026, 9, 19)


def test_시작일만_주면_오늘까지다() -> None:
    """이것이 깨져 있어서 따라잡기가 매번 죽었다."""
    assert resolve_range(오늘, None, "2025-08-15", None) == (date(2025, 8, 15), 오늘)


def test_시작일과_종료일을_모두_주면_그대로다() -> None:
    assert resolve_range(오늘, None, "2026-01-01", "2026-09-15") == (
        date(2026, 1, 1),
        date(2026, 9, 15),
    )


def test_days_는_오늘에서_거슬러_센다() -> None:
    assert resolve_range(오늘, 30, None, None) == (date(2026, 8, 20), 오늘)


def test_days_가_시작일보다_우선한다() -> None:
    """예전 동작을 그대로 지킨다. 둘 다 준 호출자가 있으면 --days 가 이긴다."""
    assert resolve_range(오늘, 10, "2020-01-01", None) == (date(2026, 9, 9), 오늘)


def test_아무것도_없으면_정하지_못한다() -> None:
    assert resolve_range(오늘, None, None, None) is None


def test_종료일만_주면_정하지_못한다() -> None:
    """시작일 없이 종료일만으로는 구간이 아니다. 조용히 오늘부터로 넘어가면 안 된다."""
    assert resolve_range(오늘, None, None, "2026-09-15") is None


class TestD1_하루_예산:
    """**2026-09-19 에 예산 가드가 있는데도 한도의 2.2배를 썼다.**

    D1 은 `meta.rows_written` 에 인덱스까지 센다. `prices` 한 행을 넣으면 표 1 +
    `UNIQUE (stock_id, date)` + `idx_prices_stock_date` + `idx_prices_date` 가 함께 쓰인다.
    그런데 받을 날 수를 **종목 수**로 나누고 있었다. 하루 한도 100,000 행짜리 DB 에
    `d1_writes` 카운터가 **220,085** 까지 올라갔고, 그 뒤 재무·업종·성과·점수·신호가 전부
    한도에 막혀 건너뛰어졌다 — 추천이 한 건도 나오지 않은 진짜 이유다 (docs/infra.md 25.20).
    """

    def test_인덱스_배수를_곱해서_나눈다(self) -> None:
        """상수를 그대로 적지 않는다 — 인덱스가 바뀌면 상수도 바뀐다. 관계만 고정한다."""
        # 남은 100,000 − 여유 32,000 = 68,000 을 (873종목 × 배수) 로 나눈 만큼
        기대 = 68_000 // (873 * bk.PRICE_ROW_D1_COST)
        assert bk.days_within_daily_budget(100_000, 32_000, 873) == 기대

    def test_배수를_안_곱하면_그만큼_많이_받는다(self) -> None:
        """곱하지 않던 옛 계산(77일). 이 차이가 그대로 한도 초과였다."""
        안곱함 = bk.days_within_budget(100_000, 32_000, 873)
        곱함 = bk.days_within_daily_budget(100_000, 32_000, 873)
        assert 안곱함 == 77
        assert 곱함 * bk.PRICE_ROW_D1_COST <= 안곱함, "배수를 곱한 쪽이 반드시 더 적게 받아야 한다"

    def test_실제로_쓰는_행이_한도_안에_든다(self) -> None:
        남은, 여유, 종목 = 100_000, 32_000, 873
        일수 = bk.days_within_daily_budget(남은, 여유, 종목)
        assert 일수 * 종목 * bk.PRICE_ROW_D1_COST <= 남은 - 여유

    def test_여유가_남은_것보다_크면_한_날도_못_받는다(self) -> None:
        assert bk.days_within_daily_budget(20_000, 32_000, 873) == 0

    def test_이미_많이_쓴_날은_적게_받는다(self) -> None:
        """9/20 아침에 이미 77,029 행을 쓴 상태였다. 그런 날은 더 받으면 안 된다."""
        assert bk.days_within_daily_budget(100_000 - 77_029, 32_000, 873) == 0


class Test도는_동안_예산_다시_재기:
    """시작할 때 센 예산은 **예측**이고, 도는 동안 재는 것은 **측정**이다.

    `PRICE_ROW_D1_COST` 는 인덱스 수에서 나온 어림이라 표가 바뀌면 틀린다. 2026-09-19·20 에
    예측만 믿다가 하루 한도의 2.2배를 썼다. 이 장치가 있으면 **상수가 틀려도 한도를 넘지 않는다**
    — 상수는 몇 날을 받을지만 정하고, 실제 멈춤은 카운터가 정한다 (docs/infra.md 25.20).
    """

    def test_여유_위면_계속_간다(self) -> None:
        assert bk.budget_stop_reason(50_000, 32_000, "KOSPI", date(2026, 9, 20)) is None

    def test_여유_아래로_내려오면_멈춘다(self) -> None:
        이유 = bk.budget_stop_reason(20_000, 32_000, "KOSPI", date(2026, 9, 20))
        assert 이유 is not None
        assert "멈춥니다" in 이유 and "내일 이어집니다" in 이유

    def test_딱_여유면_멈춘다(self) -> None:
        """경계에서 한 번 더 도는 쪽을 고르면 그만큼 여유를 파먹는다."""
        assert bk.budget_stop_reason(32_000, 32_000, "KOSPI", date(2026, 9, 20)) is not None

    def test_어느_시장_어느_날까지_받았는지_말한다(self) -> None:
        이유 = bk.budget_stop_reason(0, 32_000, "KOSDAQ", date(2026, 9, 18))
        assert "KOSDAQ" in 이유 and "2026-09-18" in 이유

    def test_재는_주기가_여유_안에_든다(self) -> None:
        """주기가 너무 길면 다시 재기 전에 여유를 다 파먹는다. 상수끼리의 관계를 고정한다."""
        기본여유 = 32_000
        최악 = bk.BUDGET_RECHECK_EVERY * 873 * bk.PRICE_ROW_D1_COST
        assert 최악 < 기본여유, f"{bk.BUDGET_RECHECK_EVERY}일마다 재면 최악 {최악:,}행 — 여유를 넘는다"
