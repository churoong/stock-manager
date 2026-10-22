"""판마다 사전 등록 지표의 오늘 값 (docs/desk.md 3장, docs/infra.md 25.1710, 결정 원장 감사 [권고] 7)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from batch.services import desk


def _날(n: int) -> list[str]:
    return [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def test_prereg_status_uses_each_versions_window() -> None:
    """A 는 판 3(날[40] 결정 뒤), C 는 판 2 — 관문과 같은 식을 계좌마다 자기 판 구간으로. 대조는 뺀다"""
    날 = _날(80)
    a = [(d, 1 + 0.002 * i) for i, d in enumerate(날)]
    r = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]
    s = {"A": a, "C": a, "R1": r, "R2": r}
    지수 = {"A": [(날[0], 1000.0, 1000.0, 1000.0), (날[-1], 1000.0, 1000.0, 1000.0)]}
    out = desk.prereg_status(s, {"A": (3, 날[40]), "C": (2, ""), "R1": (1, "")},
                             {"A": {"lower": 55.0, "n_blocks": 6}}, 지수)  # fmt: skip
    assert set(out) == {"A", "C"}
    assert out["A"]["version"] == 3 and out["A"]["from"] == 날[41] and out["A"]["points"] == 39
    assert (out["A"]["lower"], out["A"]["n_blocks"]) == (55.0, 6) and out["C"]["n_blocks"] == 0
    assert out["A"]["vs_r"] == pytest.approx(((a[-1][1] / a[41][1]) - (r[-1][1] / r[41][1])) * 100)
    assert out["A"]["vs_index"] is not None and out["C"]["vs_index"] is None  # C 는 그림자 없음 — 모름


def test_job_puts_prereg_status_in_daily_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import desk as job
    from tests.test_paper_job_1445 import Client

    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for aid in ("A", "B", "R1"):
        for i, as_of in enumerate(("2026-10-14", "2026-10-15", "2026-10-16")):
            c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw,"
                           " pnl_krw, twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1000, 0, 1000, 1000, 0,"
                           " ?, '[]', 1, 't')", [aid, as_of, 1 + 0.01 * i])  # fmt: skip
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    assert {"A", "B"} <= set(s["prereg_status"]) and "R1" not in s["prereg_status"]
    assert s["prereg_status"]["A"]["points"] >= 1
