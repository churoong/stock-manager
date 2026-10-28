"""가상 H — 지주사(성적 가중 합의) (docs/company.md 10장, docs/infra.md 25.1482).

순수 함수(가중치·세기·순위)와, 실제 헌장 일곱 계좌 + G·H 를 올린 메모리 DB 에서 하루를 돌려 H 가 두 곳 이상 추천한 종목만 사고,
추천받은 종목 전부를 표와 함께 결정 원장에 남기고, 투표 회사 헌장이 바뀌면 사지 않는지 본다.
"""

from __future__ import annotations

import json

import pytest

from batch.jobs import broker_mirror
from batch.jobs import paper as job
from batch.services import company as co
from batch.services import holding as hold
from batch.services import report_picks
from tests.test_company_job_1466 import _하루
from tests.test_paper_job_1445 import Client


def _행(day: str, sid: int, ex: float, votes: dict[str, float]) -> dict:
    return {"decided_on": day, "stock_id": sid, "ret_20d": ex, "bench_20d": 0.0,
            "detail_json": json.dumps({"holding": {"votes": votes}})}  # fmt: skip


def test_weights_start_equal_then_shrink_toward_record() -> None:
    w, s = hold.weights([], ["A", "B"])
    assert w == {"A": 1.0, "B": 1.0} and s["A"] == {"n": 0, "avg": None}
    # A 가 추천한 종목은 +10%p, B 는 0%p — 에피소드 하나씩(같은 종목 28일 안 되풀이는 한 번)
    rows = [_행("2026-11-02", 1, 10.0, {"A": 1}), _행("2026-11-03", 1, 50.0, {"A": 1}),  # 둘째는 같은 에피소드
            _행("2026-11-02", 2, 0.0, {"B": 1})]  # fmt: skip
    w, s = hold.weights(rows, ["A", "B"])
    assert s["A"] == {"n": 1, "avg": 10.0}
    # 평균 5, A 는 +5 → k = 1/31 → 1 + (1/31)(5/5) ≈ 1.0323; B 는 ≈ 0.9677 — 표본 하나로는 거의 안 기운다
    assert w["A"] == pytest.approx(1 + 1 / 31, abs=1e-4) and w["B"] == pytest.approx(1 - 1 / 31, abs=1e-4)
    # 표본이 많고 차이가 크면 자른다
    많이 = [_행(f"2026-{m:02d}-01", 100 + m, 40.0, {"A": 1}) for m in range(1, 13)] + \
          [_행(f"2026-{m:02d}-01", 200 + m, -40.0, {"B": 1}) for m in range(1, 13)]  # fmt: skip
    w, _s = hold.weights(많이, ["A", "B"])
    assert w == {"A": hold.HOLDING_W_MAX, "B": hold.HOLDING_W_MIN}
    # 미래 정보 없음 — 성과가 안 찬 행(ret_20d 없음)은 쓰지 않는다
    w, s = hold.weights([{**_행("2026-11-02", 1, 10.0, {"A": 1}), "ret_20d": None}], ["A", "B"])
    assert s["A"]["n"] == 0


def test_strength_and_rank_with_consensus() -> None:
    assert hold.strength(1.0, 1.0) == 1.0 and hold.strength(3.0, 1.0) == hold.HOLDING_STRENGTH_MAX
    assert hold.strength(0.5, 1.0) == 1.0  # 가결은 문턱 이상 — 아래로 내리지 않는다
    표 = {"A": {1: 2.0, 2: 1.0}, "B": {1: 1.0, 3: 1.0}, "C": {2: 1.0}}
    순위 = hold.rank(표, {"A": 1.0, "B": 2.0, "C": 1.0})
    assert [(x["stock_id"], x["score"], x["eligible"]) for x in 순위] == [(1, 4.0, True), (2, 2.0, True), (3, 2.0, False)]


