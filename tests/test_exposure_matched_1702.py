"""관문 R 대비 옆 참고 칸 — 노출을 H 에 맞춘 R (docs/desk.md 6장, docs/infra.md 25.1702, H·R 감사 [필수] 1 고칠 방향 2)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from batch.services import desk


def _날(n: int) -> list[str]:
    return [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def test_by_hand_two_days() -> None:
    """R 은 다 투자(현금 0)·하루 +1% 두 번, H 는 70% 투자 — R 주식 몫 +1% × 0.7 = +0.7% 두 번 = +1.4049%"""
    d = _날(3)
    s = {"H": [(x, 1.0) for x in d], "R1": [(d[0], 1.0), (d[1], 1.01), (d[2], 1.0201)]}
    c = {"H": [(x, 30.0, 100.0) for x in d], "R1": [(x, 0.0, 100.0) for x in d]}
    m = desk.exposure_matched_r(s, c, "H", d[0])
    assert m == {"r_pct": pytest.approx((1.007**2 - 1) * 100), "h_exposure_pct": pytest.approx(70.0),
                 "r_exposure_pct": pytest.approx(100.0), "skipped_days": 0}  # fmt: skip
    # R 이 반만 투자해 하루 +1% 였다면 주식 몫은 +2% — H 노출 70% 면 +1.4%
    c2 = {**c, "R1": [(x, 50.0, 100.0) for x in d]}
    assert desk.exposure_matched_r(s, c2, "H", d[0])["r_pct"] == pytest.approx((1.014**2 - 1) * 100)


def test_low_exposure_days_skipped_and_missing_cash_unknown() -> None:
    d = _날(3)
    s = {"H": [(x, 1.0) for x in d], "R1": [(d[0], 1.0), (d[1], 1.0), (d[2], 1.01)]}
    c = {"H": [(x, 0.0, 100.0) for x in d], "R1": [(d[0], 99.0, 100.0), (d[1], 0.0, 100.0), (d[2], 0.0, 100.0)]}
    m = desk.exposure_matched_r(s, c, "H", d[0])
    assert m["skipped_days"] == 1 and m["r_pct"] == pytest.approx(1.0)  # 첫날 R 투자 1% — 뺌
    assert desk.exposure_matched_r(s, {**c, "H": c["H"][1:]}, "H", d[0]) is None  # H 첫날 현금 없음 — 모름


def test_gate_reference_only_and_cash_drag_case() -> None:
    """감사 재현 꼴 — 종목 수익은 같고 투자 비중만 H 70%·R 100%. 하루 −0.2% 장에서 원래 R 대비는 현금 몫으로 + 이지만
    노출을 맞추면 0 근처. 칸은 참고 — 통과 판정(`ok`)은 원래 값 그대로"""
    날 = _날(130)
    h = [(x, (1 - 0.7 * 0.002) ** i) for i, x in enumerate(날)]
    r = [(x, (1 - 0.002) ** i) for i, x in enumerate(날)]
    c = {"R1": [(x, 0.0, 100.0) for x in 날], "R2": [(x, 0.0, 100.0) for x in 날]}
    g = desk.gate({"H": h, "R1": r, "R2": r}, "H", recon_bad_days=0, batch_failed=0, kis=None,
                  cash=[(x, 30.0, 100.0) for x in 날], control_cash=c)  # fmt: skip
    칸 = {i["key"]: i for i in g["items"]}["vs_r"]
    assert 칸["now"] > 3 and 칸["ok"]  # 현금만으로 +3%p 넘게 — 그대로 통과(참고 칸은 판정에 안 듦)
    assert 칸["exposure_matched"]["vs_r"] == pytest.approx(0.0, abs=1e-9)
    assert 칸["exposure_matched"]["h_exposure_pct"] == pytest.approx(70.0)
    # 현금 계열을 안 주면 칸은 비어 있다(옛 호출)
    g2 = desk.gate({"H": h, "R1": r, "R2": r}, "H", recon_bad_days=0, batch_failed=0, kis=None)
    assert {i["key"]: i for i in g2["items"]}["vs_r"]["exposure_matched"] is None


def test_job_passes_r_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    """운용 요약이 R1·R2 의 현금·평가 계열을 관문에 넘긴다 — 같은 `paper_daily` 행이라 읽기는 그대로"""
    import json
    from datetime import UTC, datetime

    from batch.jobs import desk as job
    from tests.test_paper_job_1445 import Client

    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for aid, cash in (("B", 300.0), ("R1", 0.0), ("R2", 0.0)):
        for i, as_of in enumerate(("2026-10-14", "2026-10-15", "2026-10-16")):
            c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw,"
                           " pnl_krw, twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1000, ?, ?, 1000, 0,"
                           " ?, '[]', 1, 't')", [aid, as_of, cash, 1000 - cash, 1 + 0.01 * i])  # fmt: skip
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    m = {i["key"]: i for i in s["gate"]["items"]}["vs_r"]["exposure_matched"]
    assert m is not None and m["h_exposure_pct"] == pytest.approx(70.0) and m["r_exposure_pct"] == pytest.approx(100.0)
