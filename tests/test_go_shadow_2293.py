"""주문 허가 판정(GO / 매도만 / NO-GO)의 그림자 기록 (docs/paper.md 15.13, docs/infra.md 25.2293).

docs/roadmap.md 1장 #2 의 첫 조각. KIS모의투자 단계 기록에서 끌어내 기록만 한다 — 주문을 막지 않는다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from batch.jobs import broker_mirror as jb
from batch.services import broker_mirror as bm

뿌리 = Path(__file__).resolve().parent.parent


def test_아무_것도_안_걸리면_go() -> None:
    평소 = {"reconciled": {"filled": 3, "unfilled": 1}, "diff": 0, "holdings": 12, "halted": False,
           "sent": {"accepted": 4}, "planned": {"cleanup": 0, "mirror": 4}}  # fmt: skip
    assert bm.go_shadow(평소) == {"verdict": "go", "reasons": []}
    assert bm.go_shadow({}) == {"verdict": "go", "reasons": []}


@pytest.mark.parametrize(("칸", "값"), [("halted", True), ("balance_truncated", True), ("send", "휴장일")])
def test_지금도_주문을_내지_않는_경우는_no_go(칸: str, 값: object) -> None:
    assert bm.go_shadow({칸: 값}) == {"verdict": "no_go", "reasons": [칸]}


@pytest.mark.parametrize(("기록", "갈래"), [
    ({"reconciled": {"filled": 2, "unknown": 1}}, "reconcile_unknown"),
    ({"reconcile_truncated": True}, "reconcile_truncated"),
    ({"diff": 2}, "diff"),
    ({"unknown_codes": 1}, "unknown_codes"),
])  # fmt: skip
def test_체결을_모르거나_잔고가_어긋나면_매도만(기록: dict, 갈래: str) -> None:
    assert bm.go_shadow(기록) == {"verdict": "sell_only", "reasons": [갈래]}


def test_둘_다_걸리면_no_go_이고_까닭은_모두_남는다() -> None:
    r = bm.go_shadow({"send": "접수 시작 전", "diff": 1, "reconciled": {"unknown": 2}, "halted": False})
    assert r == {"verdict": "no_go", "reasons": ["send", "reconcile_unknown", "diff"]}


def test_0_과_거짓과_빈_글은_걸리지_않는다() -> None:
    깨끗 = {"halted": False, "balance_truncated": False, "send": "", "reconciled": {"unknown": 0}, "diff": 0,
           "reconcile_truncated": False, "unknown_codes": 0}  # fmt: skip
    assert bm.go_shadow(깨끗)["verdict"] == "go"
    # 맞추기 칸이 dict 가 아니어도(예전 기록 꼴) 죽지 않는다
    assert bm.go_shadow({"reconciled": "모름"})["verdict"] == "go"
    # 기록의 다른 칸(체결과 맞은 수·보낸 수)은 판정에 쓰지 않는다
    assert bm.go_shadow({"reconciled": {"rejected": 5, "unfilled": 9}, "not_sent": {"x": 3}})["verdict"] == "go"


def test_갈래는_상수가_정의처다() -> None:
    assert list(bm.GO_NO_GO) == ["halted", "balance_truncated", "send"]
    assert list(bm.GO_SELL_ONLY) == ["reconcile_unknown", "reconcile_truncated", "diff", "unknown_codes"]
    assert not set(bm.GO_NO_GO) & set(bm.GO_SELL_ONLY)


def test_단계_기록의_꼬리에_실리고_깨져도_단계는_끝난다(monkeypatch: pytest.MonkeyPatch) -> None:
    세션 = type("S", (), {"calls": 3, "rate_retries": 0, "net_retries": 0})()
    r = jb._끝({"diff": 1, "holdings": 5}, 세션)
    assert r == {"diff": 1, "holdings": 5, "go_shadow": {"verdict": "sell_only", "reasons": ["diff"]}, "calls": 3}

    def 깨짐(_기록: dict) -> dict:
        raise ValueError("판정이 깨졌다")

    monkeypatch.setattr(jb.bm, "go_shadow", 깨짐)
    assert jb._끝({"diff": 1}, 세션) == {"diff": 1, "go_shadow": {"error": "ValueError"}, "calls": 3}


def test_기록만_한다_주문을_막거나_바꾸지_않는다() -> None:
    """판정을 읽는 코드가 없다 — 켜는 일은 따로(그림자 기록이 쌓인 뒤, 사용자 결정)"""
    글 = inspect.getsource(jb.run)
    assert "go_shadow" not in 글  # 주문을 내는 본문은 판정을 보지 않는다(꼬리 `_끝` 만 만든다)
    for 파일 in ("batch/jobs/broker_mirror.py", "batch/jobs/intraday_guard.py", "batch/jobs/paper.py", "batch/jobs/daily.py"):
        본문 = (뿌리 / 파일).read_text()
        assert 'get("go_shadow")' not in 본문 and '["go_shadow"]' not in 본문, 파일
    assert "execute" not in inspect.getsource(bm.go_shadow) and "client" not in inspect.getsource(bm.go_shadow)


def test_문서가_갈래와_읽는_법을_적는다() -> None:
    글 = (뿌리 / "docs" / "paper.md").read_text()
    for 조각 in ("### 15.13 주문 허가 판정", "25.2293", "`go_shadow`", "주문을 막지 않는다", "`sell_only`", "`no_go`",
               *(f"`{k}`" for k in (*bm.GO_NO_GO, *bm.GO_SELL_ONLY)), "새 코드 첫날", "오탐"):  # fmt: skip
        assert 조각 in 글, 조각