def test_migration_pins_voter_charter_hashes() -> None:
    c = Client(accounts=None, charters=True)
    설정 = json.loads(c.q("SELECT charter_json FROM paper_accounts WHERE id = 'H'")[0][0])
    헌장 = {aid: json.loads(cj) for aid, cj in c.q("SELECT id, charter_json FROM paper_accounts WHERE id IN ('A','C','D','E')")}
    assert 설정["voters"] == {aid: co.charter_hash(ch) for aid, ch in 헌장.items()}
    assert c.q("SELECT sell_rule, selection, top_n FROM paper_accounts WHERE id = 'H'") == [("trend", "weighted", 5)]
    assert broker_mirror.MIRROR_ACCOUNT == "H"


@pytest.fixture()
def 일곱(monkeypatch: pytest.MonkeyPatch) -> Client:
    c = Client(accounts=None, charters=True)
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(report_picks, "select_top", lambda rows, n=5: rows[:n])
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    return c


def test_h_buys_only_multi_voter_stocks_and_records_votes(일곱: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    줄, 기록 = _하루(일곱, monkeypatch, "2026-10-14")
    h = 기록["accounts"]["H"]["holding"]
    assert (h["recommended"], h["eligible"], h["chosen"]) == (2, 1, 1)
    assert set(h["weights"]) == {"A", "B", "C", "D", "E"} and set(h["weights"].values()) == {1.0}
    결정 = {sid: (kind, reason, json.loads(d)["holding"]) for sid, kind, reason, d in
            일곱.q("SELECT stock_id, kind, reason, detail_json FROM paper_decisions WHERE account_id = 'H'")}  # fmt: skip
    # 1 은 다섯 곳 모두 추천 → 산다(사유 "지주사 합의"), 3 은 B 만(점수 상위 5 에 둘 다 들어 있다) → 추천 회사 1곳
    assert 결정[1][:2] == ("buy", "지주사 합의") and set(결정[1][2]["votes"]) == {"A", "B", "C", "D", "E"}
    assert 결정[3][:2] == ("skip", "지주사: 추천 회사 1곳") and 결정[3][2]["votes"] == {"B": 1.0}
    assert {r[0] for r in 일곱.q("SELECT DISTINCT stock_id FROM paper_orders WHERE account_id = 'H'")} == {1}
    assert "🏢 지주사: 회사들 추천 2종목 · 두 곳 이상 추천 1 · 고름 1" in 줄
    assert any(x.startswith("   회사별 가중치 ") for x in 줄)  # 둘째 줄(25.1742)


def test_h_does_not_buy_when_voter_charter_changed(일곱: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    ch = json.loads(일곱.q("SELECT charter_json FROM paper_accounts WHERE id = 'C'")[0][0])
    ch["threshold"] = 1.5  # C 가 판을 올렸는데 H 는 그대로
    일곱.conn.execute("UPDATE paper_accounts SET charter_json = ? WHERE id = 'C'", [json.dumps(ch)])
    줄, 기록 = _하루(일곱, monkeypatch, "2026-10-14")
    assert "모멘텀" in 기록["accounts"]["H"]["holding"]["error"]
    assert 일곱.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'H'")[0][0] == 0
    assert any(x.startswith("⚠️ 가상 지주사 고르지 못함") for x in 줄)
    # 다른 계좌는 그대로 산다
    assert 일곱.q("SELECT COUNT(*) FROM paper_orders WHERE account_id = 'B'")[0][0] > 0


def test_late_account_shadow_starts_at_open(일곱: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """늦게 연 계좌의 "같은 돈 KOSPI" 는 연 날의 지수로 — 열기 전 시장 움직임을 넣지 않는다 (25.1484, 검증 [권장] 1)."""
    for d, v in (("2026-10-14", 100.0), ("2026-10-15", 105.0), ("2026-10-16", 110.0)):
        일곱.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
                         " VALUES ('KOSPI', ?, ?, 't', 't')", [d, v])  # fmt: skip
    일곱.conn.execute("UPDATE paper_accounts SET created_at = '2026-10-16T00:00:00+00:00' WHERE id = 'H'")
    _하루(일곱, monkeypatch, "2026-10-15")
    그림자 = dict(일곱.q("SELECT account_id, kospi_krw FROM paper_daily"))
    # 10-14 입금 1,000,000원: B 는 그 돈이 처음 투자 가능해진 장의 지수로(110 보다 낮다) → 지금 110 이면 100 만 원보다 크다.
    # H 는 10-16 에 열려 그날 지수 110 으로 → 그대로 1,000,000
    assert 그림자["H"] == 1_000_000 and 그림자["B"] > 1_000_000


def test_weekly_committee_review_skips_holding(일곱: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    _하루(일곱, monkeypatch, "2026-10-14")
    줄 = job.committee_review(일곱, date(2026, 10, 16), {})
    assert not any(x.startswith("  H ") for x in 줄) and any(x.startswith("  A ") for x in 줄)


def test_h_skip_줄에_보유_여부(일곱: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """H 가 이미 든 종목이 "추천 회사 1곳" 으로 skip 되면 `held` — 새 후보 풀에서 뺀다(25.1601 반박 [권고])."""
    일곱.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                     " currency, decided_on, fill_session, fill_basis, tranche, reason, horizon, status, rule_version,"
                     " created_at) VALUES (900, 'H', 'KR', 3, 'buy', 'limit', 10000, 2, 'KRW', '2026-10-01', '2026-10-02',"
                     " 'open', 1, 't', 'short', 'filled', 1, 't')")  # fmt: skip
    일곱.conn.execute("INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency,"
                     " fx_rate, fee, tax, slippage, cash_krw, horizon, created_at) VALUES (900, 'H', 3, 'buy', '2026-10-02',"
                     " 10000, 2, 'KRW', 1, 3, 0, 0, -20003, 'short', 't')")  # fmt: skip
    _하루(일곱, monkeypatch, "2026-10-14")
    [(상세,)] = 일곱.q("SELECT detail_json FROM paper_decisions WHERE account_id = 'H' AND stock_id = 3 AND kind = 'skip'")
    assert json.loads(상세)["held"] is True


def test_같은_날_다시_돌면_H_는_그날_회의록의_표를_쓴다(일곱: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1732 (위원회·H 감사 [권고] 2) — A~E 가 회의록을 쓴 뒤 H 만 실패한 날 다시 돌면, H 혼자 자료를 다시 읽어 회의를 열어 그 사이
    바뀐 자료로 H 의 표가 그날 회의록과 갈렸다. 회의록을 고쳐(A 가 1 을 집행하지 않았다고) 두고 다시 돌리면 H 는 그 회의록을 따른다"""
    진짜 = job.holding_step

    def 실패(*_a: object, **_k: object) -> None:
        raise RuntimeError("일시 읽기 실패")

    monkeypatch.setattr(job, "holding_step", 실패)
    _하루(일곱, monkeypatch, "2026-10-14")
    assert 일곱.q("SELECT COUNT(*) FROM paper_decisions WHERE account_id = 'H'") == [(0,)]
    (mj,) = 일곱.q("SELECT minutes_json FROM paper_meetings WHERE account_id = 'A'")[0]
    록 = json.loads(mj)
    for i in 록["items"]:
        if i["stock_id"] == 1:
            i["approved"] = False
    일곱.conn.execute("UPDATE paper_meetings SET minutes_json = ? WHERE account_id = 'A'", [json.dumps(록)])
    monkeypatch.setattr(job, "holding_step", 진짜)
    _줄, 기록 = _하루(일곱, monkeypatch, "2026-10-14")
    h = 기록["accounts"]["H"]["holding"]
    assert set(h["votes_from"].values()) == {"minutes"}
    표 = json.loads(일곱.q("SELECT detail_json FROM paper_decisions WHERE account_id = 'H' AND stock_id = 1")[0][0])
    assert "A" not in 표["holding"]["votes"] and {"C", "D", "E"} <= set(표["holding"]["votes"])
