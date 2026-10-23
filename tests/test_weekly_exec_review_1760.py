"""주간 요약 — 실행 품질 절과 가상모의투자 월간 복기의 따라잡기 표시를 따로 (docs/infra.md 25.1760, 결함 루프 L2).

한 표시(`paper_monthly_review_sent`)를 같이 쓰면: 첫 일요일에 실행 품질만 깨진 달은 둘째 주 따라잡기가 없어 그 절이 통째로 빠지고,
가상모의투자 절만 깨진 달은 둘째 주에 실행 품질이 한 번 더 나갔다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from batch.core import db
from batch.jobs import paper as jp
from batch.jobs import weekly_summary as ws
from tests.test_portfolio_job import MemClient


def _돌림(monkeypatch: pytest.MonkeyPatch, mem: MemClient, 언제: datetime, 모의, 실행) -> str:  # noqa: ANN001
    보낸: list[str] = []
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    monkeypatch.setattr(ws.telegram, "send", lambda t, *a, **k: 보낸.append(t) or [1])
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (0, 0, []))

    class 고정(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return 언제

    monkeypatch.setattr(ws, "datetime", 고정)
    monkeypatch.setattr(jp, "summary_section", 모의)
    monkeypatch.setattr(ws, "execution_lines", 실행)
    assert ws.run(force=True) == 0
    return 보낸[-1]


def _깨짐(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
    raise RuntimeError("한도")


def _실행(client, now):  # noqa: ANN001, ANN202
    return ["[실행 품질 — 실제 매매]"] if ws.execution_due(client, now) else []


def _모의(client, now):  # noqa: ANN001, ANN202
    오늘 = now.astimezone(ws.cal.market_tz("KR")).date()
    return ["[가상모의투자 월간 복기] 지난달"] if jp.review_due(client, 오늘) else ["[가상모의투자] 이번 주"]


@pytest.fixture
def mem() -> MemClient:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    m.close = lambda: None  # type: ignore[method-assign]
    return m


첫주 = datetime(2026, 10, 31, 22, 41, tzinfo=UTC)  # 한국 11-01(일)
둘째주 = datetime(2026, 11, 7, 22, 41, tzinfo=UTC)  # 한국 11-08(일) — 따라잡기 창


def test_첫_주에_실행_품질만_깨지면_둘째_주가_따라잡는다(monkeypatch: pytest.MonkeyPatch, mem: MemClient) -> None:
    글 = _돌림(monkeypatch, mem, 첫주, _모의, _깨짐)
    assert "월간 복기" in 글 and "실행 품질 절을 만들지 못했습니다" in 글
    assert db.get_setting(mem, ws.EXEC_REVIEW_KEY, None) is None  # type: ignore[arg-type]
    글 = _돌림(monkeypatch, mem, 둘째주, _모의, _실행)
    assert "[실행 품질 — 실제 매매]" in 글 and "월간 복기" not in 글  # 실행 품질만 따라잡고 복기는 두 번 싣지 않는다
    assert db.get_setting(mem, ws.EXEC_REVIEW_KEY, None) == "2026-11"  # type: ignore[arg-type]


def test_첫_주에_가상모의투자_절만_깨지면_실행_품질은_두_번_나가지_않는다(monkeypatch: pytest.MonkeyPatch, mem: MemClient) -> None:
    글 = _돌림(monkeypatch, mem, 첫주, _깨짐, _실행)
    assert "[실행 품질 — 실제 매매]" in 글
    assert db.get_setting(mem, jp.MONTHLY_REVIEW_KEY, None) is None  # type: ignore[arg-type]
    글 = _돌림(monkeypatch, mem, 둘째주, _모의, _실행)
    assert "월간 복기" in 글 and "[실행 품질 — 실제 매매]" not in 글


def test_잘린_목록은_나머지_수를_적고_묵은_작업은_심한_것부터() -> None:
    """25.1762(결함 루프 L2 참고) — 모든 작업이 묵으면 정의 순서 앞 8개만 실리고 나머지가 표시 없이 사라졌다"""
    now = datetime(2026, 10, 24, tzinfo=UTC)
    이름 = list(ws.EXPECTED_DAYS)
    없음, 심함 = 이름[-1], 이름[-2]  # 정의 순서 맨 끝 둘 — 예전엔 잘려 안 보였다
    마지막 = {j: "2026-10-23T00:00:00+00:00" for j in 이름[:-1]}
    마지막[심함] = "2020-01-01T00:00:00+00:00"
    절 = ws.compose(runs=[], last_success=마지막, now=now, reads_used=0).split("기대 주기를 넘긴 예약 작업\n", 1)[1]
    절 = 절.split("\n\n", 1)[0].splitlines()
    assert 절[0].startswith(f"  {없음}: 돈 기록 없음") and 절[1].startswith(f"  {심함}: 마지막 실행")
    # 모두 묵으면 8줄 + 나머지 수
    모두 = ws.compose(runs=[], last_success=dict.fromkeys(이름, "2020-01-01T00:00:00+00:00"), now=now, reads_used=0)
    절 = 모두.split("기대 주기를 넘긴 예약 작업\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert len(이름) > ws.MAX_LINES and len(절) == ws.MAX_LINES + 1
    assert 절[-1] == f"  … 외 {len(이름) - ws.MAX_LINES}개 (웹 /status)"


def test_문제_작업과_되풀이_경고도_나머지_수를_적는다() -> None:
    """25.1762 교차검증 [권고] — 세 목록 가운데 묵은 작업만 시험해, 나머지 둘의 "… 외 N개" 를 빼도 통과했다"""
    now = datetime(2026, 10, 24, tzinfo=UTC)
    n = ws.MAX_LINES + 2
    runs = [ws.Run(f"job{i}", "KR", "failed", "2026-10-20T00:00:00+00:00", None, ()) for i in range(n)]
    # 머리말(PREFIX_LEN)이 서로 다른 경고를 두 번씩 — 되풀이로 묶인다
    경고 = tuple(f"경고{i:02d} " + "가" * ws.PREFIX_LEN for i in range(ws.MAX_LINES + 1))
    runs += [ws.Run("daily_kr", "KR", "partial", f"2026-10-2{k}T00:00:00+00:00", None, 경고) for k in (1, 2)]
    글 = ws.compose(runs, dict.fromkeys(ws.EXPECTED_DAYS, "2026-10-23T00:00:00+00:00"), now, 0)
    문제 = 글.split("\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert 문제[-1] == f"  … 외 {n + 1 - ws.MAX_LINES}개 (웹 /status)"  # 실패 n + 부분 1 작업
    되풀이 = 글.split("되풀이된 경고·실패 사유", 1)[1].split("\n\n", 1)[0].splitlines()
    assert 되풀이[-1] == "  … 외 1개 (웹 /status)"


def test_묵은_작업은_일수가_아니라_기대_주기_대비로() -> None:
    """daily_kr 6일(기대 4일 — 1.5배)이 dividends 400일(기대 370일 — 1.08배)보다 앞 (25.1762 교차검증 [참고])"""
    now = datetime(2026, 10, 24, tzinfo=UTC)
    마지막 = dict.fromkeys(ws.EXPECTED_DAYS, "2026-10-23T00:00:00+00:00")
    마지막["daily_kr"] = "2026-10-18T00:00:00+00:00"
    마지막["dividends"] = "2025-09-19T00:00:00+00:00"
    절 = ws.compose([], 마지막, now, 0).split("기대 주기를 넘긴 예약 작업\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert [x.split(":")[0].strip() for x in 절] == ["daily_kr", "dividends"]
