"""25.1920 — 가상모의투자 계좌 하나의 예외가 단계 전체를 멈추지 않는다 (스윙·단타 첫날 감사 [권고], handoff L2 후보).

예전엔 `run_step` 의 계좌 루프에 계좌별 `try` 가 없어, 한 계좌의 예외로 뒤 계좌·결정 원장 성과 채우기·가상 대조·리포트 절과
H 의 KIS 베끼기(`daily.py` 는 `paper.error` 면 베끼지 않는다)까지 그날 빠졌다. 지금은 그 계좌만 `error` 로 남기고 나머지는
돈다. KIS 가 베끼는 H 가 깨지면 예전처럼 단계 실패(`error`)로 베끼기를 막는다. DB 한도는 그대로 던진다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.core import db
from batch.jobs import broker_mirror as bm
from batch.jobs import paper as job
from tests.test_paper_accounts_1458 import _하루, c  # noqa: F401 — 픽스처

pytestmark = pytest.mark.usefixtures("c")


def _깨뜨림(monkeypatch: pytest.MonkeyPatch, 계좌: str, exc: Exception) -> None:
    원래 = job.mark

    def mark(client: Any, aid: str, *a: Any, **k: Any) -> Any:
        if aid == 계좌:
            raise exc
        return 원래(client, aid, *a, **k)

    monkeypatch.setattr(job, "mark", mark)


def _주문계좌(c: Any) -> set[str]:  # noqa: F811 — 픽스처 이름과 같은 인자
    return {r[0] for r in c.q("SELECT DISTINCT account_id FROM paper_orders")}


def test_한_계좌가_깨져도_나머지는_돌고_그_계좌만_오류(c: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _깨뜨림(monkeypatch, "C", RuntimeError("평가 실패"))
    기록 = _하루(c, monkeypatch, "2026-10-14")
    assert "error" not in 기록  # C 는 KIS 가 베끼는 계좌가 아니다 — 베끼기는 막지 않는다
    assert 기록["account_errors"] == {"C": "RuntimeError: 평가 실패"}
    assert 기록["accounts"]["C"]["error"] == "RuntimeError: 평가 실패"
    # C 뒤 계좌(D·E·G·H·R1·R2)도 돌았다 — 예전엔 C 에서 멈춰 그 뒤가 비었다
    assert {"A", "B", "D", "E", "R1", "R2"} <= _주문계좌(c) and "C" not in _주문계좌(c)
    # 루프 뒤 곁다리도 돌았다
    assert "decision_outcomes" in 기록 and "frozen_by_account" in 기록


def test_베끼는_계좌가_깨지면_단계_실패로_베끼기를_막는다(c: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _깨뜨림(monkeypatch, bm.MIRROR_ACCOUNT, KeyError("x"))
    기록 = _하루(c, monkeypatch, "2026-10-14")
    assert 기록["error"].startswith(f"가상 {bm.MIRROR_ACCOUNT} 계좌 단계 실패: KeyError")
    assert set(기록["account_errors"]) == {bm.MIRROR_ACCOUNT}
    assert {"A", "B", "R1", "R2"} <= _주문계좌(c)  # 다른 계좌는 그대로


def test_DB_한도는_가두지_않고_던진다(c: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    class 한도(Exception):
        pass

    monkeypatch.setattr(db, "quota_reason", lambda exc: "한도" if isinstance(exc, 한도) else None)
    _깨뜨림(monkeypatch, "C", 한도("BLOCKED"))
    with pytest.raises(한도):
        _하루(c, monkeypatch, "2026-10-14")
