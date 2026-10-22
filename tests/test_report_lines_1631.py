"""모의투자 알림 줄 감사 [필수] 3건 (docs/infra.md 25.1631).

1. 운용 보고 KIS 줄이 접수 시간 밖(미국 예약 마감 뒤·국내 접수 시작 10분 넘게 전)에도 "그대로 냅니다"
2. 괴리 알림이 반올림으로 "85% — 문턱 85%" 처럼 문턱과 같아 보임
3. 위원회 메모의 "가결" 이 실제로는 집행(상위 top_n) 수
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from batch.jobs import broker_mirror as bmj
from batch.jobs import desk as job
from batch.services import broker_mirror as bm
from batch.services import company as co
from tests.test_paper_job_1445 import Client


def test_send_block_은_단계와_같은_판정() -> None:
    # 미국 10-21(수) 13:25 UTC — 예약 마감(개장 13:30 UTC 앞) 뒤
    assert bmj.send_block("US", datetime(2026, 10, 21, 13, 25, tzinfo=UTC)) == "예약 접수 마감 뒤 — 보내지 않음"
    # 국내 07:50 KST 손 실행 — 접수 08:30 까지 40분, 기다림 상한 10분 넘음
    assert bmj.send_block("KR", datetime(2026, 10, 21, 22, 50, tzinfo=UTC)) == "접수 시작 전 — 보내지 않음"
    # 국내 08:20:00 KST 정각 — 기다림이 상한(10분)과 같으면 기다려 낸다(넘을 때만 막음)
    assert bmj.send_block("KR", datetime(2026, 10, 21, 23, 20, tzinfo=UTC)) is None
    assert bmj.send_block("KR", datetime(2026, 10, 21, 23, 19, 59, tzinfo=UTC)) == "접수 시작 전 — 보내지 않음"
    # 국내 08:25 KST — 5분 기다려 낸다
    assert bmj.send_block("KR", datetime(2026, 10, 21, 23, 25, tzinfo=UTC)) is None
    # 국내 15:30 KST — 장 끝난 뒤
    assert bmj.send_block("KR", datetime(2026, 10, 22, 6, 30, tzinfo=UTC)) == "장 끝난 뒤 — 보내지 않음"
    # 토요일
    assert bmj.send_block("KR", datetime(2026, 10, 23, 23, 25, tzinfo=UTC)) == "오늘은 장이 없다"


def test_운용_보고는_접수_시간_밖이면_내지_않는다고_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for k, v in (("KIS_VTS_APP_KEY", "k"), ("KIS_VTS_APP_SECRET", "s"), ("KIS_VTS_ACCOUNT", "12345678-01")):
        monkeypatch.setenv(k, v)

    def 머리(market: str, now: datetime) -> str:
        return job.report_head(c, market, now, trade_date="2026-10-20", session_date="2026-10-21",
                               step_log={"paper": {"accounts": {}}}, warnings=[])[-1]  # fmt: skip

    assert 머리("US", datetime(2026, 10, 21, 13, 25, tzinfo=UTC)).startswith(
        "▸ KIS모의투자: 오늘은 내지 않습니다 — 예약 접수 마감 뒤")
    assert 머리("KR", datetime(2026, 10, 21, 22, 50, tzinfo=UTC)).startswith(
        "▸ KIS모의투자: 오늘은 내지 않습니다 — 접수 시작 전")
    assert 머리("KR", datetime(2026, 10, 21, 23, 25, tzinfo=UTC)).startswith(
        "▸ KIS모의투자: 이 주문을 접수 시간에 그대로 냅니다")
    # 까닭은 앞말만(" — 보내지 않음" 떼고), "오늘은" 겹치지 않게
    assert 머리("KR", datetime(2026, 10, 23, 23, 25, tzinfo=UTC)) == "▸ KIS모의투자: 오늘은 내지 않습니다 — 장이 없다"
    assert 머리("US", datetime(2026, 10, 21, 13, 25, tzinfo=UTC)) == "▸ KIS모의투자: 오늘은 내지 않습니다 — 예약 접수 마감 뒤"


def _요약(**k: object) -> dict:
    return {"n": 30, "days": 28, "internal_only": 0, "kis_only": 0, **k}


def test_괴리_알림은_문턱과_같아_보이지_않는다() -> None:
    글 = bm.diverge(_요약(match_pct=79.6, kept_pct=84.6, slip_buy={"avg": 0.503, "n": 12}),
                    ["match", "kept", "slip_buy"], "KR")  # fmt: skip
    assert 글 is not None
    assert "체결 일치 79.6% — 문턱 80% 아래." in 글
    assert "비율) 84.6% — 문턱 85% 아래:" in 글
    assert "매수 가격 차이 평균(n=12) +0.503% — 문턱 +0.5% 위:" in 글
    # 0.5 바로 위도 넘어 보이게(교차검증 [권고])
    assert "+0.5002% — 문턱 +0.5% 위" in (bm.diverge(_요약(slip_buy={"avg": 0.5002, "n": 30}), ["slip_buy"], "KR") or "")
    # 멀리 떨어진 값은 예전 자릿수 그대로
    assert "비율) 70% — 문턱 85% 아래" in (bm.diverge(_요약(kept_pct=70.0), ["kept"], "KR") or "")


def test_문턱_안쪽으로_돌아온_켜진_칸엔_방향을_말하지_않는다() -> None:
    """교차검증 [필수] — 켜진 칸은 꺼짐 문턱까지 켜 두고, 전날 알림이 실패하면 오늘 다시 글로 온다. 값이 문턱 안쪽이면
    "82.000% — 문턱 80% 아래" 같은 거짓 방향이 났다."""
    글 = bm.diverge(_요약(match_pct=82.0, kept_pct=88.0, slip_buy={"avg": 0.45, "n": 12}),
                    ["match", "kept", "slip_buy"], "KR") or ""  # fmt: skip
    assert "체결 일치 82% — 문턱 80%." in 글 and "비율) 88% — 문턱 85%:" in 글
    assert "평균(n=12) +0.45% — 문턱 +0.5%:" in 글 and "아래" not in 글 and " 위" not in 글


def test_위원회_메모는_가결과_집행을_가른다() -> None:
    def 안건(sid: int, 통과: bool) -> co.Item:
        return co.Item(stock_id=sid, ticker=f"T{sid:02d}", opinions={}, vote=1.0, favor=0, issue=False, passed=통과,
                       reason="")  # fmt: skip

    items = [안건(i, i <= 8) for i in range(1, 21)]
    글 = co.memo("가치투자사", items, items[:5])
    assert 글.startswith("가치투자사 위원회: 안건 20 · 가결 8(집행 5) · 부결 12 · 거부 0")
