"""결정 단위 선정 증거 (docs/paper.md 10.1, docs/desk.md 6장, docs/infra.md 25.1518)."""

from __future__ import annotations

from typing import Any

import pytest

from batch.services import paper as pp


def test_t_quantile_matches_table() -> None:
    """t 표(양쪽 아님, 한쪽 위 분위) — 자유도 3 이상에서 0.5% 안"""
    for p, df, 표 in ((0.95, 3, 2.353), (0.95, 5, 2.015), (0.95, 10, 1.812), (0.95, 30, 1.697), (0.99, 3, 4.541),
                     (0.99, 5, 3.365), (0.995, 5, 4.032), (0.995, 10, 3.169)):  # fmt: skip
        assert pp.t_quantile(p, df) == pytest.approx(표, rel=0.005)


def test_block_bound_by_hand() -> None:
    """묶음 평균 50·60·70·80 → 평균 65, 표준편차 12.91, 표준오차 6.455, 하한 65 − 2.353×6.455 = 49.81"""
    x = pp.block_bound({1: 50.0, 2: 60.0, 3: 70.0, 4: 80.0})
    assert x["n_blocks"] == 4 and x["mean"] == pytest.approx(65.0)
    assert x["se"] == pytest.approx(6.455, rel=1e-3) and x["lower"] == pytest.approx(65 - 2.353 * 6.455, abs=0.05)
    assert pp.block_bound({1: 50.0, 2: 60.0, 3: 70.0})["lower"] is None  # 묶음 셋 — 하한 안 냄
    assert pp.block_bound({})["mean"] is None


def _d(kind: str, day: str, ret: float, *, reason: str = "r", market: str = "KR", bench: float = 0.0) -> dict[str, Any]:
    return {"kind": kind, "decided_on": day, "ret_20d": ret, "bench_20d": bench, "reason": reason, "market": market,
            "stock_id": hash((kind, day, ret, reason)) % 10_000}  # fmt: skip


def test_selection_evidence_same_day_pool() -> None:
    """같은 날 같은 시장 새 후보 안 분위 — 시장 전체가 오른 몫은 빠진다"""
    rows = [
        # 10-01 국내: 산 것 +5 · 후보 +1·+2·+3·+10 → 3/4 = 75
        _d("buy", "2026-10-01", 5.0), *[_d("skip", "2026-10-01", r) for r in (1.0, 2.0, 3.0, 10.0)],
        # 같은 날 미국은 다른 풀 — 산 것 −1 · 후보 −5·−3·−2 → 100
        _d("buy", "2026-10-01", -1.0, market="US"), *[_d("skip", "2026-10-01", r, market="US") for r in (-5.0, -3.0, -2.0)],
        # 이미 든 종목(상한 도달)은 새 후보가 아니다 — 풀에서 뺀다. 그러면 후보 둘뿐이라 그날은 안 센다
        _d("buy", "2026-12-01", 5.0), _d("skip", "2026-12-01", 1.0), _d("skip", "2026-12-01", 9.0),
        _d("skip", "2026-12-01", 0.0, reason="종목 비중 상한 도달"),
    ]  # fmt: skip
    e = pp.selection_evidence(rows)
    assert e["days"] == 2 and e["n_blocks"] == 1
    assert list(e["blocks"].values()) == [pytest.approx(87.5)]
    # 초과(지수 대비) 묶음은 풀 크기와 상관없이 산 종목 모두 — 12-01 은 다른 묶음
    assert sorted(e["excess_blocks"].values()) == [pytest.approx(2.0), pytest.approx(5.0)]


def test_selection_evidence_ties_and_missing() -> None:
    rows = [_d("buy", "2026-10-01", 2.0), *[_d("skip", "2026-10-01", r) for r in (2.0, 2.0, 1.0, 3.0)],
            {**_d("skip", "2026-10-01", 0.0), "ret_20d": None}]  # fmt: skip
    e = pp.selection_evidence(rows)
    assert list(e["blocks"].values()) == [pytest.approx((1 + 0.5 * 2) / 4 * 100)]  # 같은 수는 절반


def _날(n: int) -> list[str]:
    from datetime import date, timedelta

    return [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def test_gate_selection_item() -> None:
    from batch.services import desk

    날 = _날(130)
    h = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]
    s = {"H": h, "R1": [(d, 1.0) for d in 날], "R2": [(d, 1.0) for d in 날]}

    def 칸(ev: dict | None) -> dict:
        g = desk.gate(s, "H", recon_bad_days=0, batch_failed=0, kis={"kept_pct": 95.0, "n": 200}, evidence=ev)
        return {i["key"]: i for i in g["items"]}["selection"]

    assert 칸({"lower": 51.0, "n_blocks": 6})["ok"]
    assert not 칸({"lower": 51.0, "n_blocks": 5})["ok"]  # 반년 묶음이 안 됨
    assert not 칸({"lower": 49.0, "n_blocks": 9})["ok"]  # 평균이 50 위여도 하한이 50 아래면 운일 수 있다
    assert not 칸(None)["ok"] and 칸(None)["now"] is None


