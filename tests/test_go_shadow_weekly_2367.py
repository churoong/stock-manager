"""주문 허가 그림자의 주간 분포 (docs/paper.md 15.13, roadmap #2 "문턱·켜기는 분포를 본 뒤", docs/infra.md 25.2367).

KIS 단계 기록의 `go_shadow`(25.2293)는 실행마다 쌓이지만 세는 곳이 없었다 — 25.2335 독스트링이 "분포를 세는 쪽(아직
없다)" 이라 적은 자리. 주간 요약이 실행 기록에서(새 읽기 없이) 판정·갈래·모름 칸을 센다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from batch.jobs import weekly_summary as ws
from batch.services import broker_mirror as bm


def test_짧은_이름은_갈래와_같은_집합() -> None:
    assert set(bm.GO_SHORT) == set(bm.GO_NO_GO) | set(bm.GO_SELL_ONLY)
    assert set(bm.GO_UNKNOWN_KEYS) == {"new_code_error", "read_budget_unknown"}


def test_분포_줄() -> None:
    assert bm.go_shadow_lines([]) == []
    v = [
        ("broker", "KR", "go", (), False, 2),
        ("broker", "KR", "go", (), True, 1),  # 모름 칸 — 그대로와 따로
        ("broker", "KR", "sell_only", ("new_code",), True, 3),  # 매도만의 모름 칸은 "모름 칸 있음" 에 세지 않는다(25.2368)
        ("broker", "KR", "sell_only", ("diff",), False, 0),  # 매수를 내려 하지 않은 매도만 — 켜도 막을 것이 없다(25.2371)
        ("broker2", "KR", "sell_only", ("diff",), False, 2),
        ("broker", "KR", "no_go", ("halted", "diff"), False, 4),  # 막힘의 매수는 지금도 안 나간다 — 세지 않는다
        ("broker", "US", "sell_only", ("read_budget",), False, None),  # 25.2298 앞 기록 — 매수 수 모름
        ("broker", "US", "error", (), False, 1),  # 그림자 계산이 깨진 실행(25.2368)
    ]
    줄 = bm.go_shadow_lines(v)
    assert 줄 == [
        "주문 허가 그림자(KIS 단계, 켜지 않음, 날로 합치지 않음): 판정 칸 8개 (베끼는 계좌 7 · 도전자 1)",
        "  KR: 그대로 1 · 매도만이었을 3 · 막힘 1 · 모름 칸 있음 1",
        "    켰다면 막았을 매수: 5건 (매도만 3칸 가운데 매수를 내려 한 칸 2)",
        "    매도만 갈래: 잔고 어긋남 2 · 새 코드 첫날 1",  # 많은 것부터 — 넣은 차례(새 코드 먼저)와 다르게
        "    막힘 갈래: 멈춤 1 · 잔고 어긋남 1",
        "  US: 그대로 0 · 매도만이었을 1 · 막힘 0 · 판정 못 함 1",
        "    켰다면 막았을 매수: 0건 (매도만 1칸 가운데 매수를 내려 한 칸 0) · 매수 수 모름 1칸",
        "    매도만 갈래: 읽기 예산 1",
    ]
    # 한 실행에 두 자리면 판정 칸 둘(25.2370 — "실행 2번" 으로 실행 수가 부풀어 읽혔다)
    assert bm.go_shadow_lines([("broker", "KR", "go", (), False, None), ("broker2", "KR", "go", (), False, None)])[0].endswith(
        "판정 칸 2개 (베끼는 계좌 1 · 도전자 1)")
    # 국내 먼저 — 시장 없는 줄("?")은 글자 순으로 앞이지만 뒤로
    assert [x.split(":")[0].strip() for x in bm.go_shadow_lines([("broker", "?", "go", (), False, None),
                                                                ("broker", "KR", "go", (), False, None)])[1:]] == ["KR", "?"]
    # 모름 칸·판정 못 함이 없으면 그 조각은 싣지 않는다
    assert bm.go_shadow_lines([("broker", "US", "go", (), False, None)]) == [
        "주문 허가 그림자(KIS 단계, 켜지 않음, 날로 합치지 않음): 판정 칸 1개 (베끼는 계좌 1 · 도전자 0)",
        "  US: 그대로 1 · 매도만이었을 0 · 막힘 0"]


def _넣기(mem, i: int, 로그: object, 앞시간: int = 1, 시장: str | None = "KR") -> None:  # type: ignore[no-untyped-def]
    t = (datetime.now(UTC) - timedelta(hours=앞시간 + i)).isoformat(timespec="seconds")
    mem.conn.execute(
        "INSERT INTO batch_runs (job_name, market, trade_date, status, started_at, step_log) VALUES (?,?,?,?,?,?)",
        [f"daily_{(시장 or 'x').lower()}", 시장, t[:10], "success", t, json.dumps(로그) if 로그 is not None else None],
    )


def test_실행_기록에서_읽는다() -> None:
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    로그들: list[object] = [
        {"broker": {"go_shadow": {"verdict": "sell_only", "reasons": ["diff"]}, "read_budget_unknown": True,
                    "planned_buys": 3},
         "broker2": {"go_shadow": {"verdict": "go", "reasons": []}, "new_code_error": "ValueError"}},
        {"broker": {"error": "x"}},  # 단계 예외 — 판정 없음
        {"broker": {"go_shadow": "글"}}, {"broker": "글"}, None, [1], {"broker": {"go_shadow": {"verdict": 3}}},
        {"broker": {"go_shadow": {"error": "KeyError"}}},
        {"broker": {"go_shadow": {"verdict": "go", "reasons": None}}},
    ]  # fmt: skip
    for i, 로그 in enumerate(로그들):
        _넣기(mem, i, 로그)
    # 읽는 쪽도 시장을 싣는다(25.2369 — 국내 행만 넣어 시장을 상수로 바꾼 변이가 살아남았다). 시장 없는 줄은 "?"
    _넣기(mem, 20, {"broker": {"go_shadow": {"verdict": "sell_only", "reasons": ["diff"]}, "planned_buys": True}}, 시장="US")
    _넣기(mem, 21, {"broker": {"go_shadow": {"verdict": "go", "reasons": []}}}, 시장=None)
    runs = ws.load_runs(mem, "2026-01-01")  # type: ignore[arg-type]
    모두 = [v for r in runs for v in r.go]
    # planned_buys 가 참거짓(꼴 틀림)이면 모름(None) — bool 은 int 의 하위형이라 따로 거른다(25.2371)
    assert 모두.count(("broker", "US", "sell_only", ("diff",), False, None)) == 1
    assert 모두.count(("broker", "?", "go", (), False, None)) == 1
    모두 = [v for v in 모두 if v[1] == "KR"]
    assert ("broker", "KR", "sell_only", ("diff",), True, 3) in 모두  # 내려 한 매수 건수를 싣는다(25.2371)
    assert ("broker2", "KR", "go", (), True, None) in 모두
    assert ("broker", "KR", "go", (), False, None) in 모두
    # 계산이 깨진 그림자·모르는 판정 글은 "판정 못 함"(25.2368) — 빠지지 않는다
    assert 모두.count(("broker", "KR", "error", (), False, None)) == 2
    assert len(모두) == 5


def test_요약_실행이_분포를_싣는다(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """움직임 — `run(dry_run=True)` 가 찍는 요약에 분포 줄. 분포가 던져도 요약은 나간다"""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    _넣기(mem, 0, {"broker": {"go_shadow": {"verdict": "sell_only", "reasons": ["new_code"]}, "planned_buys": 2}})
    _넣기(mem, 1, {"broker": {"go_shadow": {"verdict": "go", "reasons": []}}})
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (0, 0, []))
    찍힘: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: 찍힘.append(" ".join(str(x) for x in a)))
    assert ws.run(dry_run=True) == 0
    글 = "\n".join(찍힘)
    assert "주문 허가 그림자(KIS 단계, 켜지 않음, 날로 합치지 않음): 판정 칸 2개 (베끼는 계좌 2 · 도전자 0)" in 글
    assert "  KR: 그대로 1 · 매도만이었을 1 · 막힘 0" in 글 and "    매도만 갈래: 새 코드 첫날 1" in 글
    assert "    켰다면 막았을 매수: 2건 (매도만 1칸 가운데 매수를 내려 한 칸 1)" in 글
    monkeypatch.setattr(ws._bm, "go_shadow_lines", lambda v: 1 / 0)
    찍힘.clear()
    assert ws.run(dry_run=True) == 0
    assert "주문 허가 그림자 줄을 만들지 못했습니다 (ZeroDivisionError)" in "\n".join(찍힘)
