"""매수 단계가 이상으로 일찍 끝난 날(환율 없음·미체결 달러 환율 모름·장부 현금 음수)을 운용 점검·관문 무사고가 센다
(docs/infra.md 25.2226, 11-12 결함 회차 [권고]).

예전엔 `desk.account_skips` 가 buy 칸을 보지 않고 `checks` 에도 줄이 없어, H 가 장부 현금 음수로 사지 못해도
'확인할 일: 없음' 이고 관문 '운영 무사고' 는 사고 0일이었다."""

from __future__ import annotations

import inspect
import re

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


def test_같은_까닭은_한_줄로_모은다() -> None:
    """미국 실행 환율 없음 — 배분 계좌가 모두 같은 날 멈춘다. 계좌마다 줄을 세우면 텔레그램 세 줄이 밀렸다(25.2227)"""
    p = {"accounts": {aid: {"buy": {"skipped": "환율 없음"}} for aid in ("A", "B", "C", "D", "E", "G", "H")}}
    p["accounts"]["X"] = {"etf": {"regime": "up"}, "buy": {"skipped": "환율 없음"}}  # ETF 는 빠진다
    줄 = [x for x in desk.checks(halted=False, paper=p, broker=None, recon_bad=[], warnings=0)
         if "매수 단계 건너뜀" in x["text"]]  # fmt: skip
    assert len(줄) == 1 and 줄[0]["level"] == "normal"
    assert desk._이름("X") not in 줄[0]["text"] and desk._이름("H") in 줄[0]["text"]


def test_buy_orders_의_글자_까닭은_모두_상수에_있다() -> None:
    """반대 방향 — 새 이른 반환 글이 생기고 상수에 안 넣으면 조용히 정상 쉼이 된다(25.2227)"""
    src = inspect.getsource(job.buy_orders)
    글들 = set(re.findall(r'return \[\], \{"skipped": "([^"]+)"\}', src))
    assert 글들 == set(pp.BUY_FAULT_SKIPS)


def test_같은_날_다시_돌아_샀어도_사고로_남는다() -> None:
    """보수 쪽 — 25.2145 의 '다시 돌아도 사고' 가 매수 이상 끝남에도 붙는다(25.2227 에 적음)"""
    계좌 = {"H": {"buy": {"skipped": "환율 없음"}}}
    줄들 = _읽기([{"paper": {"accounts": 계좌, "account_skips": desk.account_skips(계좌)}},
                {"paper": {"accounts": {"H": {"buy": {"orders": 2}}}, "account_skips": {}}}])
    for r in 줄들:
        r["trade_date"] = "2026-10-10"
    assert desk.batch_incident_days(줄들, {"daily_kr": ["2026-10-10"]}) == 1
