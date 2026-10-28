"""25.1920 — 가상모의투자 계좌 하나의 예외가 단계 전체를 멈추지 않는다 (스윙·단타 첫날 감사 [권고], handoff L2 후보).

예전엔 `run_step` 의 계좌 루프에 계좌별 `try` 가 없어, 한 계좌의 예외로 뒤 계좌·결정 원장 성과 채우기·가상 대조·리포트 절과
H 의 KIS 베끼기(`daily.py` 는 `paper.error` 면 베끼지 않는다)까지 그날 빠졌다. 지금은 그 계좌만 `step_error` 로 남기고(25.1922) 나머지는
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
    # `error`(건너뛴 계좌 — "오늘 평가·매매 안 함")가 아니라 `step_error`(25.1922)
    # 기록 dict 는 처음부터 걸려 있어 그때까지 채운 칸(체결 수)이 남는다(25.1924) — 주문은 아직 없다
    assert 기록["accounts"]["C"] == {"fills": 0, "step_error": "RuntimeError: 평가 실패"}
    # C 뒤 계좌도 돌았다 — 예전엔 C 에서 멈춰 그 뒤가 비었다. D·E·R1·R2 는 주문을 냈고, KIS 가 베끼는 H 는 평가까지
    # 끝냈다(이 자료에선 H 의 투표 회사가 없어 사지 않는다)
    assert {"D", "E", "R1", "R2"} <= _주문계좌(c) and "C" not in _주문계좌(c)
    assert "recon_ok" in 기록["accounts"][bm.MIRROR_ACCOUNT] and "step_error" not in 기록["accounts"][bm.MIRROR_ACCOUNT]
    # 상황실 확인할 일 — 높음 한 줄(25.1922)
    from batch.services import desk

    글 = [x["text"] for x in desk.checks(halted=False, paper=기록, broker=None, recon_bad=[], warnings=0)]
    assert any(t.startswith("가상 ") and "계좌 단계 실패(RuntimeError: 평가 실패)" in t for t in 글), 글
    # 루프 뒤 곁다리도 돌았다
    assert "decision_outcomes" in 기록 and "frozen_by_account" in 기록


def test_베끼는_계좌가_깨지면_단계_실패로_베끼기를_막는다(c: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _깨뜨림(monkeypatch, bm.MIRROR_ACCOUNT, KeyError("x"))
    줄들: list[list[str]] = []
    원래 = job.run_step
    monkeypatch.setattr(job, "run_step", lambda *a, **k: (r := 원래(*a, **k), 줄들.append(r[0]))[0])
    기록 = _하루(c, monkeypatch, "2026-10-14")
    # 리포트 ⚠️ 줄이 KIS 베끼기를 안 한 까닭을 말한다(25.1922·25.1924)
    assert any("계좌 단계 실패(KeyError)" in x and x.endswith("오늘 KIS 베끼기 안 함") for x in 줄들[0]), 줄들[0]
    # 상황실은 단계 실패 줄 하나로 — 같은 사건의 계좌 줄을 겹쳐 내지 않는다(25.1924)
    from batch.services import desk

    글 = [x["text"] for x in desk.checks(halted=False, paper=기록, broker=None, recon_bad=[], warnings=0)]
    assert sum("계좌 단계 실패" in t for t in 글) == 1, 글
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


def test_도전자_계좌만_깨진_날은_도전자_베끼기를_보류한다() -> None:
    """25.1922 (25.1920 교차검증 [권고]) — `daily.challenger_block`: 잠금·첫째 까닭이 없어도 도전자 계좌가 계좌 단계
    실패면 보류. 다른 계좌의 실패는 도전자를 막지 않는다."""
    from batch.jobs import daily

    깨짐 = {"account_errors": {"G": "KeyError: 1"}}
    assert daily.challenger_block(깨짐, {}, None, "G") == "가상 G 계좌 단계 실패 — 오늘 도전자 베끼기 보류"
    assert daily.challenger_block(깨짐, {}, None, "A") is None
    assert daily.challenger_block({}, {}, None, "G") is None
    assert daily.challenger_block(깨짐, {}, "잠금", "G") == "잠금"


@pytest.mark.parametrize(("rec", "글"), [
    ({"orders": 3}, "주문 3건은 적은 뒤"),
    ({"orders": "이미 냄"}, "오늘 주문은 앞 실행이 이미 냄"),
    ({"orders_pending": 2}, "주문 2건을 적던 중(일부만 적혔을 수 있음)"),
    ({"orders": 0}, ""),
    ({}, ""),
    ({"orders": True}, ""),
])
def test_주문을_어디까지_적었나_글(rec: dict, 글: str) -> None:
    """25.1924 (25.1922 교차검증 [권고]) — `orders` 가 글("이미 냄")이면 "이미 냄건" 처럼 깨졌고, `_쓰기` 도중 예외는 주문
    수가 없어 "끝나지 않았을 수 있음" 만 나왔다. 기록 dict 를 처음부터 걸어 두고 적기 전 수(`orders_pending`)를 남긴다"""
    from batch.services import paper as pp

    assert pp.step_error_orders(rec) == 글


def test_주문을_적던_중_깨지면_적기_전_수가_남는다(c: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """25.1926 (25.1924 교차검증 [권고]) — `_쓰기` 도중 예외(비원자 폴백이면 일부만 적힘)를 run_step 으로. C 의 주문 문장을
    적는 순간 깨뜨리면 accounts.C 에 `orders_pending` 이 남고, 리포트 ⚠️ 줄이 "적던 중" 을 말한다"""
    원래 = job._쓰기

    def 쓰기(client: Any, 문장: list) -> None:
        if any(sql == job.INSERT_ORDER_SQL and a and a[0] == "C" for sql, a in 문장):
            raise RuntimeError("쓰기 실패")
        원래(client, 문장)

    monkeypatch.setattr(job, "_쓰기", 쓰기)
    줄들: list[list[str]] = []
    원래단계 = job.run_step
    monkeypatch.setattr(job, "run_step", lambda *a, **k: (r := 원래단계(*a, **k), 줄들.append(r[0]))[0])
    기록 = _하루(c, monkeypatch, "2026-10-14")
    c기록 = 기록["accounts"]["C"]
    assert c기록["step_error"] == "RuntimeError: 쓰기 실패" and c기록.get("orders_pending", 0) >= 1 and "orders" not in c기록
    assert any("계좌 단계 실패(RuntimeError)" in x and "적던 중" in x for x in 줄들[0]), 줄들[0]


def test_베끼는_계좌의_단계_실패_줄에_주문_적은_곳을_붙인다() -> None:
    """25.1926 — H 계좌 줄을 겹쳐 내지 않으면서 잃던 "주문 N건은 적은 뒤" 를 단계 실패 줄에 붙인다(가상엔 있는데 KIS 엔
    안 베낀 주문 — 다음 날 괴리의 까닭). "이미 냄" 글은 "멈춤" 없이 그대로"""
    from batch.services import desk

    H = bm.MIRROR_ACCOUNT
    p = {"error": f"가상 {H} 계좌 단계 실패: KeyError: x",
         "accounts": {H: {"orders": 3, "step_error": "KeyError: x"}, "C": {"orders": "이미 냄", "step_error": "E: y"}}}
    글 = [x["text"] for x in desk.checks(halted=False, paper=p, broker=None, recon_bad=[], warnings=0)]
    assert any(t.startswith("가상모의투자 단계 실패:") and "주문 3건은 적은 뒤 — 그날 KIS 베끼기도" in t for t in 글), 글
    assert sum("계좌 단계 실패(" in t for t in 글) == 1  # C 줄만
    assert any(t.endswith("— 오늘 주문은 앞 실행이 이미 냄") for t in 글), 글
