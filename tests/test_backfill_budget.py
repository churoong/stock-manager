"""시세 백필의 **예산 계산과 날짜 고르기** (docs/infra.md 25.63).

따라잡기의 4단계다. 하루 한 번뿐인 실행에서 이 계산이 틀리면 그날은 통째로 헛돈다.
`--budget-reserve` 가 있으면 "오늘 남은 D1 쓰기" 로 받을 거래일 수를 정하는데,
그 수가 **0 이 되는 날**이 정상적으로 생긴다 — 앞 단계(유니버스·재무)가 예산을 다 쓴 날이다.

2026-09-21 에 그 0 을 두 곳이 서로 다르게 다루고 있었다.

- `pick_days` 는 0 을 가려 **아무 날도 안 받았다** (맞다)
- 어림값을 내는 쪽은 `sessions[-max_days:] if max_days else sessions` 라 **전부**를 셌다.
  `days[-0:]` 은 빈 목록이 아니라 전체다

그래서 "오늘은 한 날도 안 받는다" 가 "400일치를 받는다" 로 어림되었다. 그 어림값이
남은 월 예산보다 크면 실행이 통째로 멈춘다(hard stop). 게다가 그 멈춤이 종료코드 1 이라
따라잡기의 **뒤 단계(성과·점수·신호·감시)까지 건너뛰어졌다.**
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from batch.jobs import backfill_kr as bf


def 날들(n: int) -> list[date]:
    시작 = date(2026, 1, 5)
    return [시작 + timedelta(days=i) for i in range(n)]


class Test받을_날_고르기:
    """`limit_days` 하나로 모았다. 거를 때와 어림할 때가 같은 답을 봐야 한다."""

    def test_None_이면_전부(self) -> None:
        assert bf.limit_days(날들(5), None) == 날들(5)

    def test_0_이면_하나도_안_받는다(self) -> None:
        # `days[-0:]` 은 전부다. 이 한 줄이 이 파일이 있는 이유다
        assert bf.limit_days(날들(5), 0) == []

    def test_음수도_하나도_안_받는다(self) -> None:
        assert bf.limit_days(날들(5), -3) == []

    def test_뒤에서부터_센다(self) -> None:
        """최근의 빠진 날부터 받는다 (infra 25.8). 앞에서 세면 몇 달 전 시세만 보인다."""
        assert bf.limit_days(날들(5), 2) == 날들(5)[-2:]

    def test_가진_것보다_많이_달라면_가진_만큼(self) -> None:
        assert bf.limit_days(날들(3), 99) == 날들(3)

    def test_원본을_건드리지_않는다(self) -> None:
        원본 = 날들(4)
        bf.limit_days(원본, 2)

        assert 원본 == 날들(4)


class Test거르기와_어림이_같은_날을_본다:
    """두 곳이 어긋났던 것이 결함의 본체였다."""

    @pytest.mark.parametrize("max_days", [None, 0, 1, 3, 10])
    def test_저장된_날이_없으면_같다(self, max_days: int | None) -> None:
        sessions = 날들(6)

        assert bf.pick_days(sessions, set(), max_days) == bf.limit_days(sessions, max_days)

    def test_저장된_날을_뺀_뒤에_센다(self) -> None:
        sessions = 날들(6)
        저장됨 = {sessions[0].isoformat(), sessions[1].isoformat()}

        골랐다 = bf.pick_days(sessions, 저장됨, 2)

        assert 골랐다 == sessions[-2:]


class Test하루_예산으로_날_수_정하기:
    def test_여유를_빼고_나눈다(self) -> None:
        # 남은 100,000 − 여유 32,000 = 68,000 ÷ (880종목 × 3) = 25일
        assert bf.days_within_daily_budget(100_000, 32_000, 880) == 68_000 // (880 * 3)

    def test_여유보다_적게_남으면_0(self) -> None:
        assert bf.days_within_daily_budget(30_000, 32_000, 880) == 0

    def test_딱_여유만큼_남아도_0(self) -> None:
        assert bf.days_within_daily_budget(32_000, 32_000, 880) == 0

    def test_종목이_0이면_0(self) -> None:
        # 유니버스가 비었을 때 0으로 나누지 않는다
        assert bf.days_within_daily_budget(100_000, 0, 0) == 0

    def test_인덱스_몫을_센다(self) -> None:
        """D1 은 인덱스 쓰기도 `rows_written` 에 넣는다. 종목 수로 나누면 2.2배를 넘긴다(25.20)."""
        종목 = 100
        assert bf.days_within_daily_budget(100_000, 0, 종목) == 100_000 // (종목 * bf.PRICE_ROW_D1_COST)


class Test어림값:
    """부풀리면 실행이 통째로 멈춘다. 틀리는 방향이 중요하다."""

    def test_받을_종목만_센다(self) -> None:
        keep = set(range(880))

        assert bf.estimate_rows(None, 날들(10), False, keep) == 880 * 10  # type: ignore[arg-type]

    def test_한_날도_안_받으면_0이다(self) -> None:
        keep = set(range(880))

        assert bf.estimate_rows(None, bf.limit_days(날들(270), 0), False, keep) == 0  # type: ignore[arg-type]

    def test_전종목으로_세면_세_배가_된다(self) -> None:
        """`keep` 을 안 넘기면 한국거래소가 주는 전 종목으로 센다 — 고치기 전 모습이다."""
        keep = set(range(880))
        전종목 = bf.estimate_rows(None, 날들(10), False, set(range(2_760)))  # type: ignore[arg-type]

        assert 전종목 > bf.estimate_rows(None, 날들(10), False, keep) * 3 - 1  # type: ignore[arg-type]


def test_예산_때문에_멈추는_것은_고장이_아니다() -> None:
    """종료코드 0 이어야 따라잡기의 뒤 단계가 돈다 (docs/infra.md 25.6·25.63).

    소스를 읽어 확인한다 — 여기서 DB 를 띄울 수는 없다.
    """
    글 = Path(bf.__file__).read_text(encoding="utf-8")
    조각 = 글.split('status="skipped", step_log={"reason": message}', 1)[1].split("\n\n", 1)[0]

    assert "return 0" in 조각, "예산 초과가 아직 실패(1)로 끝난다 — 뒤 단계가 건너뛰어진다"
    assert "return 1" not in 조각
