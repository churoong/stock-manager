"""H 감사 [필수] (docs/company.md 10장, docs/paper.md 2.5, docs/infra.md 25.1622).

지키는 것: 매수 단계가 일찍 끝난 날(환율 모름 등)에도 지주사가 고른 종목의 표(`detail.holding`)가 결정 원장에 남는다 —
skip "매수 단계 건너뜀: …" 으로, 견줄 후보에서는 뺀다
"""

from __future__ import annotations

import json

import pytest

from batch.services import paper as pp
from tests.test_company_job_1466 import _하루
from tests.test_holding_1482 import 일곱  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def test_매수_단계가_일찍_끝나도_고른_종목의_표가_남는다(일곱: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    c = 일곱
    # 어젯밤 미국 배치가 낸 H 의 달러 지정가 매수(아직 열림) — 환율 표가 비어 그 예약의 원화를 모른다
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, fill_basis, tranche, reason, horizon, status, rule_version,"
                   " created_at) VALUES (901, 'H', 'US', 3, 'buy', 'limit', 10, 1, 'USD', '2026-10-13', '2026-10-16',"
                   " 'open', 1, 't', 'short', 'open', 1, 't')")  # fmt: skip
    _줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    h = 기록["accounts"]["H"]
    assert h["holding"]["chosen"] == 1 and h["buy"] == {"skipped": "미체결 달러 주문의 환율 모름"}
    rows = {r[0]: (r[1], r[2], json.loads(r[3] or "{}"))
            for r in c.q("SELECT stock_id, kind, reason, detail_json FROM paper_decisions"
                         " WHERE account_id = 'H' AND market = 'KR'")}  # fmt: skip
    kind, reason, detail = rows[1]
    assert kind == "skip" and reason == pp.BUY_STEP_SKIP_PREFIX + "미체결 달러 주문의 환율 모름"
    assert "holding" in detail and detail["holding"]["votes"]
    assert reason.startswith(pp.NONCOMPARABLE_SKIP_PREFIXES)  # 선정 결과가 아니다
