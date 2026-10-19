"""핵심 H 장부 전략이 운영 함수의 모르는 읽기를 삼키지 않는다 (docs/infra.md 25.1533, 25.1532 반박 검증).

운영 `jobs/paper.frozen_ids` 는 읽기 실패를 "멈춤" 으로 삼킨다(운영에서는 보수 쪽). 백테스트 창(`_ViewClient`)이 모르는
읽기를 받으면 그 실패가 삼켜져 무작위 대조 후보가 조용히 모두 멈출 수 있었다 — 적어 두었다가 그날 판단 끝에 다시 던진다.
"""

from __future__ import annotations

import contextlib

import pytest

from batch.jobs import paper as jp
from batch.services import backtest as bt
from batch.services import core_strategy as cs


def test_삼켜진_모르는_읽기는_그날_판단_끝에_다시_던진다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_core_strategy_1532 import _돌림

    원래 = jp.buy_orders

    def 삼킴(client, **kw):  # noqa: ANN001, ANN202 — 운영 함수가 안에서 읽기 실패를 삼키는 꼴
        with contextlib.suppress(LookupError):
            client.execute("SELECT 운영이_새로_읽는_칸 FROM 어딘가 WHERE stock_id = ?", [1])
        return 원래(client, **kw)

    monkeypatch.setattr(jp, "buy_orders", 삼킴)
    with pytest.raises(LookupError, match="삼켜짐"):
        _돌림("core_b", {1: [100, 100, 100, 100]}, {0: [(1, 10.0)]})


def test_줄이_있는_dict_읽기는_깨진다() -> None:
    assert cs._Rows([]).dicts() == []
    with pytest.raises(LookupError):
        cs._Rows([(1,)]).dicts()


def test_아는_읽기는_그대로() -> None:
    v = bt.PriceView({1: {"2024-01-02": 100.0, "2024-01-03": 110.0}}, "2024-01-03")
    c = cs._ViewClient(v)
    assert c.execute(jp.LAST_CLOSE_SQL, [1, "2024-01-03"]).rows == [(110.0, "2024-01-03")]
    assert c.misses == []
