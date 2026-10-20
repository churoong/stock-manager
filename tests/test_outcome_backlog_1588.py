"""결정 원장 성과 채우기가 한 번 상한(`OUTCOME_BATCH`)에 닿으면 운용 줄에 밀림 경고 (docs/infra.md 25.1588)."""

from __future__ import annotations

import pytest

from batch.jobs import paper as job
from tests.test_paper_job_1445 import Client, _하루, client  # noqa: F401 — 픽스처


def test_상한에_닿으면_밀림_경고(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    c = client
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert 기록["decision_outcomes"]["pending_read"] < job.OUTCOME_BATCH
    assert not any("성과 채우기 밀림" in x for x in 줄)
    monkeypatch.setattr(job, "OUTCOME_BATCH", 0)  # 대기열이 상한만큼 찼다
    줄, _ = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert any(x.startswith("⚠️ 결정 원장 성과 채우기 밀림 — 한 번에 0행 상한") for x in 줄)
