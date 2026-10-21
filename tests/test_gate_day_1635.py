"""관문 계측 — 하루 수익(관문이 본 값)과 까닭 코드별 막힘 수를 실행 기록에 (36회차 P1 계측·P2 ①, docs/infra.md 25.1635)."""

from __future__ import annotations

import pytest

from tests.test_risk_gate_1629 import _멈춤_계좌, _종목4로_사봄
from tests.test_risk_gate_1629 import _틀 as _틀  # noqa: F401 — 같은 자동 설정(배분 고르기 고정)


def test_진짜_하락일엔_하루_수익과_막힘_까닭이_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    멈춤: set[int] = set()
    c = _멈춤_계좌(monkeypatch, 멈춤)
    a = _종목4로_사봄(c, monkeypatch, 13_500, 8_800)  # 종목 3 −12%
    g = a["gate_day"]
    assert g["raw"] == g["gate"] and g["raw"] < -3 and g["prev"] == "2026-10-16" and g["prev_at"]
    assert a["risk_blocked_by"] == {"daily_loss": a["risk_blocked"]}


def test_멈춤으로_보정한_날엔_원래_값과_관문_값이_갈린다(monkeypatch: pytest.MonkeyPatch) -> None:
    멈춤: set[int] = set()
    c = _멈춤_계좌(monkeypatch, 멈춤)
    멈춤.add(1)
    a = _종목4로_사봄(c, monkeypatch, 13_500, 10_000)  # 시세 그대로 — 멈춤만
    g = a["gate_day"]
    assert g["raw"] < -3 < g["gate"] and a["day_loss_basis"] == "보정"
    assert "risk_blocked_by" not in a  # 막힌 것 없음


def test_매도_관문_막힘도_까닭_코드로_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    """교차검증 [권고] — 매도 쪽 막힘을 안 세도 시험이 통과했다. 멈춤 스위치 날 손절 매도가 막히면 halt 로 센다."""
    from batch.services import sell_flags as sf

    멈춤: set[int] = set()
    c = _멈춤_계좌(monkeypatch, 멈춤)
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('paper_halt', '{\"on\": true}', 't')")
    monkeypatch.setattr(sf, "evaluate", lambda h, _t, _r=None: [sf.Flag(h.stock_id, "red", "손절", "x", [])])
    a = _종목4로_사봄(c, monkeypatch, 13_500, 10_000)
    # 막힌 것은 모두 멈춤 스위치 — 매도(보유 둘)와 매수 회차를 함께 센 `risk_blocked` 와 같아야 한다
    assert a["risk_blocked"] >= 3 and a["risk_blocked_by"] == {"halt": a["risk_blocked"]}
