"""실전 전환 관문·위험판·챔피언 교체 후보 (docs/desk.md 6장, docs/infra.md 25.1508) — 손으로 낸 값."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from batch.jobs import desk as job
from batch.services import desk
from batch.services import paper as pp
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
    # 결정 단위 하한도(25.1518), 가상 대조 하한도(25.1697) 넘어야 통과
    # 25.1963 — 하한은 Pocock 문턱으로 다시 낸다(평균·표준오차·묶음). 95% 하한(`lower`)은 참고
    증거 = {"lower": 55.0, "n_blocks": 6, "mean": 62.0, "se": 2.0,
            "control": {"lower": 53.0, "n_blocks": 6, "mean": 60.0, "se": 2.0}}  # fmt: skip
    # 지수 그림자 — 같은 기간 반반 지수 +2% (H 는 약 +13%)
    지수 = [(날[0], 1000.0, 1000.0, 1000.0), (날[-1], 1020.0, 1020.0, 1000.0)]
    g = desk.gate(series, "H", recon_bad_days=0, batch_failed=0, kis={"kept_pct": 92.0, "n": 120}, evidence=증거,
                  index=지수)  # fmt: skip
    칸 = {i["key"]: i for i in g["items"]}
    assert 칸["days"]["now"] == 130 and 칸["days"]["ok"]
    assert 칸["vs_r"]["ok"] and 칸["vs_index"]["ok"] and 칸["ratio"]["ok"] and 칸["incidents"]["ok"] and 칸["kis"]["ok"] and 칸["selection"]["ok"]
    대조하한 = 60.0 - pp.t_quantile(1 - desk.GATE_POCOCK_ALPHA, 5) * 2.0
    assert 칸["control"]["ok"] and 칸["control"]["now"] == pytest.approx(대조하한) and 칸["control"]["lower_95"] == 53.0
    assert g["passed"] and g["account"] == "H" and g["next"].startswith("2단계 손 매매")
    # 하나라도 아니면 통과 아님 — 표본 모자란 실현율·사고 하루·기간 모자람
    assert not desk.gate(series, "H", recon_bad_days=0, batch_failed=0, kis={"kept_pct": 99.0, "n": 99})["passed"]
    g2 = desk.gate(series, "H", recon_bad_days=1, batch_failed=0, kis={"kept_pct": 92.0, "n": 120})
    assert not g2["passed"] and {i["key"]: i for i in g2["items"]}["incidents"]["detail"] == {"recon_bad_days": 1,
                                                                                            "batch_failed": 0}  # fmt: skip
    짧음 = {k: v[:30] for k, v in series.items()}
    assert not desk.gate(짧음, "H", recon_bad_days=0, batch_failed=0, kis=None)["passed"]


def test_gate_own_window_and_current_version() -> None:
    """검토(25.1511) 1·3 — 새 계좌가 20번째 평가에 닿아도 관문은 그대로(자기 구간), 판을 바꾸면 그 뒤 평가만 센다"""
    날 = _날(160)
    h = [(d, 1 + 0.001 * i + (0.002 if i % 2 else 0)) for i, d in enumerate(날)]
    r = [(d, 1 + (0.01 if i % 2 else -0.01)) for i, d in enumerate(날)]
    증거 = {"lower": 55.0, "n_blocks": 6, "mean": 62.0, "se": 2.0,
            "control": {"lower": 53.0, "n_blocks": 6, "mean": 60.0, "se": 2.0}}  # fmt: skip
    지수 = [(날[0], 1000.0, 1000.0, 1000.0), (날[-1], 1000.0, 1000.0, 1000.0)]
    전 = desk.gate({"H": h, "R1": r, "R2": r}, "H", recon_bad_days=0, batch_failed=0, kis={"kept_pct": 95.0, "n": 200},
                  evidence=증거, index=지수)  # fmt: skip
    새계좌 = {"H": h, "R1": r, "R2": r, "I": [(d, 1.0) for d in 날[-20:]]}
    뒤 = desk.gate(새계좌, "H", recon_bad_days=0, batch_failed=0, kis={"kept_pct": 95.0, "n": 200}, evidence=증거,
                  index=지수)  # fmt: skip
    assert 전["items"] == 뒤["items"] and 뒤["passed"] and 뒤["from"] == 날[0]
    # 판 2 를 날[100] 결정에서 봤다 — 그 뒤 평가 59번만 센다, 비교 구간도 그 뒤부터
    판2 = desk.gate({"H": h, "R1": r, "R2": r}, "H", since=날[100], version=2, recon_bad_days=0, batch_failed=0,
                   kis={"kept_pct": 95.0, "n": 200})  # fmt: skip
    칸 = {i["key"]: i for i in 판2["items"]}
    assert 칸["days"]["now"] == 59 and not 칸["days"]["ok"] and 칸["days"]["label"] == "모의 기간(지금 판 2의 평가 횟수)"
    assert 판2["from"] == 날[101] and 판2["version"] == 2


def test_combine_kept() -> None:
    assert desk.combine_kept([{"both": 8, "internal_only": 2}, {"both": 1, "internal_only": 1}]) == {
        "kept_pct": pytest.approx(75.0), "n": 12, "path_pct": pytest.approx(75.0), "path_n": 12}
    assert desk.combine_kept([{}, {}]) == {"kept_pct": None, "n": 0, "path_pct": None, "path_n": 0}  # 25.1624 참고 칸


def test_risk_board_by_hand() -> None:
    latest = {"A": {"equity": 1_000_000, "holdings": 600_000, "positions": [
        {"name": "가", "horizon": "short", "avg_price": 100.0, "close": 94.5, "weight_pct": 40.0},  # −5.5% ≤ −7 + 2 → 가까움
        {"name": "나", "horizon": "short", "avg_price": 100.0, "close": 95.1, "weight_pct": 20.0},  # −4.9% 아직
        {"name": "다", "horizon": "mid", "avg_price": None, "close": None, "weight_pct": None},
        # 기간 없음 → 장기(−25)로: −23.5% ≤ −23 → 가까움 (검토 2 — 매도 플래그와 같이)
        {"name": "라", "horizon": None, "avg_price": 100.0, "close": 76.5, "weight_pct": 1.0},
        # 원화 손익률은 환율로 −3% 여도 종목 통화로 −6% 면 가까움 — 종목 통화 잣대
        {"name": "마", "horizon": "short", "avg_price": 50.0, "close": 47.0, "unrealized_pct": -3.0, "weight_pct": 1.0},
    ]}, "B": {"equity": 0, "holdings": 0, "positions": []}}  # fmt: skip
    series = {"A": [("d1", 1.0), ("d2", 1.2), ("d3", 0.9)]}
    rows = {r["id"]: r for r in desk.risk_board(latest, series, {"short": -7.0, "mid": -15.0, "long": -25.0})}
    a = rows["A"]
    assert a["exposure_pct"] == pytest.approx(60.0) and a["positions"] == 5
    assert (a["top_name"], a["top_weight_pct"]) == ("가", 40.0)
    assert [x["name"] for x in a["near_stop"]] == ["가", "라", "마"]
    assert a["near_stop"][2]["unrealized_pct"] == pytest.approx(-6.0)
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
    # 블록 규칙은 C 만 — 결정 단위 근거가 없으면 후보는 아니다(25.1518)
    assert (rows["C"]["blocks"], rows["C"]["wins"], rows["C"]["blocks_ok"], rows["C"]["candidate"]) == (3, 3, True, False)
    assert (rows["D"]["blocks"], rows["D"]["wins"], rows["D"]["blocks_ok"]) == (3, 1, False)
    assert rows["D"]["pct"] > rows["D"]["champion_pct"]  # 전체는 앞섰다
    assert [r["id"] for r in desk.champion({"H": h, "C": c, "D": d_}, "H")][0] == "C"
    # 블록이 모자라면 후보 아님
    assert not desk.champion({"H": h[:41], "C": c[:41]}, "H")[0]["candidate"]
    # 검토(25.1511) 6 — 최근 끝에서 자른다: 앞 60 평가는 C 가 이기다 최근 19 평가에 크게 지면 그 블록이 승패에 든다
    날2 = _날(80)
    h2 = [(d, 1 + 0.001 * i) for i, d in enumerate(날2)]
    c2 = [(d, (1 + 0.003 * i) if i <= 60 else (1.18 - 0.005 * (i - 60))) for i, d in enumerate(날2)]
    r2 = {r["id"]: r for r in desk.champion({"H": h2, "C": c2}, "H")}["C"]
    assert r2["blocks"] == 3 and r2["wins"] < 3  # 마지막 블록(최근 20 평가)이 진 것으로 든다


def test_write_puts_gate_risk_champion(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    for aid, as_of, twr, recon in (("B", "2026-10-15", 1.0, 1), ("B", "2026-10-16", 1.02, 0), ("A", "2026-10-16", 1.0, 1)):
        c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
                       " twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1000, 400, 600, 1000, 0, ?, ?, ?, 't')",
                       [aid, as_of, twr, json.dumps([{"stock_id": 1, "horizon": "short", "avg_price": 100.0, "close": 94.0,
                                                      "weight_pct": 60.0}]), recon])  # fmt: skip
    # 사고 날은 거래일로 센다(25.1572) — 창 안 거래일을 모두 성공으로 채우고, 10-08 하루만 실패 뒤 성공으로 둔다
    지금 = datetime(2026, 10, 16, 23, 40, tzinfo=UTC)
    창 = "2026-08-18"
    for m, 작업 in (("KR", "daily_kr"), ("US", "daily_us")):
        for d in job._expected_trade_dates(m, 창, 지금):
            c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, started_at, status)"
                           " VALUES (?, ?, ?, ?, 'success')", [작업, m, d, f"{d}T23:30:00"])  # fmt: skip
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, started_at, status)"
                   " VALUES ('daily_kr', 'KR', '2026-10-08', '2026-10-09T00:00:00', 'failed')")  # fmt: skip
    job.write(c, "KR", 지금, trade_date="2026-10-16", session_date="2026-10-19", step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    칸 = {i["key"]: i for i in s["gate"]["items"]}
    assert s["gate"]["account"] == "B" and 칸["incidents"]["detail"] == {"recon_bad_days": 1, "batch_failed": 1}
    assert not s["gate"]["passed"]
    b = {r["id"]: r for r in s["risk"]}["B"]
    assert b["exposure_pct"] == pytest.approx(60.0) and b["near_stop"][0]["name"] == "종목1"
    assert isinstance(s["champion"], list)


def test_gate_failure_clears_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    """검토(25.1511) 5 — 계산이 실패하면 세 칸을 비우고 까닭을 적는다(앞 실행 값이 새 시각 아래 남지 않게)"""
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    c.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES ('2026-10-17', ?, 't')",
                   [json.dumps({"gate": {"passed": True}, "risk": [{"id": "A"}], "markets": {}})])  # fmt: skip

    def 망(*_a: object, **_k: object) -> None:
        raise RuntimeError("망")

    monkeypatch.setattr(job.desk, "gate", 망)
    기록 = job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                   session_date="2026-10-19", step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk WHERE as_of = '2026-10-17'")[0][0])
    assert (s["gate"], s["risk"], s["champion"]) == (None, [], []) and s["gate_error"].startswith("RuntimeError")
    assert 기록["gate_error"].startswith("RuntimeError")


def test_판_이력을_못_읽으면_관문을_비우고_까닭을_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """since='' 로 모든 판을 섞어 관문을 내던 것 (25.1573, 운용 감사 [참고])"""
    from datetime import UTC, datetime

    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    monkeypatch.setattr(job, "_판이력", lambda *_a, **_k: ({}, "OperationalError: 표 없음"))
    기록 = job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16",
                   session_date="2026-10-19", step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    assert s["gate"] is None and s["gate_error"].startswith("판 이력을 읽지 못해 관문을 내지 않음")
    assert 기록["rule_history_error"].startswith("OperationalError")


def test_챔피언_비교는_두_계좌_모두_지금_판인_구간만() -> None:
    """C 가 판 1 에서 크게 이기고 판 2 에선 H 만큼만 오른다 — 예전엔 판 1 성적으로 블록 규칙을 통과했다
    (25.1575, 운용 감사 [참고])"""
    날 = _날(120)
    h = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]
    c = [(d, (1 + 0.004 * i) if i < 100 else (1.4 + 0.001 * (i - 100))) for i, d in enumerate(날)]
    전부 = {r["id"]: r for r in desk.champion({"H": h, "C": c}, "H")}["C"]
    assert 전부["blocks_ok"]
    판2 = {r["id"]: r for r in desk.champion({"H": h, "C": c}, "H", since={"C": 날[99]})}["C"]
    assert 판2["from"] == 날[100] and not 판2["blocks_ok"]

