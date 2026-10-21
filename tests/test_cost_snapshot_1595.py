"""주문 근거에 비용 스냅샷 — 기본값을 쓴 항목까지 (docs/paper.md 2.3, docs/infra.md 25.1595)."""

from __future__ import annotations

import json

import pytest

from batch.jobs import paper as job
from tests.test_paper_job_1445 import Client, _시세, _하루, client  # noqa: F401 — 픽스처


def test_비용_스냅샷_손계산() -> None:
    s = job.cost_snapshot("KR", {}, {}, "2026-10-15")
    assert s["defaults"] == ["commission", "tax", "slippage"] and s["fee_pct"] == pytest.approx(0.015)
    s = job.cost_snapshot("US", {"us_buy_pct": 0.25, "us_sell_pct": 0.25}, {}, "2026-10-15")
    assert s["fee_pct"] == pytest.approx(0.25) and s["sell_tax_pct"] == 0 and "commission" not in s["defaults"]


def test_매수_매도_주문_근거에_비용이_남는다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    c = client
    _하루(c, monkeypatch, "2026-10-14")
    근거 = [json.loads(r) for (r,) in c.q("SELECT rationale_json FROM paper_orders WHERE side = 'buy'")]
    assert 근거 and all(x["costs"]["as_of"] == "2026-10-15" and "slippage" in x["costs"]["defaults"] for x in 근거)
    _시세(c, "2026-10-15", 10_000, 9_000)  # 단기 손절 → 매도
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    매도 = [json.loads(r) for (r,) in c.q("SELECT rationale_json FROM paper_orders WHERE side = 'sell'")]
    assert 매도 and all("fee_pct" in x["costs"] for x in 매도)
