"""점검 플래그의 [확인] 이어받기 — 수치·자료 사유끼리의 이동은 다시 알리지 않는다 (docs/infra.md 25.614)."""

from __future__ import annotations

from batch.services import sell_flags as sf
from batch.services import universe as u


def _c(display: str) -> list[dict]:
    return [{"label": "유니버스 제외", "display": display}]


def test_거래대금미달과_데이터없음_사이는_이어받는다() -> None:
    assert sf.keeps_dismissal("점검", _c(u.REASON_NO_DATA), _c(u.REASON_LOW_TURNOVER))
    assert sf.keeps_dismissal("점검", _c(u.REASON_LOW_TURNOVER), _c(u.REASON_NO_DATA))


def test_결격으로_넘어가면_다시_알린다() -> None:
    """25.564 가 지키려던 순간 — 시총미달을 확인한 뒤 관리종목으로 넘어가면 조용히 지나가면 안 된다."""
    assert not sf.keeps_dismissal("점검", _c(u.REASON_SMALL_CAP), _c(u.REASON_SUPERVISED))
    assert not sf.keeps_dismissal("점검", _c(u.REASON_NO_DATA), _c(u.REASON_HALTED))


def test_수치_사유는_유니버스_상수와_같다() -> None:
    assert sf.수치_사유 == {u.REASON_NO_DATA, u.REASON_SMALL_CAP, u.REASON_LOW_TURNOVER, u.REASON_NEWLY_LISTED}
