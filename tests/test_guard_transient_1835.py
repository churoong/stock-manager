"""장중 감시 — 잔고 조회의 순간 한도는 그 회차만 쉬고, 잇달아 쉬면 그날 한 번 알린다 (docs/infra.md 25.1835).

2026-10-28 11:25 KST 회차가 모의 원장 초당 한도(EGW00215)를 1.5초·61초 다시 받기에도 못 넘어 "실행 실패" 알림이
갔는데 다음 회차는 정상이었다. 주문 전이라 아무것도 안 나갔다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.core import db
from batch.jobs import intraday_guard as ig
from batch.sources import kis_vts as kv
from batch.sources.kis import KisFailed
from tests.test_intraday_guard_1768 import Client, _main_준비, c  # noqa: F401 — 픽스처
from tests.test_kis_vts_1475 import _Sess

한도글 = "모의 잔고 조회 실패 EGW00215 원장에서 허용 가능한 초당 거래건수를 초과하였습니다."


class _막힌(_Sess):
    def __init__(self, exc: Exception) -> None:
        super().__init__([])
        self.exc = exc

    def balance(self) -> Any:
        raise self.exc


def test_순간_한도면_이_회차만_쉬고_건너뜀으로_닫는다(c: Client) -> None:  # noqa: F811
    from datetime import UTC, datetime

    기록 = ig.run(c, datetime(2026, 10, 26, 2, 25, tzinfo=UTC), session=_막힌(KisFailed(한도글)))
    assert 기록["transient"] is True and ig.TRANSIENT_SKIP in 기록["skipped"]
    assert ig.run_status(기록) == "skipped"


def test_순간_한도가_아니면_그대로_실패다(c: Client) -> None:  # noqa: F811
    from datetime import UTC, datetime

    with pytest.raises(KisFailed):
        ig.run(
            c, datetime(2026, 10, 26, 2, 25, tzinfo=UTC), session=_막힌(KisFailed("모의 잔고 조회 실패 OPSQ0002 계좌"))
        )
    with pytest.raises(kv.NoResponse):  # 응답 없음은 순간 한도로 보지 않는다
        ig.run(c, datetime(2026, 10, 26, 2, 25, tzinfo=UTC), session=_막힌(kv.NoResponse("EGW00215 응답 없음")))


def test_한_번_쉼은_알리지_않고_세_회차_잇달아면_그날_한_번(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _막힌(KisFailed(한도글)))
    for _ in range(2):
        assert ig.main() == 0
    assert 보낸 == []  # 두 회차(20분)는 다음 회차가 본다
    assert ig.main() == 0
    assert len(보낸) == 1 and "3회차(30분)" in 보낸[0]
    assert db.get_setting(c, ig.TRANSIENT_ALERT_KEY, None) == "2026-10-26"
    assert ig.main() == 0 and len(보낸) == 1  # 그날 다시 알리지 않는다
    assert (
        c.conn.execute(
            "SELECT status FROM batch_runs WHERE job_name = 'intraday_guard' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        == "skipped"
    )
    # 정상 회차는 세던 수를 비운다
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _Sess([]))
    assert ig.main() == 0
    assert db.get_setting(c, ig.TRANSIENT_STREAK_KEY, None) == {"date": "2026-10-26", "n": 0}


def test_토큰_오류는_쉬지_않고_바로_실패다(c: Client) -> None:  # noqa: F811
    """25.1836 (25.1835 교차검증 [필수]) — 캐시한 토큰이 무효면 그날 내내 같은 코드다. 순간 한도로 보면 조용히 쉬다
    30분 뒤 '초당 한도' 라는 틀린 까닭으로 알렸다"""
    from datetime import UTC, datetime

    for 코드 in ("EGW00123", "EGW00121"):
        with pytest.raises(KisFailed):
            ig.run(
                c,
                datetime(2026, 10, 26, 2, 25, tzinfo=UTC),
                session=_막힌(KisFailed(f"모의 잔고 조회 실패 {코드} 토큰")),
            )
    기록 = ig.run(
        c, datetime(2026, 10, 26, 2, 25, tzinfo=UTC), session=_막힌(KisFailed("모의 잔고 조회 실패 EGW00201 초당"))
    )
    assert 기록["transient"] is True


def test_잇단_쉼_알림은_실패_알림과_표시가_따로고_보낸_뒤에만_적는다(
    c: Client,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """25.1836 — 같은 표시를 쓰면 쉼 알림이 그날 뒤의 진짜 실패 알림을 막았다. 표시를 보내기 전에 적으면 전송이 끊긴 날
    알림이 다시 가지 않는다(25.1772)"""
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸, 실패=True)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _막힌(KisFailed(한도글)))
    for _ in range(3):
        assert ig.main() == 0
    assert db.get_setting(c, ig.TRANSIENT_ALERT_KEY, None) is None  # 못 보냈으니 표시 없음
    _main_준비(c, monkeypatch, 보낸)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _막힌(KisFailed(한도글)))
    assert ig.main() == 0 and len(보낸) == 1  # 넷째 회차가 다시 보낸다
    assert db.get_setting(c, ig.TRANSIENT_ALERT_KEY, None) == "2026-10-26"
    assert db.get_setting(c, ig.FAIL_ALERT_KEY, None) is None  # 실패 알림 표시는 건드리지 않는다
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _막힌(KisFailed("모의 잔고 조회 실패 EGW00123 토큰")))
    with pytest.raises(KisFailed):
        ig.main()
    assert len(보낸) == 2 and "실행 실패" in 보낸[1]  # 쉼 알림 뒤의 진짜 실패도 그날 한 번 간다


def test_전날_쉼은_세지_않고_확인_실행은_세지_않는다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _막힌(KisFailed(한도글)))
    db.set_setting(c, ig.TRANSIENT_STREAK_KEY, {"date": "2026-10-23", "n": 2})
    assert ig.main() == 0 and 보낸 == []
    assert db.get_setting(c, ig.TRANSIENT_STREAK_KEY, None) == {"date": "2026-10-26", "n": 1}
    monkeypatch.setattr("sys.argv", ["intraday_guard", "--dry-run"])
    for _ in range(3):
        assert ig.main() == 0
    assert db.get_setting(c, ig.TRANSIENT_STREAK_KEY, None) == {"date": "2026-10-26", "n": 1} and 보낸 == []


def test_설정_표시_이름은_모두_다르다() -> None:
    """25.1837 (25.1836 교차검증 [필수]) — 잇단 쉼 표시를 종목별 순간 거부 표시와 같은 이름으로 만들어 서로 덮었다"""
    이름들 = {k: v for k, v in vars(ig).items() if k.endswith("_KEY") and isinstance(v, str)}
    assert len(이름들) >= 8
    assert len(set(이름들.values())) == len(이름들), 이름들


def test_어제_표시는_오늘_잇단_쉼_알림을_막지_않는다(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    보낸: list[str] = []
    _main_준비(c, monkeypatch, 보낸)
    monkeypatch.setattr(kv, "Session", lambda *a, **k: _막힌(KisFailed(한도글)))
    db.set_setting(c, ig.TRANSIENT_ALERT_KEY, "2026-10-23")
    for _ in range(3):
        assert ig.main() == 0
    assert len(보낸) == 1
