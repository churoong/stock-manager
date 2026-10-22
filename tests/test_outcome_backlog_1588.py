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
    # 창 하나가 상한에 닿고 그 끝까지 찼다(25.1692 부터 그때만 밀림 — 시세가 끊긴 행만 남은 것은 아니다)
    monkeypatch.setattr(job, "fill_outcomes",
                        lambda *a, **k: {"pending_read": 400, "filled": 400, "gave_up": 0, "full_bands": 1})
    줄, _ = _하루(c, monkeypatch, "2026-10-15", signals=False)
    assert any(x.startswith(f"⚠️ 결정 원장 성과 채우기 밀림 — 한 번에 {job.OUTCOME_BATCH}행 상한") for x in 줄)