def test_champion_needs_paired_decision_evidence_with_bonferroni() -> None:
    """블록 규칙을 넘은 C — 묶음 다섯에서 H 보다 매번 +2~+4%p 면 후보, 들쭉날쭉(+10·−8…)이면 아니다.
    도전자가 늘면 유의수준이 나뉘어 더 엄격해진다"""
    from batch.services import desk

    날 = _날(61)
    h = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]
    c = [(d, 1 + 0.002 * i) for i, d in enumerate(날)]
    챔 = {str(k): 1.0 for k in range(5)}
    꾸준 = {"C": {"excess_blocks": {str(k): 1.0 + v for k, v in enumerate((2.0, 3.0, 4.0, 2.5, 3.5))}},
          "H": {"excess_blocks": 챔}}  # fmt: skip
    r = {x["id"]: x for x in desk.champion({"H": h, "C": c}, "H", 꾸준)}["C"]
    assert r["blocks_ok"] and r["candidate"] and r["evidence_diff"]["n_blocks"] == 5
    assert r["evidence_diff"]["mean"] == pytest.approx(3.0)
    출렁 = {**꾸준, "C": {"excess_blocks": {str(k): 1.0 + v for k, v in enumerate((10.0, -8.0, 9.0, -6.0, 10.0))}}}
    assert not {x["id"]: x for x in desk.champion({"H": h, "C": c}, "H", 출렁)}["C"]["candidate"]
    # 도전자 여섯이면 유의수준 0.05/6 — 같은 C 도 하한이 낮아진다
    여럿 = {k: c for k in ("C", "D", "E", "G", "A", "B")} | {"H": h}
    rr = {x["id"]: x for x in desk.champion(여럿, "H", 꾸준)}["C"]
    assert rr["evidence_diff"]["alpha"] == pytest.approx(0.05 / 6) and rr["evidence_diff"]["lower"] < r["evidence_diff"]["lower"]


# ── 관문 지수 대비 (25.1519) ─────────────────────────────────────────────────


def test_index_return_by_hand_with_deposit() -> None:
    """시작 KOSPI 그림자 1,000(입금 1,000) → 끝 1,650(그 사이 입금 500 → 그 돈만큼 빼면 1,150) = +15%,
    S&P 1,000 → 1,550 − 500 = +5% → 반반 +10%. 한쪽이 비면 다른 쪽만"""
    from batch.services import desk

    rows = [("2026-01-01", 1000.0, 1000.0, 1000.0), ("2026-02-01", 1200.0, None, 1000.0), ("2026-03-01", 1650.0, 1550.0, 1500.0)]
    assert desk.index_return(rows, "2026-01-15") == pytest.approx(10.0)
    assert desk.index_return([(d, k, None, i) for d, k, _s, i in rows], "2026-01-01") == pytest.approx(15.0)
    assert desk.index_return(rows, "2025-12-31") is None  # 시작 앞 행 없음


def test_gate_vs_index_item() -> None:
    """R 은 이겨도 지수에 지면 관문 통과 아님 — 실전 돈의 기회비용은 지수 ETF"""
    from batch.services import desk

    날 = _날(130)
    h = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]  # 약 +12.9%
    s = {"H": h, "R1": [(d, 1.0) for d in 날], "R2": [(d, 1.0) for d in 날]}

    def 칸(지수끝: float) -> dict:
        g = desk.gate(s, "H", recon_bad_days=0, batch_failed=0, kis=None,
                      index=[(날[0], 1000.0, 1000.0, 1000.0), (날[-1], 지수끝, 지수끝, 1000.0)])  # fmt: skip
        return {i["key"]: i for i in g["items"]}

    assert 칸(1100.0)["vs_index"]["ok"] and 칸(1100.0)["vs_r"]["ok"]
    지 = 칸(1200.0)
    assert 지["vs_r"]["ok"] and not 지["vs_index"]["ok"] and 지["vs_index"]["index_pct"] == pytest.approx(20.0)
    assert desk.gate(s, "H", recon_bad_days=0, batch_failed=0, kis=None)["items"][2]["now"] is None  # 그림자 없음 → 모름


# ── 판 사전 등록 (25.1520) ──────────────────────────────────────────────────


def test_every_current_rule_version_is_preregistered() -> None:
    """계좌 표(마이그레이션)의 지금 판이 모두 사전 등록에 있다 — 판을 올리는 커밋이 가설을 같이 적지 않으면 여기서 깨진다"""
    from batch.services import desk
    from tests.test_paper_job_1445 import Client

    c = Client(accounts=None, charters=True)
    빠짐 = [(a, v) for a, v in c.q("SELECT id, rule_version FROM paper_accounts") if (str(a), int(v)) not in desk.PREREG]
    assert not 빠짐, f"사전 등록 없는 판: {빠짐} — batch/services/desk.py PREREG 에 가설을 적는다(docs/paper.md 2.5)"
    for x in desk.PREREG.values():
        assert x.get("hypothesis")


def test_rule_history_carries_prereg() -> None:
    from batch.services import desk

    h = desk.rule_history([("H", 1, ""), ("Z", 9, "")], {"H": [("2026-01-01", 1.0)], "Z": [("2026-01-01", 1.0)]})
    assert h["H"][0]["prereg"]["hypothesis"].startswith("회사들의 성적 가중 합의") and h["H"][0]["prereg"]["retro"]
    assert h["H"][0]["prereg"]["metric"] == desk.PREREG_METRIC
    assert h["Z"][0]["prereg"] is None  # 등록 없이 연 판
