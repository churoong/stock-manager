"""매수 단계가 이상으로 일찍 끝난 날(환율 없음·미체결 달러 환율 모름·장부 현금 음수)을 운용 점검·관문 무사고가 센다
(docs/infra.md 25.2226, 11-12 결함 회차 [권고]).

예전엔 `desk.account_skips` 가 buy 칸을 보지 않고 `checks` 에도 줄이 없어, H 가 장부 현금 음수로 사지 못해도
'확인할 일: 없음' 이고 관문 '운영 무사고' 는 사고 0일이었다."""

from __future__ import annotations

import inspect

import pytest

from batch.jobs import paper as job
from batch.services import desk
from batch.services import paper as pp
from tests.test_gate_account_skips_2142 import _읽기


@pytest.mark.parametrize(
    ("계좌", "사고"),
    [
        ({"H": {"recon_ok": True, "holding": {}, "buy": {"skipped": "장부 현금이 음수"}}}, True),
        ({"A": {"buy": {"skipped": "환율 없음"}}}, True),
        ({"B": {"buy": {"skipped": "미체결 달러 주문의 환율 모름"}}}, True),
        ({"H": {"buy": {"skipped": "배분하지 않는 날(신호 없음·묵음·추천 못 읽음)"}}}, False),  # 정상 쉼
        ({"A": {"buy": {"candidates": 3, "skipped": ["종목 상한"]}}}, False),  # 후보별 건너뜀(목록)
        ({"X": {"etf": {"regime": "up"}, "buy": {"skipped": "환율 없음"}}}, False),  # ETF 는 제 경고
    ],
)
def test_이상_끝남은_사고_정상_쉼은_아님(계좌: dict, 사고: bool) -> None:
    모음 = desk.account_skips(계좌)
    assert bool(모음) is 사고, 모음
    log = {"paper": {"accounts": 계좌, **({"account_skips": 모음} if 모음 else {})}}
    (줄,) = _읽기([log])
    assert desk.batch_incident_days([줄], {"daily_kr": ["2026-10-10"]}) == (1 if 사고 else 0)


def test_확인할_일에_줄이_서고_장부_현금_음수는_높음() -> None:
    p = {"accounts": {"H": {"recon_ok": True, "buy": {"skipped": "장부 현금이 음수"}},
                      "A": {"buy": {"skipped": "환율 없음"}}}}  # fmt: skip
    줄 = desk.checks(halted=False, paper=p, broker=None, recon_bad=[], warnings=0)
    글 = {x["text"]: x["level"] for x in 줄 if "매수 단계 건너뜀" in x["text"]}
    assert len(글) == 2
    assert [lv for t, lv in 글.items() if "장부 현금이 음수" in t] == ["high"]
    assert [lv for t, lv in 글.items() if "환율 없음" in t] == ["normal"]


def test_까닭_글이_buy_orders_와_같다() -> None:
    src = inspect.getsource(job.buy_orders)
    for 까닭 in pp.BUY_FAULT_SKIPS:
        assert f'{{"skipped": "{까닭}"}}' in src, 까닭
