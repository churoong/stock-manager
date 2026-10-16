"""KIS모의투자 시험 주문 스크립트 (scripts/probe_kis_vts.py, docs/infra.md 25.1491) — 가짜 세션으로 흐름만 본다."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from batch.sources import kis_vts as kv
from tests.test_kis_vts_1475 import _세션

_spec = importlib.util.spec_from_file_location(
    "probe_kis_vts", Path(__file__).resolve().parent.parent / "scripts" / "probe_kis_vts.py")
probe = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(probe)  # type: ignore[union-attr]


class _가짜:
    """시장가 주문은 곧바로 다 찬다. 잔고는 산 만큼 늘고 판 만큼 준다."""

    def __init__(self, fill: bool = True) -> None:
        self.q, self.fill, self.orders, self.calls = 0, fill, [], 0
        self.ex: list[kv.Execution] = []

    def order(self, side: str, code: str, q: int, price: Any) -> kv.Accepted:
        no = f"000{len(self.orders) + 1}"
        self.orders.append((side, code, q, price))
        찬 = q if self.fill else 0
        self.q += 찬 if side == "buy" else -찬
        self.ex.append(kv.Execution("20261016", no, code, side, q, 찬, 70_000.0 if 찬 else None, 0, False))
        return kv.Accepted(no, None)

    def executions(self, start: str, end: str) -> tuple[list[kv.Execution], bool]:
        return self.ex, True

    def balance(self) -> tuple[list[kv.Holding], dict]:
        return ([kv.Holding("005930", self.q, self.q, 70_000.0)] if self.q else []), {}


def _kst(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-16T{hhmm}:00+09:00").astimezone(UTC)


def test_kr_buys_then_sells_and_leaves_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    s = _가짜()
    assert probe.kr(s, "005930", _kst("11:10")) is True
    assert s.orders == [("buy", "005930", 1, None), ("sell", "005930", 1, None)] and s.q == 0
    assert "처음 0주와 같음" in capsys.readouterr().out


def test_kr_does_not_sell_when_buy_did_not_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "WAIT_S", 0)
    monkeypatch.setattr(probe, "POLL_S", 0)
    s = _가짜(fill=False)
    assert probe.kr(s, "005930", _kst("11:10")) is False
    assert [o[0] for o in s.orders] == ["buy"]


def test_kr_outside_hours_places_no_order() -> None:
    s = _가짜()
    assert probe.kr(s, "005930", _kst("16:00")) is True and s.orders == []


def test_buyable_us_is_read_only_mock_call(monkeypatch: pytest.MonkeyPatch) -> None:
    s, http = _세션(monkeypatch, [({"rt_cd": "0", "output": {"ord_psbl_frcr_amt": "0", "echm_af_ord_psbl_qty": "70"}}, "")])
    assert s.buyable_us("AAPL", "NASD", 100.0)["echm_af_ord_psbl_qty"] == "70"
    url, kw = http.sent[0]
    assert url == kv.BASE + kv.US_PSAMOUNT_PATH and kw["headers"]["tr_id"] == "VTTS3007R"
    assert kw["params"] == {"CANO": "12345678", "ACNT_PRDT_CD": "01", "OVRS_EXCG_CD": "NASD", "OVRS_ORD_UNPR": "100.00",
                            "ITEM_CD": "AAPL"}  # fmt: skip


def test_diagnosis_never_prints_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIS_VTS_APP_KEY", "secretkey")
    monkeypatch.delenv("KIS_VTS_APP_SECRET", raising=False)
    monkeypatch.setenv("KIS_VTS_ACCOUNT", "5012-3456 ")
    글 = probe._진단()
    assert "앱키 있음" in 글 and "시크릿 없음" in 글 and "숫자 8자리 + 그 밖 글자 2개" in 글
    assert "secretkey" not in 글 and "5012" not in 글 and "3456" not in 글
