"""모의 투자사 — 일일 모의 단계에 투자위원회 연결 (docs/company.md 3·5장, docs/infra.md 25.1466).

실제 마이그레이션을 올린 메모리 SQLite 에서: 헌장이 있는 회사만 위원회를 열고, 가결 종목만 사고, 회의록·조사 자료·부결 결정이 한 묶음으로
남고, 같은 날 다시 돌아도 두 번 쓰지 않고, 헌장이 틀린 회사는 판 1 그대로 도는지.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import company as co
from batch.services import report_picks
from tests.test_paper_job_1445 import Client, _신호, 설정

헌장 = {
    "name": "가치투자사", "sell_rule": "core", "weights": {"research": 1, "quant": 1, "sentiment": 1},
    "research_mix": {"value": 1}, "lead": "research", "veto": [], "max_mdd": None,
    "threshold": 1.0, "agenda": 20, "top_n": 5,
}  # fmt: skip


@pytest.fixture()
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    c = Client()
    c.conn.execute(
        "INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
        " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')"
    )
    c.conn.execute("UPDATE paper_accounts SET charter_json = ?, rule_version = 2 WHERE id = 'A'", [json.dumps(헌장)])
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    return c


def _신호들() -> list[report_picks.SignalRow]:
    좋은 = replace(_신호(), factor_scores={"value": 90.0, "momentum": 90.0})
    나쁜 = replace(_신호(), stock_id=3, ticker="000660", name="종목3", factor_scores={"value": 10.0, "momentum": 10.0})
    return [좋은, 나쁜]


def _하루(c: Client, monkeypatch: pytest.MonkeyPatch, trade_date: str) -> tuple[list[str], dict]:
    다음 = cal.next_session("KR", date.fromisoformat(trade_date))
    monkeypatch.setattr(cal, "local_today", lambda _m=None: 다음)
    return job.run_step(
        c, market="KR", trade_date=trade_date, late=False, signal_rows=_신호들(), settings=설정, price_series={},
        series_unknown={}, allocate=True, now=datetime.fromisoformat(f"{다음}T00:30:00+00:00"),
    )  # fmt: skip


def test_company_buys_only_what_committee_passed(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    # A(가치투자사): 리서치·퀀트 찬성(신호 두 종목 안 100 백분위) → 1 가결. 3 은 둘 다 반대 → 부결
    assert {r[0] for r in c.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'A'")} == {1}
    assert {r[0] for r in c.q("SELECT DISTINCT reason FROM paper_orders WHERE account_id = 'A'")} == {"위원회 가결"}
    # B(헌장 없음)는 판 1 그대로 두 종목 다
    assert {r[0] for r in c.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'B'")} == {1, 3}
    # 회의록 한 장 · 조사 자료(시장·날짜·종목마다 한 번) · 부결 결정(판 번호와 위원회 기록)
    회의 = c.q("SELECT account_id, minutes_json FROM paper_meetings")
    assert [a for a, _m in 회의] == ["A"]
    assert json.loads(회의[0][1])["memo"].startswith("가치투자사 위원회: 안건 2 · 가결 1")
    assert c.q("SELECT stock_id FROM paper_dossiers ORDER BY stock_id") == [(1,), (3,)]
    부결 = c.q("SELECT kind, reason, rule_version, detail_json FROM paper_decisions WHERE account_id = 'A' AND stock_id = 3")
    assert 부결[0][0] == "skip" and 부결[0][1].startswith("위원회:") and 부결[0][2] == 2
    assert json.loads(부결[0][3])["committee"]["opinions"]["research"][0] == -1
    # 자료 없음은 까닭과 함께 — 중립(0)과 가른다 (25.1471)
    assert json.loads(부결[0][3])["committee"]["opinions"]["sentiment"][2].startswith("자료 없음")
    산것 = c.q("SELECT detail_json, rule_version FROM paper_decisions WHERE account_id = 'A' AND stock_id = 1")
    assert json.loads(산것[0][0])["committee"]["vote"] == pytest.approx(2.0) and 산것[0][1] == 2
    assert c.q("SELECT DISTINCT rule_version FROM paper_decisions WHERE account_id = 'B'") == [(1,)]
    # 운용역 메모 한 줄
    assert any(x.startswith("🏛 A 가치투자사 위원회:") for x in 줄)
    assert 기록["accounts"]["A"]["committee"]["approved"] == 1

    # 같은 날 다시 — 회의록·결정·주문이 두 번 생기지 않는다
    전 = c.q("SELECT (SELECT COUNT(*) FROM paper_orders), (SELECT COUNT(*) FROM paper_decisions),"
             " (SELECT COUNT(*) FROM paper_meetings), (SELECT COUNT(*) FROM paper_dossiers)")  # fmt: skip
    _하루(c, monkeypatch, "2026-10-14")
    assert c.q("SELECT (SELECT COUNT(*) FROM paper_orders), (SELECT COUNT(*) FROM paper_decisions),"
               " (SELECT COUNT(*) FROM paper_meetings), (SELECT COUNT(*) FROM paper_dossiers)") == 전  # fmt: skip


def test_meeting_alone_blocks_rerun(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """회의록만 먼저 써진 날(결정 없음) 다시 돌아도 새로 쓰지 않는다 — 가드가 회의록도 센다 (docs/company.md 3장 8)."""
    c.conn.execute("INSERT INTO paper_meetings VALUES ('A', 'KR', '2026-10-14', 'h', '{}', 't')")
    _, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert 기록["accounts"]["A"]["orders"] == "이미 냄"
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'A'") == [(0,)]


def test_bad_charter_runs_rule_one_and_says_why(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """헌장이 위원회 규칙에 어긋나면(한 부서 40% 넘음) 그 회사는 판 1 그대로 돌고 기록에 까닭을 남긴다."""
    나쁜 = {**헌장, "weights": {"research": 5, "quant": 1}}
    c.conn.execute("UPDATE paper_accounts SET charter_json = ? WHERE id = 'A'", [json.dumps(나쁜)])
    _, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert any("40%" in e for e in 기록["accounts"]["A"]["charter_errors"])
    assert {r[0] for r in c.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'A'")} == {1, 3}
    assert c.q("SELECT COUNT(*) FROM paper_meetings") == [(0,)]


def test_committee_failure_stops_only_that_company(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """위원회가 실패하면 그 회사만 오늘 사지 않는다 — 다른 계좌는 그대로, 판 1 로 몰래 사지 않는다, 리포트에 경고 한 줄."""
    def 터짐(*_a: object, **_k: object) -> None:
        raise RuntimeError("조사 자료 읽기 실패")

    monkeypatch.setattr(job, "committee_step", 터짐)
    줄, 기록 = _하루(c, monkeypatch, "2026-10-14")
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'A'") == [(0,)]
    assert {r[0] for r in c.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'B'")} == {1, 3}
    assert 기록["accounts"]["A"]["committee"]["error"].startswith("조사 자료")
    assert any(x.startswith("⚠️ 모의 A 투자위원회를 열지 못함") for x in 줄)


def test_charter_migration_matches_code() -> None:
    """0077 의 헌장은 `services/company.CHARTERS` 그대로 — 판 2 는 첫 거래일(10-16)부터, B·R 은 판 1 (25.1468)."""
    from batch.services import company as co
    from batch.services import paper as pp

    c = Client(accounts=None, charters=True)
    행 = {r[0]: r[1:] for r in c.q("SELECT id, charter_json, charter_since, rule_version, sell_rule FROM paper_accounts")}
    for aid, ch in co.CHARTERS.items():
        assert json.loads(행[aid][0]) == ch and 행[aid][1:] == ("2026-10-16", 2, ch["sell_rule"]), aid
        assert co.charter_errors(ch) == [] and ch["sell_rule"] in pp.SELL_RULES
    for aid in ("B", "R1", "R2"):
        assert 행[aid][0] is None and 행[aid][2] == 1, aid


def test_news_targets_follow_rules_that_sell_on_sentiment() -> None:
    """뉴스 수집 대상 계좌 = 감성급락을 따르는 원칙 — `SELL_RULES` 와 같은 목록이어야 C(momentum)의 감성 매도가 살아 있다 (25.1468)."""
    import re

    from batch.jobs import monitor_targets
    from batch.services import paper as pp

    따름 = {k for k, v in pp.SELL_RULES.items() if v is None or "감성급락" in v}
    적힌 = set(re.findall(r"'(\w+)'", monitor_targets.PAPER_HELD_SQL.split("sell_rule IN")[1].split(")")[0]))
    assert 적힌 == 따름 == {"all", "momentum"}


def test_department_scorecard_from_decision_ledger(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """결정 원장의 부서 입장 + 20 거래일 성과 → 부서 성적표가 평가 행의 성과 분해에 (25.1472). 판 1 결정은 섞지 않는다."""
    _하루(c, monkeypatch, "2026-10-14")
    # 성과가 찼다고 친다 — 1(리서치 찬성)은 지수보다 +5%p, 3(리서치 반대)은 −3%p
    c.conn.execute("UPDATE paper_decisions SET ret_20d = 8, bench_20d = 3 WHERE account_id = 'A' AND stock_id = 1")
    c.conn.execute("UPDATE paper_decisions SET ret_20d = 0, bench_20d = 3 WHERE account_id = 'A' AND stock_id = 3")
    # 판 1 결정(같은 계좌의 옛 판)은 성적표에 들지 않는다
    c.conn.execute("INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, detail_json, created_at,"
                   " ret_20d, bench_20d, rule_version) VALUES ('A', 'KR', '2026-10-01', 3, 'skip', 'x', ?, 't', 50, 0, 1)",
                   [json.dumps({"committee": {"opinions": {"research": [1, 1]}}})])  # fmt: skip
    다음 = cal.next_session("KR", date(2026, 10, 14)).isoformat()
    a = job.save_attribution(c, "A", 다음, 2)
    리서치 = next(x for x in a["departments"] if x["dept"] == "research")
    assert (리서치["n_for"], 리서치["n_against"]) == (1, 1)
    assert 리서치["spread"] == pytest.approx(0.08)
    # 감성은 자료 없음 — 표본에 들지 않는다
    assert not any(x["dept"] == "sentiment" for x in a["departments"])
    # 판 1 계좌(B)에는 부서 성적표가 없다
    assert "departments" not in job.save_attribution(c, "B", 다음, 1)


def test_committee_skip_reasons_group_without_numbers() -> None:
    from batch.services import paper as pp

    assert pp._까닭_묶음("위원회: 표 0.83 < 문턱 1") == pp._까닭_묶음("위원회: 표 0.91 < 문턱 1") == "위원회: 표 N < 문턱 N"
    assert pp._까닭_묶음("종목 비중 상한 도달") == "종목 비중 상한 도달"
    결정 = [{"stock_id": i, "decided_on": f"2026-09-{i:02d}", "kind": "skip", "reason": f"위원회: 표 0.{i} < 문턱 1",
             "ret_20d": 1.0, "bench_20d": 0.0} for i in range(1, 4)]  # fmt: skip
    assert list(pp.attribution(결정, [])["skip_by_reason"]) == ["위원회: 표 N < 문턱 N"]


def test_weekly_committee_review(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """주간 요약 투자사 절 — 회의 수와 소수의견이 옳았던 결정(부결했는데 지수보다 크게 오름) (25.1473)."""
    _하루(c, monkeypatch, "2026-10-14")
    # 부결한 3 이 20 거래일 뒤 지수보다 +12%p — 찬성한 소수(퀀트)가 옳았다고 친다
    상세 = {"committee": {"opinions": {"research": [-1, 1.0], "quant": [1, 0.5]}, "minority": ["quant"]}}
    c.conn.execute("UPDATE paper_decisions SET ret_20d = 15, bench_20d = 3, detail_json = ? WHERE account_id = 'A'"
                   " AND stock_id = 3", [json.dumps(상세)])  # fmt: skip
    줄 = job.committee_review(c, date(2026, 11, 12), {})
    assert 줄[0] == "[가상모의투자 투자사 — 투자위원회]"
    assert any(x.startswith("  A 가치투자사: 회의 0") for x in 줄)  # 11-12 기준 이번 주 회의는 없다
    assert any("부결했는데 +12.0%p — 소수의견(찬성: 퀀트부)이 옳았다" in x and "종목3" in x for x in 줄)
    # 이번 주 회의는 센다
    줄 = job.committee_review(c, date(2026, 10, 16), {})
    assert any(x.startswith("  A 가치투자사: 회의 1 · 안건 2 · 가결 1 · 집행 1") for x in 줄)
    # 주간 요약에 실린다 (summary_section 이 부른다)
    요약 = job.summary_section(c, datetime(2026, 10, 18, 0, 0, tzinfo=UTC))
    assert "[가상모의투자 투자사 — 투자위원회]" in 요약


def test_review_lines_threshold_and_order() -> None:
    """문턱(5%p) 이하·소수의견 없음은 빼고, 산 것이 크게 밑돌면 반대한 소수, 빗나간 크기 순 셋까지 (25.1473)."""
    def 결정(sid: int, kind: str, ret: float, minority: list[str], reason: str = "위원회 부결") -> dict:
        return {"account_id": "A", "stock_id": sid, "decided_on": "2026-09-20", "kind": kind, "reason": reason,
                "ret_20d": ret, "bench_20d": 0.0,
                "detail_json": json.dumps({"committee": {"minority": minority}})}  # fmt: skip

    결정들 = [
        결정(1, "skip", 4.9, ["quant"]),             # 문턱 아래
        결정(2, "skip", 20.0, []),                   # 소수의견 없음
        결정(3, "buy", -8.0, ["risk"], "위원회 가결"),  # 산 것이 밑돎 → 반대한 소수
        결정(4, "skip", 6.0, ["quant"]),
        결정(5, "skip", 30.0, ["broker"]),
        결정(6, "skip", 7.0, ["flows"]),
        결정(7, "skip", 50.0, ["quant"], "자리 없음"),  # 위원회 부결이 아님
    ]
    줄 = co.review_lines([], 결정들, {}, {"A": {"name": "가치투자사"}})
    빗 = [x for x in 줄 if x.startswith("  A 2026-09-20")]
    assert [x.split(" ")[4].rstrip(":") for x in 빗] == ["5", "3", "6"]
    assert "샀는데 -8.0%p — 소수의견(반대: 리스크관리부)이 옳았다" in 빗[1]
    # 문턱 — 4.9%p 는 빼고 6%p 는 싣는다(셋 안에 들어도)
    줄 = co.review_lines([], 결정들[:1] + 결정들[3:4], {}, {"A": {"name": "가치투자사"}})
    assert [x.split(" ")[4].rstrip(":") for x in 줄 if x.startswith("  A 2026-09-20")] == ["4"]
