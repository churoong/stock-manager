"""2부가 한 종목의 여러 기간 신호 중 금액이 같은 것을 고를 때 들어온 행 순서에 맡기지 않는다
(docs/infra.md 25.1322, 리포트 감사 #3). 같으면 1부 순서(단기 → 중기 → 장기)."""

from __future__ import annotations

from batch.services import report_picks as rp
from tests.test_report_picks import row


def _note(rows: list[rp.SignalRow]) -> str:
    view = rp.build_portfolio(rows, total_investable=100_000_000)
    assert len(view.allocations) == 1
    return view.allocations[0].note


def test_금액이_같으면_들어온_순서와_무관하게_단기():
    앞 = _note([row(1, "short"), row(1, "long")])
    뒤 = _note([row(1, "long"), row(1, "short")])  # 예전: "장기 신호 기준"
    assert "단기 신호 기준" in 앞 and "단기 신호 기준" in 뒤


def test_금액이_크면_그_기간():
    note = _note([row(1, "short", suggested_amount=500_000.0), row(1, "long", suggested_amount=900_000.0)])
    assert "장기 신호 기준" in note
