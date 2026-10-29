"""관문 가상 대조 칸 — 산 새 종목의 무작위 대조 분위 묶음 하한 (docs/desk.md 6장, docs/infra.md 25.1697).

관문 "R 대비" 는 H 와 R 의 규칙 차이(진입·매도·투자 속도)를 잰다 — 선정 실력만 재는 칸을 AND 로 더했다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from batch.services import desk
from batch.services import paper as pp


def _d(kind: str, day: str, ret: float, ctl: float | None, *, held: bool = False, market: str = "KR") -> dict[str, Any]:
    return {"kind": kind, "decided_on": day, "ret_20d": ret, "bench_20d": 0.0, "reason": "r", "market": market,
            "stock_id": hash((kind, day, ret)) % 10_000, "control_pct": ctl,
            "detail": {"held": True} if held else {}}  # fmt: skip


def test_control_blocks_by_hand(monkeypatch: pytest.MonkeyPatch) -> None:
    """날마다 산 새 종목의 대조 분위 평균 → 28일 묶음 평균 → 하한. 같은 날 후보 풀이 모자라도(선정 분위는 안 셈)
    대조 분위는 센다 — 대조군은 유니버스 무작위 20종목이라 그날 후보 수와 무관"""
    monkeypatch.setattr(pp, "EVIDENCE_BLOCK_MIN_DAYS", 1)
    rows = [
        # 10-01 국내 두 매수 80·60 → 70, 같은 날 미국 한 매수 90 — 시장마다 그날 평균을 따로 내 묶음에 넣는다 → (70+90)/2
        _d("buy", "2026-10-01", 5.0, 80.0), _d("buy", "2026-10-01", 3.0, 60.0),
        _d("buy", "2026-10-01", 1.0, 90.0, market="US"),
        # 추가 매수(이미 든 종목)는 새 선정이 아니다 — 대조 분위 0 이어도 빠진다
        _d("buy", "2026-10-01", -9.0, 0.0, held=True),
        # 대조가 모자라 분위가 없는 매수(None)는 뺀다
        _d("buy", "2026-10-01", 2.0, None),
        # 안 산 후보는 대조 분위가 있어도 세지 않는다
        _d("skip", "2026-10-01", 9.0, 100.0),
    ]  # fmt: skip
    e = pp.selection_evidence(rows)
    assert e["days"] == 0 and e["n_blocks"] == 0  # 후보 풀이 없어 선정 분위는 안 셈
    assert e["control"]["n_blocks"] == 1 and e["control"]["mean"] == pytest.approx(80.0)
    assert list(e["control"]["blocks"].values()) == [pytest.approx(80.0)]
    assert e["control"]["lower"] is None  # 묶음 하나 — 하한 안 냄


def test_control_lower_bound_and_min_days() -> None:
    """묶음 넷(서로 떨어짐) 50·60·70·80 → 하한 65 − 2.353 × 6.455. 결정일이 셋 못 되는 묶음은 세지 않는다"""
    rows = []
    for k, v in enumerate((50.0, 60.0, 70.0, 80.0)):
        첫 = date.fromordinal((date(2026, 1, 1).toordinal() // pp.EVIDENCE_BLOCK_DAYS + 2 * k) * pp.EVIDENCE_BLOCK_DAYS)
        rows += [_d("buy", (첫 + timedelta(days=i)).isoformat(), 1.0, v) for i in range(3)]
    끝 = date.fromordinal((date(2026, 1, 1).toordinal() // pp.EVIDENCE_BLOCK_DAYS + 20) * pp.EVIDENCE_BLOCK_DAYS)
    rows += [_d("buy", 끝.isoformat(), 1.0, 0.0), _d("buy", (끝 + timedelta(days=1)).isoformat(), 1.0, 0.0)]  # 이틀뿐
    c = pp.selection_evidence(rows)["control"]
    assert c["n_blocks"] == 4 and c["mean"] == pytest.approx(65.0)
    assert c["lower"] == pytest.approx(65 - 2.353 * 6.455, abs=0.05)


def _날(n: int) -> list[str]:
    return [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def test_gate_control_item_is_and() -> None:
    """R·지수·결정 단위 선정을 다 넘어도 가상 대조 하한이 50 이하이거나 묶음이 모자라면 통과 아님"""
    날 = _날(130)
    h = [(d, 1 + 0.001 * i + (0.002 if i % 2 else 0)) for i, d in enumerate(날)]
    r = [(d, 1 + (0.01 if i % 2 else -0.01)) for i, d in enumerate(날)]
    지수 = [(날[0], 1000.0, 1000.0, 1000.0), (날[-1], 1000.0, 1000.0, 1000.0)]

    def 관문(ctl: dict[str, Any] | None) -> dict[str, Any]:
        # 25.1963 뒤로 하한은 평균·표준오차에서 Pocock 문턱으로 — 60 − t(0.9779, 5) × 2 ≈ 54.5
        ev = {"mean": 60.0, "se": 2.0, "lower": 55.0, "n_blocks": 6, **({"control": ctl} if ctl is not None else {})}
        return desk.gate({"H": h, "R1": r, "R2": r}, "H", recon_bad_days=0, batch_failed=0,
                         kis={"kept_pct": 95.0, "n": 200}, evidence=ev, index=지수)  # fmt: skip

    assert 관문({"mean": 60.0, "se": 2.0, "lower": 55.0, "n_blocks": 6})["passed"]
    # 52 − t(0.9779, 8) × 2 ≈ 47.3 · 묶음 5 · 표준오차 없음 · 증거 없음 · 95% 하한만 있고 평균·표준오차 없음
    for ctl in ({"mean": 52.0, "se": 2.0, "lower": 50.0, "n_blocks": 9}, {"mean": 70.0, "se": 2.0, "n_blocks": 5},
                {"n_blocks": 3, "mean": 70.0}, None, {"lower": 51.0, "n_blocks": 6}):  # fmt: skip
        g = 관문(ctl)
        칸 = {i["key"]: i for i in g["items"]}
        assert not g["passed"] and not 칸["control"]["ok"], ctl
        assert all(i["ok"] for i in g["items"] if i["key"] != "control")  # 다른 칸은 그대로 통과
    칸 = {i["key"]: i for i in 관문({"n_blocks": 3, "mean": 70.0})["items"]}["control"]
    assert (칸["now"], 칸["n"], 칸["mean"], 칸["target"]) == (None, 3, 70.0, 50.0)


def test_control_counts_first_buy_per_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1706 (25.1697 교차검증 [권고] 1) — 같은 종목이 사흘 내리 새 매수 결정이 되어도(분할 지정가가 안 참) 첫 날만.
    1 번 종목 90 이 10-01·02·03, 2 번 종목 30 이 10-02 → 날 평균 90·30 → 묶음 60 (되풀이를 세면 90·60·90 → 80)"""
    monkeypatch.setattr(pp, "EVIDENCE_BLOCK_MIN_DAYS", 1)
    rows = [{**_d("buy", d, 1.0, 90.0), "stock_id": 1} for d in ("2026-10-01", "2026-10-02", "2026-10-03")]
    rows.append({**_d("buy", "2026-10-02", 1.0, 30.0), "stock_id": 2})
    c = pp.selection_evidence(rows)["control"]
    assert c["n_blocks"] == 1 and c["mean"] == pytest.approx(60.0)
