"""매도 경로 감사 [필수] 1·2 (docs/paper.md 2.2·9장, docs/infra.md 25.1612).

지키는 것
- 이미 매도가 열린(정지·하한가로 미뤄진) 종목은 결정 원장에 "들고 감"(hold_flag)·"막힘"(blocked)으로 적지 않는다 —
  그 열린 매도대로 팔린다(멈춤 스위치도 열린 매도의 체결은 막지 않는다)
- 손절이 선 보유는 손절을 따르지 않는 계좌(D)라도 남은 다음 장 매수 회차를 거둔다 — 더 사지 않는다
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import broker_mirror as bm
from tests.test_paper_job_1445 import Client, _시세, _신호, _하루, client, 설정  # noqa: F401


def _점수(c: Client, day: str, total: float) -> None:
    c.conn.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                   " weights_json, calc_version, created_at) VALUES (1, ?, ?, '{}', 10, '{}', 1, 't')", [day, total])  # fmt: skip


def _미뤄진_손절(c: Client, monkeypatch: pytest.MonkeyPatch, 재무악화: bool) -> None:
    c.conn.execute("DELETE FROM paper_accounts WHERE id != 'B'")
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    if 재무악화:
        _점수(c, "2026-10-16", 50.0)  # 매수 당시 72 → 50 — 재무악화(B 는 따르지 않음)
    _시세(c, "2026-10-16", 9_100, 9_000)  # −10% 손절 → 매도 주문
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT reason, status FROM paper_orders WHERE side = 'sell'") == [("손절", "open")]


def test_열린_매도_종목은_들고_감으로_적지_않는다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _미뤄진_손절(client, monkeypatch, 재무악화=True)
    _시세(client, "2026-10-19", 0, 9_500, volume=0)  # 정지 — 매도는 다음 장으로, 종가 −5% 라 손절은 풀림
    _하루(client, monkeypatch, "2026-10-19", signals=False)
    assert client.q("SELECT status FROM paper_orders WHERE side = 'sell'") == [("open",)]
    assert client.q("SELECT kind FROM paper_decisions WHERE decided_on = '2026-10-19'") == []


def test_열린_매도는_멈춤_날에도_막힘으로_적지_않는다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _미뤄진_손절(client, monkeypatch, 재무악화=False)
    _시세(client, "2026-10-19", 0, 9_000, volume=0)  # 정지 → 미룸
    client.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('paper_halt', ?, 't')",
                        [json.dumps({"on": True})])  # fmt: skip
    _하루(client, monkeypatch, "2026-10-19", signals=False)
    assert client.q("SELECT kind FROM paper_decisions WHERE decided_on = '2026-10-19'") == []
    _시세(client, "2026-10-20", 9_050, 9_000)
    _하루(client, monkeypatch, "2026-10-20", signals=False)
    assert client.q("SELECT COUNT(*) FROM paper_fills WHERE side = 'sell'") == [(1,)]  # 열린 매도대로 팔렸다


def test_손절을_따르지_않는_d_도_손절_보유의_남은_회차를_거둔다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    c = client
    c.conn.execute("INSERT OR IGNORE INTO paper_accounts (id, name, sell_rule, rule_version, created_at, entry_rule,"
                   " top_n, selection) VALUES ('D', 'D', 'time', 1, '2026-10-14T00:00:00+00:00', 'tranche', 5, 'signal')")
    c.conn.execute("UPDATE paper_accounts SET sell_rule = 'time', charter_json = NULL WHERE id = 'D'")
    c.conn.execute("DELETE FROM paper_accounts WHERE id != 'D'")
    _하루(c, monkeypatch, "2026-10-14")
    _시세(c, "2026-10-15", 10_100, 9_900)  # 1회차 4주 @10,000
    # 10-16 아침 실행이 실패 → 장 끝난 뒤 손 실행(trade_date 10-15) — 회차는 10-19 장으로
    monkeypatch.setattr(cal, "local_today", lambda _m=None: date(2026, 10, 16))
    job.run_step(c, market="KR", trade_date="2026-10-15", late=True, signal_rows=[_신호()], settings=설정,
                 price_series={}, series_unknown={}, allocate=True, after_close=True,
                 now=datetime.fromisoformat("2026-10-16T07:00:00+00:00"))  # fmt: skip
    _시세(c, "2026-10-16", 9_100, 9_000)  # −10% 손절(D 는 따르지 않음)
    _하루(c, monkeypatch, "2026-10-16")
    거둔 = c.q("SELECT COUNT(*) FROM paper_orders WHERE side = 'buy' AND status = 'expired' AND close_note = ?",
               [job.STOP_WITHDRAWN_NOTE])  # fmt: skip
    assert 거둔 == [(3,)]  # 손 실행이 낸 10-19 장 회차 셋
    assert job.STOP_WITHDRAWN_NOTE.startswith(bm.NON_JUDGMENT_EXPIRY)  # KIS 비교에선 판정 밖 만료
    _시세(c, "2026-10-19", 9_000, 8_900)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    assert c.q("SELECT COUNT(*) FROM paper_fills WHERE side = 'buy' AND fill_date = '2026-10-19'") == [(0,)]
