"""거래일 확정 판정 테스트.

장중에 받은 가격을 종가로 저장하면 나중에 백테스트가 잘못된 값을 본다.
이 경계가 어긋나면 조용히 틀린 데이터가 쌓이므로 테스트로 고정한다.
"""

from __future__ import annotations

import pytest


def _is_settled(quote_date: str, expected_trade_date: str) -> bool:
    """daily.collect_prices 가 쓰는 판정과 같은 식.

    확정 거래일보다 뒤인 날짜는 아직 끝나지 않은 장이다.
    """
    return quote_date <= expected_trade_date


class Test확정판정:
    def test_예상_거래일과_같으면_확정이다(self) -> None:
        assert _is_settled("2026-09-15", "2026-09-15")

    def test_예상보다_과거면_확정이다(self) -> None:
        # 연휴 등으로 데이터가 뒤처진 경우. 값 자체는 확정된 종가다
        assert _is_settled("2026-09-11", "2026-09-15")

    def test_예상보다_미래면_장중이다(self) -> None:
        # 장중에 수동 실행하면 오늘 진행 중인 가격이 넘어온다
        assert not _is_settled("2026-09-16", "2026-09-15")

    def test_날짜_문자열_비교가_연월일_순서를_지킨다(self) -> None:
        # ISO 형식이라 문자열 비교로 날짜 비교가 성립한다.
        # 이 전제가 깨지면 판정이 통째로 뒤집힌다
        assert _is_settled("2026-01-31", "2026-02-01")
        assert not _is_settled("2026-02-01", "2026-01-31")
        assert _is_settled("2025-12-31", "2026-01-01")


class Test경계값:
    @pytest.mark.parametrize(
        ("quote_date", "trade_date", "expected"),
        [
            ("2026-09-15", "2026-09-15", True),
            ("2026-09-14", "2026-09-15", True),
            ("2026-09-16", "2026-09-15", False),
            ("2026-12-31", "2027-01-04", True),
            ("2027-01-04", "2026-12-31", False),
        ],
    )
    def test_연말연시를_넘어도_맞는다(
        self, quote_date: str, trade_date: str, expected: bool
    ) -> None:
        assert _is_settled(quote_date, trade_date) is expected
