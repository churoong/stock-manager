"""주문 허가 판정의 읽기 예산 갈래 (docs/paper.md 15.13, docs/infra.md 25.2334).

DB 월 읽기 누계가 경고선 이상인 날 새 매수를 내면, 보낸 뒤 결과를 적는 읽기·쓰기가 한도에 막혀 응답 받은 주문이
`sending` 으로 남을 수 있다 — 체결을 모르는 채 다음 날. 그림자 기록만 한다(주문을 막지 않는다).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from batch.core import db
from batch.jobs import broker_mirror as jb
from batch.services import broker_mirror as bm


def test_쓴_비율() -> None:
    L = db.TURSO_MONTHLY_READ_LIMIT
    assert bm.read_budget_used_pct(None, L) is None  # 모름
    assert bm.read_budget_used_pct(L, L) == 0.0
    assert bm.read_budget_used_pct(L // 5, L) == 80.0
    assert bm.read_budget_used_pct(int(L * 0.2004), L) < bm.READ_BUDGET_PCT  # 79.96 — 반올림 전 값(25.2335)
    assert bm.read_budget_used_pct(0, L) == 100.0
    assert bm.read_budget_used_pct(10, 0) is None


def test_경고선_이상이면_매도만_모름은_go_쪽_칸이_아니다() -> None:
    assert bm.go_shadow({"read_budget": 85.0}) == {"verdict": "sell_only", "reasons": ["read_budget"]}
    # 모름은 갈래가 아니다 — 따로 센다(15.13 "판정 칸이 없는 실행" 과 같은 까닭으로 칸만 남긴다)
    assert bm.go_shadow({"read_budget_unknown": True}) == {"verdict": "go", "reasons": []}
    assert "read_budget" in bm.GO_SELL_ONLY and "read_budget_unknown" not in bm.GO_SELL_ONLY


def _판(monkeypatch: pytest.MonkeyPatch, 쓴: int | None):  # type: ignore[no-untyped-def]
    from tests.test_kis_vts_1475 import 키
    from tests.test_paper_job_1445 import Client

    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DB_BACKEND", raising=False)  # 셸에 d1 이 있어도 Turso 로(25.2335)
    monkeypatch.setattr(jb, "MIRROR_ACCOUNT", "B")
    c = Client()
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, reason, rule_version, tranche, created_at)"
        " VALUES (101, 'B', 'KR', 1, 'buy', 'limit', 9800, 3, 'KRW', '2026-10-15', '2026-10-16', 'r', 1, 1, 't')"
    )  # fmt: skip
    if 쓴 is not None:
        c.conn.execute(
            "INSERT INTO api_usage (api_name, window_type, window_start, call_count, limit_value, warn_at_pct, state,"
            " last_call_at, updated_at) VALUES ('turso_reads', 'month', ?, ?, ?, 80, 'warn', 't', 't')",
            [datetime.now(UTC).strftime("%Y-%m"), 쓴, db.TURSO_MONTHLY_READ_LIMIT],
        )  # fmt: skip
    return c


@pytest.mark.parametrize(("쓴", "기대"), [
    (int(db.TURSO_MONTHLY_READ_LIMIT * 0.85), 85.0),
    (int(db.TURSO_MONTHLY_READ_LIMIT * 0.80), 80.0),  # 경고선과 같으면 선다(사용량 상태 `warn` 과 같은 잣대)
    (int(db.TURSO_MONTHLY_READ_LIMIT * 0.79), None),
    (int(db.TURSO_MONTHLY_READ_LIMIT * 0.7996), None),  # 반올림하면 80.0 이지만 서지 않는다(25.2335)
    (None, None),  # 이번 달 줄이 없다(달 첫 실행) — 다 남음
])  # fmt: skip
def test_단계가_읽기_예산을_싣고_주문은_그대로_낸다(monkeypatch: pytest.MonkeyPatch, 쓴: int | None, 기대: float | None) -> None:
    from tests.test_kis_vts_1475 import _at, _Sess

    s = _Sess()
    기록 = jb.run(_판(monkeypatch, 쓴), "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert 기록.get("read_budget") == 기대 and "read_budget_unknown" not in 기록
    assert ("read_budget" in 기록["go_shadow"]["reasons"]) is (기대 is not None)
    assert len(s.orders) == 1  # 그림자 — 주문은 그대로 나간다


def test_못_읽으면_모름_칸만_남기고_주문은_간다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _판(monkeypatch, None)
    monkeypatch.setattr(jb.db, "remaining_read_budget_or_none", lambda _c: None)
    s = _Sess()
    기록 = jb.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert 기록["read_budget_unknown"] is True and "read_budget" not in 기록 and len(s.orders) == 1
    assert 기록["go_shadow"]["verdict"] == "go"


def test_D1_이면_보지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """카운터(`turso_reads`)는 Turso 한도다 — D1 으로 돌면 그 날의 한도가 아니다(읽지도 않는다)"""
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _판(monkeypatch, int(db.TURSO_MONTHLY_READ_LIMIT * 0.95))
    monkeypatch.setattr(jb.client_backend, "resolved_backend", lambda: jb.client_backend.D1)
    기록 = jb.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None)
    assert "read_budget" not in 기록 and "read_budget_unknown" not in 기록


def test_막힌_실행은_읽지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """보내기 단계까지 간 실행에서만 — 막힌 실행은 어차피 `no_go`(15.13 `sending_today` 와 같은 자리)"""
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _판(monkeypatch, int(db.TURSO_MONTHLY_READ_LIMIT * 0.95))
    기록 = jb.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, hold_send="보류")
    assert "read_budget" not in 기록 and 기록["go_shadow"]["verdict"] == "no_go"


def test_문서() -> None:
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "docs/paper.md").read_text()
    for 조각 in ("`read_budget`", "25.2334", "`READ_BUDGET_PCT`", "`read_budget_unknown`", "시세 기준일 갈래는 넣지 않는다"):
        assert 조각 in 글, 조각
    assert "read_budget" in inspect.getsource(jb.run)


def test_재는_일이_깨져도_주문은_간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2335 (25.2334 교차검증 [권고]) — 블록이 try 밖이라 값 변환·백엔드 판정이 던지면 보내기 전에 단계가 끝났다"""
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _판(monkeypatch, None)

    def 깨짐(_c: object) -> int:
        raise ValueError("x")

    monkeypatch.setattr(jb.db, "remaining_read_budget_or_none", 깨짐)
    s = _Sess()
    기록 = jb.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert 기록["read_budget_unknown"] is True and 기록["read_budget_error"] == "ValueError" and len(s.orders) == 1


def test_execute_가_던지면_모름(monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 `remaining_read_budget_or_none` 의 except 길 — 가짜 클라이언트가 api_usage 읽기에서만 던진다"""
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _판(monkeypatch, None)
    원래 = c.execute

    def 실행(sql: str, *a: object, **k: object):  # type: ignore[no-untyped-def]
        if "FROM api_usage WHERE api_name = 'turso_reads'" in sql:
            raise RuntimeError("막힘")
        return 원래(sql, *a, **k)

    monkeypatch.setattr(c, "execute", 실행)
    기록 = jb.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None)
    assert 기록["read_budget_unknown"] is True and "read_budget_error" not in 기록


def test_멈춤_날도_싣고_no_go_가_이긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _판(monkeypatch, int(db.TURSO_MONTHLY_READ_LIMIT * 0.9))
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('paper_halt', '{\"on\": true}', 't')")
    기록 = jb.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None)
    assert 기록["read_budget"] == 90.0 and 기록["go_shadow"]["verdict"] == "no_go"
    assert "read_budget" in 기록["go_shadow"]["reasons"] and "halted" in 기록["go_shadow"]["reasons"]
