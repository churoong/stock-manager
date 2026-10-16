"""실전 전환 관문·위험판·챔피언 교체 후보 (docs/desk.md 6장, docs/infra.md 25.1508) — 손으로 낸 값."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.jobs import desk as job
from batch.services import desk
from tests.test_paper_job_1445 import Client


def _날(n: int) -> list[str]:
    from datetime import date, timedelta

    return [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def test_risk_ratio_by_hand() -> None:
    """수익률 +1%, −1% 를 번갈아 21개 → 평균 ≈ 0. 너무 적으면 모름"""
    v, t = [], 1.0
    for i, d in enumerate(_날(22)):
        v.append((d, t))
        t *= 1.01 if i % 2 == 0 else 0.99
    r = desk.risk_ratio(v)
    xs = [b / a - 1 for (_x, a), (_y, b) in zip(v, v[1:], strict=False)]
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    assert r == pytest.approx(m / sd)
    assert desk.risk_ratio(v[:20]) is None  # 수익률 19개


def test_gate_items_and_pass() -> None:
    날 = _날(130)
    h = [(d, 1 + 0.001 * i + (0.002 if i % 2 else 0)) for i, d in enumerate(날)]  # 꾸준히 오름
    r = [(d, 1 + (0.01 if i % 2 else -0.01)) for i, d in enumerate(날)]  # 제자리 출렁
    series = {"H": h, "R1": r, "R2": r}
    board = desk.leaderboard(series)
    g = desk.gate(series, board, "H", recon_bad_days=0, batch_failed=0, kis={"kept_pct": 92.0, "n": 120})
    칸 = {i["key"]: i for i in g["items"]}
    assert 칸["days"]["now"] == 130 and 칸["days"]["ok"]
    assert 칸["vs_r"]["ok"] and 칸["ratio"]["ok"] and 칸["incidents"]["ok"] and 칸["kis"]["ok"]
    assert g["passed"] and g["account"] == "H" and g["next"].startswith("2단계 손 매매")
    # 하나라도 아니면 통과 아님 — 표본 모자란 실현율·사고 하루·기간 모자람
    assert not desk.gate(series, board, "H", recon_bad_days=0, batch_failed=0, kis={"kept_pct": 99.0, "n": 99})["passed"]
    g2 = desk.gate(series, board, "H", recon_bad_days=1, batch_failed=0, kis={"kept_pct": 92.0, "n": 120})
    assert not g2["passed"] and {i["key"]: i for i in g2["items"]}["incidents"]["detail"] == {"recon_bad_days": 1,
                                                                                            "batch_failed": 0}  # fmt: skip
    짧음 = {k: v[:30] for k, v in series.items()}
    assert not desk.gate(짧음, desk.leaderboard(짧음), "H", recon_bad_days=0, batch_failed=0, kis=None)["passed"]


def test_combine_kept() -> None:
    assert desk.combine_kept([{"both": 8, "internal_only": 2}, {"both": 1, "internal_only": 1}]) == {
        "kept_pct": pytest.approx(75.0), "n": 12}
    assert desk.combine_kept([{}, {}]) == {"kept_pct": None, "n": 0}


def test_risk_board_by_hand() -> None:
    latest = {"A": {"equity": 1_000_000, "holdings": 600_000, "positions": [
        {"name": "가", "horizon": "short", "unrealized_pct": -5.5, "weight_pct": 40.0},  # 손절 −7 + 2 = −5 이하 → 가까움
        {"name": "나", "horizon": "short", "unrealized_pct": -4.9, "weight_pct": 20.0},  # 아직
        {"name": "다", "horizon": "mid", "unrealized_pct": None, "weight_pct": None},
    ]}, "B": {"equity": 0, "holdings": 0, "positions": []}}  # fmt: skip
    series = {"A": [("d1", 1.0), ("d2", 1.2), ("d3", 0.9)]}
    rows = {r["id"]: r for r in desk.risk_board(latest, series, {"short": -7.0, "mid": -15.0})}
    a = rows["A"]
    assert a["exposure_pct"] == pytest.approx(60.0) and a["positions"] == 3
    assert (a["top_name"], a["top_weight_pct"]) == ("가", 40.0)
    assert [x["name"] for x in a["near_stop"]] == ["가"]
    assert a["dd_pct"] == pytest.approx((0.9 / 1.2 - 1) * 100)
    assert rows["B"]["exposure_pct"] is None and rows["B"]["dd_pct"] is None and rows["B"]["top_name"] is None


def test_champion_blocks_by_hand() -> None:
    """61 평가 → 겹치지 않는 20 평가 블록 셋. C 는 세 블록 모두 H 보다 높음 → 후보, D 는 첫 블록만 크게 이기고
    나머지 둘은 짐 → 전체는 앞서도 후보 아님(꾸준함), R 은 뺀다"""
    날 = _날(61)
    h = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]
    c = [(d, 1 + 0.002 * i) for i, d in enumerate(날)]
    d_ = [(d, (1 + 0.02 * i) if i <= 20 else (1.4 - 0.0001 * (i - 20))) for i, d in enumerate(날)]
    rows = {r["id"]: r for r in desk.champion({"H": h, "C": c, "D": d_, "R1": c}, "H")}
    assert "R1" not in rows and "H" not in rows
    assert (rows["C"]["blocks"], rows["C"]["wins"], rows["C"]["candidate"]) == (3, 3, True)
    assert (rows["D"]["blocks"], rows["D"]["wins"], rows["D"]["candidate"]) == (3, 1, False)
    assert rows["D"]["pct"] > rows["D"]["champion_pct"]  # 전체는 앞섰다
    assert [r["id"] for r in desk.champion({"H": h, "C": c, "D": d_}, "H")][0] == "C"
    # 블록이 모자라면 후보 아님
    assert not desk.champion({"H": h[:41], "C": c[:41]}, "H")[0]["candidate"]


def test_write_puts_gate_risk_champion(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for aid, as_of, twr, recon in (("B", "2026-10-15", 1.0, 1), ("B", "2026-10-16", 1.02, 0), ("A", "2026-10-16", 1.0, 1)):
        c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
                       " twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1000, 400, 600, 1000, 0, ?, ?, ?, 't')",
                       [aid, as_of, twr, json.dumps([{"stock_id": 1, "horizon": "short", "unrealized_pct": -6.0,
                                                      "weight_pct": 60.0}]), recon])  # fmt: skip
    c.conn.execute("INSERT INTO batch_runs (job_name, market, started_at, status) VALUES ('daily_kr', 'KR', '2026-10-10T00:00:00', 'failed')")
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    칸 = {i["key"]: i for i in s["gate"]["items"]}
    assert s["gate"]["account"] == "B" and 칸["incidents"]["detail"] == {"recon_bad_days": 1, "batch_failed": 1}
    assert not s["gate"]["passed"]
    b = {r["id"]: r for r in s["risk"]}["B"]
    assert b["exposure_pct"] == pytest.approx(60.0) and b["near_stop"][0]["name"] == "종목1"
    assert isinstance(s["champion"], list)
