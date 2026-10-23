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
