"""거장 계좌 ZM·ZL 개설 (docs/company.md 12장, docs/infra.md 25.2267, migrations/0096).

헌장은 코드(`services/masters.CHARTERS` — 12.4 사전 등록 값)와 마이그레이션이 같고, 계좌가 열려도 A~Y 의 주문·결정·회의록이
그대로인지(12.8 무변화) 하루 단계로 본다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from batch.jobs import paper as job
from batch.services import company as co
from batch.services import desk, report_picks
from batch.services import masters as ms
from batch.services import paper as pp
from tests.test_masters_2261 import 헌장_12_4
from tests.test_paper_accounts_1458 import _시세, _하루
from tests.test_paper_job_1445 import Client

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "0096_paper_master_accounts.sql"


def _행() -> dict[str, dict]:
    c = Client(accounts=None)
    return {str(r["id"]): r for r in c.execute(
        "SELECT id, name, sell_rule, rule_version, created_at, entry_rule, top_n, selection, charter_json, charter_since"
        " FROM paper_accounts WHERE id LIKE 'Z%'").dicts()}  # fmt: skip


def test_마이그레이션_헌장은_코드_헌장_그대로다() -> None:
    행 = _행()
    assert sorted(행) == ["ZL", "ZM"]  # ZN 은 12.6 거름에서 열지 않았다(25.2266)
    for aid, r in 행.items():
        assert json.loads(r["charter_json"]) == ms.CHARTERS[aid], aid
        가중, 주관, 거부, mdd, 문턱, 안건, top_n, 매도 = 헌장_12_4[aid]
        assert (r["sell_rule"], r["rule_version"], r["entry_rule"], r["top_n"], r["selection"]) == \
            (매도, 1, "tranche", top_n, "signal")  # fmt: skip
        assert r["created_at"] == "2026-11-24T00:00:00+00:00" and r["charter_since"] == "2026-11-24"
        assert co.charter_errors(json.loads(r["charter_json"])) == []
    # 화면 이름에 투자가 이름(사용자 지시) — 12.4 표의 화면 이름
    assert 행["ZM"]["name"] == "가상 거장 위원회 (버핏·린치·그레이엄·그린블랫·오닐)"
    assert 행["ZL"]["name"] == "가상 린치 계좌"
    assert "'ZN'" not in MIGRATION.read_text(encoding="utf-8")


def test_등록부() -> None:
    assert (pp.account_name("ZM"), pp.account_name("ZL")) == ("거장위원회", "린치")
    assert {"ZM", "ZL"} <= set(pp.SEPARATE_IDS) and pp.MASTER_IDS == ("ZL", "ZM")
    assert {("ZM", 1), ("ZL", 1)} <= set(desk.PREREG) and ("ZN", 1) not in desk.PREREG
    assert all("Holm" in desk.PREREG[(a, 1)]["judge"] for a in ("ZM", "ZL"))
    assert "버핏" in desk.KIND["ZM"] and "린치" in desk.KIND["ZL"]
    # 거장 계좌는 스윙 손절 글이 아니라 core 매도 규칙의 손절선을 그대로 잰다
    assert desk._손절_해당없음("ZM", 3) is None and desk._손절_해당없음("ZL", 0) is None
    assert desk._손절_해당없음("S", 0) is not None  # 스윙은 그대로
    # 웹 사본
    웹 = (Path(__file__).resolve().parent.parent / "web" / "lib" / "kisChallenger.ts").read_text(encoding="utf-8")
    assert re.search(r'SEPARATE_IDS: readonly string\[\] = \["S", "T", "X", "Y", "ZL", "ZM"\]', 웹)


def _a_to_y(c: Client) -> tuple[list, list, list]:
    주문 = c.q("SELECT account_id, stock_id, side, order_type, limit_price, quantity, tranche, reason FROM paper_orders"
               " WHERE account_id NOT LIKE 'Z%' ORDER BY account_id, tranche, stock_id")  # fmt: skip
    결정 = c.q("SELECT account_id, stock_id, kind, reason FROM paper_decisions WHERE account_id NOT LIKE 'Z%'"
               " ORDER BY account_id, stock_id, kind")  # fmt: skip
    회의 = c.q("SELECT account_id, charter_hash, minutes_json FROM paper_meetings WHERE account_id NOT LIKE 'Z%'"
               " ORDER BY account_id")  # fmt: skip
    return 주문, 결정, 회의


def _새(monkeypatch: pytest.MonkeyPatch) -> Client:
    """`test_paper_accounts_1458.c` 와 같은 하루 — 국내 1·3, 입금 100만"""
    from batch.jobs import paper as jp

    c = Client(accounts=None)
    c.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                   " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    for sid in (1, 3):
        c.conn.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                       " VALUES ('2026-10-10', ?, 1, 'KRW', 't')", [sid])  # fmt: skip
        _시세(c, sid, "2026-10-14", 10_000, 10_000)
    # 시험 계좌 표는 A~E 헌장을 비워 둔다 — 운영처럼 코드 헌장을 넣어 위원회가 실제로 열리게(비교가 공짜가 아니게)
    for aid in ("A", "C", "D", "E"):
        c.conn.execute("UPDATE paper_accounts SET charter_json = ?, charter_since = '2026-10-01' WHERE id = ?",
                       [json.dumps(co.CHARTERS[aid], ensure_ascii=False), aid])  # fmt: skip
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(jp, "frozen_ids", lambda *_a, **_k: set())
    return c


def test_거장_계좌가_열려도_A_부터_Y_까지_그대로다(monkeypatch: pytest.MonkeyPatch) -> None:
    """12.8 무변화 — 계좌 루프는 id 순이라 ZL·ZM 이 맨 뒤에 돈다. 조사 자료는 안건 50·거장 칸으로 늘지만 회의·주문은 같다"""
    없음 = _새(monkeypatch)
    없음.conn.execute("DELETE FROM paper_accounts WHERE id LIKE 'Z%'")
    _하루(없음, monkeypatch, "2026-10-14")
    있음 = _새(monkeypatch)
    # 하루 단계 날짜에 열린 것으로 — 개설일 막이를 지나 실제로 회의를 연다
    있음.conn.execute("UPDATE paper_accounts SET created_at = '2026-10-14T00:00:00+00:00' WHERE id LIKE 'Z%'")
    불림: list[object] = []
    원래 = job.committee_step
    monkeypatch.setattr(job, "committee_step", lambda *a, **k: (불림.append(k["account"]), 원래(*a, **k))[1])
    기록 = _하루(있음, monkeypatch, "2026-10-14")
    assert _a_to_y(있음) == _a_to_y(없음)
    assert _a_to_y(없음)[2]  # 비교가 공짜가 아니다 — A~E 회의록이 있다
    assert {"ZL", "ZM"} <= set(불림) and all("not_open_until" not in 기록["accounts"][a] for a in ("ZL", "ZM"))
    assert sorted(r[0] for r in 있음.q("SELECT account_id FROM paper_meetings WHERE account_id LIKE 'Z%'")) == ["ZL", "ZM"]
    # 거장 자료를 읽었다 — 조사 자료 행에 masters 칸(12.8)
    assert all("masters" in json.loads(r[0]) for r in 있음.q("SELECT dossier_json FROM paper_dossiers"))
    assert not any("masters" in json.loads(r[0]) for r in 없음.q("SELECT dossier_json FROM paper_dossiers"))


def test_개설일_전에는_평가도_매매도_하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _새(monkeypatch)
    기록 = _하루(c, monkeypatch, "2026-10-14")
    for aid in ("ZL", "ZM"):
        assert 기록["accounts"][aid] == {"not_open_until": "2026-11-24"}, aid
    assert c.q("SELECT COUNT(*) FROM paper_orders WHERE account_id LIKE 'Z%'") == [(0,)]
    assert c.q("SELECT COUNT(*) FROM paper_meetings WHERE account_id LIKE 'Z%'") == [(0,)]


def test_지주사는_거장_자료_실패면_거장_투표_회사를_다시_열지_않는다() -> None:
    """지금 H 투표 회사(A·C·D·E)엔 거장 부서가 없어 닿지 않는다 — 나중에 들어와도 '자료 없음' 표가 섞이지 않게(25.2267)"""
    from datetime import date

    ch = {"ZM": ms.CHARTERS["ZM"]}
    out = job.holding_step(
        object(), market="KR", account="H", config={"voters": {"ZM": co.charter_hash(ch["ZM"])}}, charters=ch,
        signal_rows=[], trade_date="2026-11-24", cache={"agenda": [], "population": [], "masters_error": "x"},
        now="t", rule_version=3, today=date(2026, 11, 24), top_n=5,
    )  # fmt: skip
    assert out[3]["error"].startswith("거장 자료 읽기 실패로 투표 회사 회의를 열 수 없음") and "거장위원회" in out[3]["error"]
