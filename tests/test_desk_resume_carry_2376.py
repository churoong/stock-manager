"""같은 거래일 이어 돈 실행이 운용 요약 시장 칸의 계좌 기록을 잃지 않는다 (docs/infra.md 25.2376, 결함 루프 2026-11-26).

이어 돌기(25.2144·25.2240·같은 날 손 재실행)는 가상 단계를 다시 돌지만 그날 결정이 있는 계좌는 `orders: "이미 냄"` 만
남겨, 시장 칸을 통째로 바꾸면 첫 실행의 높음 확인할 일과 지주사 기록(`holding`)이 사라졌다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.jobs import desk as job
from batch.services import desk
from tests.test_paper_job_1445 import Client

앞 = {"trade_date": "2026-11-25", "holding": {"weights": {"A": 1.1}},
      "checks": [{"level": "high", "text": "가상 가치 투자위원회를 열지 못함 — 오늘 사지 않았습니다: x"},
                 {"level": "normal", "text": "이 실행의 경고 3줄 — 시스템 상태 화면에서", "kind": "warnings"},
                 {"level": "high", "text": "멈춤 스위치", "kind": "global"}]}  # fmt: skip


def test_이어_돈_실행은_앞_칸을_잇는다() -> None:
    이번 = {"trade_date": "2026-11-25", "holding": None, "checks": [{"level": "normal", "text": "KIS모의투자 거부 1건"}]}
    out = desk.carry_same_day(앞, 이번, resumed=True)
    assert out["holding"] == {"weights": {"A": 1.1}}
    글 = [c["text"] for c in out["checks"]]
    assert 글 == ["앞 실행 — 가상 가치 투자위원회를 열지 못함 — 오늘 사지 않았습니다: x", "KIS모의투자 거부 1건"]  # 높음 먼저
    # 경고 줄 수·멈춤은 이번 실행 것이 맞다 — 잇지 않는다
    assert not any("경고 3줄" in t or "멈춤" in t for t in 글)


def test_세번째_실행도_머리를_겹치지_않는다() -> None:
    한번 = desk.carry_same_day(앞, {"trade_date": "2026-11-25", "holding": None, "checks": []}, resumed=True)
    두번 = desk.carry_same_day(한번, {"trade_date": "2026-11-25", "holding": None, "checks": []}, resumed=True)
    assert [c["text"] for c in 두번["checks"]] == ["앞 실행 — 가상 가치 투자위원회를 열지 못함 — 오늘 사지 않았습니다: x"]


@pytest.mark.parametrize(("앞칸", "resumed"), [(앞, False), ({**앞, "trade_date": "2026-11-24"}, True), (None, True)])
def test_잇지_않는_때(앞칸: dict | None, resumed: bool) -> None:
    """이어 돈 실행이 아니거나(첫 실행) 거래일이 다르거나(새 날) 앞 칸이 없으면 이번 칸 그대로"""
    이번 = {"trade_date": "2026-11-25", "holding": None, "checks": []}
    assert desk.carry_same_day(앞칸, 이번, resumed) is 이번


def test_이번_지주사_기록이_있으면_이번_것() -> None:
    이번 = {"trade_date": "2026-11-25", "holding": {"weights": {"A": 0.9}}, "checks": []}
    assert desk.carry_same_day(앞, 이번, resumed=True)["holding"] == {"weights": {"A": 0.9}}


def test_write_가_두번째_실행에서_잇는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    지금 = datetime(2026, 10, 15, 23, 40, tzinfo=UTC)
    첫 = {"paper": {"accounts": {"B": {"recon_ok": True, "holding": {"weights": {"A": 1.1}},
                                     "step_error": "RuntimeError: x"}}}}  # fmt: skip
    job.write(c, "KR", 지금, trade_date="2026-10-15", session_date="2026-10-16", step_log=첫, warnings=[])
    s = json.loads(c.q("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-16'")[0][0])
    첫줄 = [x["text"] for x in s["markets"]["KR"]["checks"]]
    assert 첫줄 and s["markets"]["KR"]["holding"] == {"weights": {"A": 1.1}}
    둘 = {"paper": {"accounts": {"B": {"orders": "이미 냄"}}}}
    job.write(c, "KR", 지금.replace(minute=50), trade_date="2026-10-15", session_date="2026-10-16", step_log=둘, warnings=[])
    s = json.loads(c.q("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-16'")[0][0])
    kr = s["markets"]["KR"]
    assert kr["holding"] == {"weights": {"A": 1.1}}
    assert {desk.CARRIED_HEAD + t for t in 첫줄 if not t.startswith("멈춤")} <= {x["text"] for x in kr["checks"]}


def test_이번_실행이_KIS_를_돌았으면_앞_KIS_줄은_잇지_않는다() -> None:
    """25.2380 (25.2376 교차검증 [권고]) — 늦게 여는 날 08:27 첫 실행의 "접수 시작 전 — 베낄 주문 3건이 남았습니다" 가
    09:35 이어 돈 실행이 다 낸 뒤에도 "앞 실행 —" 높음으로 남았다"""
    앞칸 = {"trade_date": "d", "holding": None, "checks": desk.checks(
        halted=False, paper={}, broker={"send": "접수 시작 전 — 보내지 않음"}, recon_bad=[], warnings=0, h_orders=3)}
    assert 앞칸["checks"] and all(c.get("kind") == "kis" for c in 앞칸["checks"])
    보냄 = {"trade_date": "d", "holding": None, "kis_step": {"sent": {"accepted": 3}}, "checks": []}
    assert desk.carry_same_day(앞칸, 보냄, resumed=True)["checks"] == []
    # 이번 실행이 KIS 를 안 돌았으면(기록 없음) 앞 KIS 줄은 그날 있었던 일 — 잇는다
    안돎 = {"trade_date": "d", "holding": None, "kis_step": {}, "checks": []}
    assert [c["text"].startswith(desk.CARRIED_HEAD) for c in desk.carry_same_day(앞칸, 안돎, resumed=True)["checks"]] == [True]


def test_표지_전_멈춤_줄도_잇지_않는다() -> None:
    """25.2375 앞에 쓴 칸의 멈춤 줄은 `kind: global` 이 없다 — 배포 당일 "앞 실행 — 멈춤…" 으로 남지 않게"""
    앞칸 = {"trade_date": "d", "checks": [{"level": "high", "text": "멈춤 스위치가 켜져 있어 가상·KIS모의투자 모두 새 주문을 내지 않습니다"}]}
    assert desk.carry_same_day(앞칸, {"trade_date": "d", "holding": None, "checks": []}, True)["checks"] == []
